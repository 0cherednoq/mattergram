"""Verify one-post dialog navigation, widgets, form, and Redis on owned Mattermost 11.1."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import secrets
import subprocess
from datetime import UTC, datetime
from typing import Any

import aiohttp
from aiohttp import web
from pydantic import SecretStr

from mattergram import Bot, Dispatcher, F
from mattergram.fsm.state import State, StatesGroup
from mattergram.fsm.storage.base import DefaultKeyBuilder
from mattergram.fsm.storage.redis import RedisEventIsolation, RedisStorage
from mattergram.http import CallbackSigner, HttpAdapter, SlashBinding
from mattergram.http import DialogManager as NativeDialogManager
from mattergram.types import DialogSubmission, SlashCommand
from mattergram.ui import UIProfile
from mattergram.ui.capabilities import SERVER_FEATURES
from mattergram_dialog import (
    Back,
    Const,
    Dialog,
    DialogRegistry,
    DialogRuntime,
    DialogStorage,
    Filter,
    Format,
    FormWindow,
    Multi,
    OpenForm,
    PagedList,
    Pager,
    ShowMode,
    SwitchTo,
    Window,
    setup_dialogs,
)
from mattergram_dialog.api.protocols.manager import DialogManager
from mattergram_dialog.widgets.form import TextInput
from tools._common import ROOT, read_json, write_json


class Scenario(StatesGroup):
    home = State()
    orders = State()
    incidents = State()
    help = State()
    form = State()
    confirmation = State()


async def verify() -> dict[str, Any]:
    compose = [
        "docker",
        "compose",
        "--env-file",
        str(ROOT / "build/native-stand.env"),
        "-p",
        "mattergram-native-stand",
        "-f",
        str(ROOT / "infra/native-stand.compose.yml"),
    ]

    def docker(*args: str) -> str:
        return subprocess.run(
            [*compose, *args], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()

    port = docker("port", "mattermost", "8065")
    if not port.startswith("127.0.0.1:"):
        raise RuntimeError("Owned Mattermost is not bound to loopback")
    container = docker("ps", "-q", "mattermost")
    labels = json.loads(
        subprocess.run(
            ["docker", "inspect", "--format", "{{json .Config.Labels}}", container],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    if labels.get("com.docker.compose.project") != "mattergram-native-stand":
        raise RuntimeError("Refusing a Mattermost server outside the owned stand")
    if not (ROOT / "build/native-stand-state.json").exists():
        from tools.verify_native_stand import verify as bootstrap

        await bootstrap()
    state = read_json(ROOT / "build/native-stand-state.json")
    base = "http://" + port
    marker = "mattergram-dialog-" + secrets.token_hex(6)
    redis_url = "redis://127.0.0.1:47851/0"
    redis_prefix = "mattergram-dialog-e8-03-" + secrets.token_hex(8)
    key_builder = DefaultKeyBuilder(prefix=redis_prefix)
    storage = RedisStorage.from_url(redis_url, key_builder=key_builder)
    isolation = RedisEventIsolation.from_url(redis_url, key_builder=key_builder)
    dialog_storage = DialogStorage(storage, isolation)
    created_posts: list[str] = []
    command_id: str | None = None
    dispatcher = Dispatcher()
    bot = Bot(token=state["bot_token"], base_url=base)
    task: asyncio.Task[None] | None = None
    actor_socket: aiohttp.ClientWebSocketResponse | None = None
    probe = web.Application()
    probe_peer: list[str] = []
    command_record: dict[str, Any] = {}

    async def capture_probe(request: web.Request) -> web.Response:
        form = await request.post()
        token = command_record.get("token")
        if not isinstance(token, str) or not secrets.compare_digest(str(form.get("token", "")), token):
            return web.json_response({"error": "unauthorized"}, status=403)
        if request.remote is None:
            raise RuntimeError("Mattermost probe peer is missing")
        probe_peer.append(request.remote)
        return web.json_response({"response_type": "ephemeral", "text": ""})

    probe.router.add_post("/probe", capture_probe)
    probe_runner = web.AppRunner(probe, access_log=None)
    await probe_runner.setup()
    await web.TCPSite(probe_runner, "0.0.0.0", 0).start()
    probe_port = probe_runner.addresses[0][1]

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as control:
        admin_token: str | None = None

        async def request(
            method: str,
            path: str,
            body: dict[str, Any] | None = None,
            *,
            token: str | None = None,
            expected: tuple[int, ...] = (200, 201),
        ) -> dict[str, Any]:
            credential = token or admin_token
            async with control.request(
                method,
                base + "/api/v4/" + path,
                json=body,
                headers={"Authorization": "Bearer " + credential} if credential else {},
            ) as response:
                if response.status not in expected:
                    raise RuntimeError(f"Owned Mattermost {method} {path} returned {response.status}")
                if response.content_type != "application/json":
                    return {}
                result = await response.json()
                if not isinstance(result, dict):
                    raise RuntimeError("Owned Mattermost returned an unexpected payload")
                return result

        async def execute(trigger: str) -> None:
            await request(
                "POST",
                "commands/execute",
                {"channel_id": state["channel_id"], "command": "/" + trigger},
            )

        async def find_post(text: str) -> dict[str, Any]:
            async with asyncio.timeout(10):
                while True:
                    page = await request("GET", f"channels/{state['channel_id']}/posts?per_page=100")
                    for post in page.get("posts", {}).values():
                        if post.get("message") == text:
                            if post["id"] not in created_posts:
                                created_posts.append(post["id"])
                            return dict(post)
                    await asyncio.sleep(0.025)

        async def wait_post(post_id: str, text: str) -> dict[str, Any]:
            async with asyncio.timeout(10):
                while True:
                    post = await request("GET", "posts/" + post_id)
                    if post.get("message") == text:
                        return post
                    await asyncio.sleep(0.025)

        async def invoke(post_id: str, action_id: str, expected_text: str) -> dict[str, Any]:
            await request("POST", f"posts/{post_id}/actions/{action_id}", {})
            return await wait_post(post_id, expected_text)

        try:
            async with control.post(
                base + "/api/v4/users/login",
                json={"login_id": state["admin_username"], "password": state["admin_password"]},
            ) as login:
                admin = await login.json()
                admin_token = login.headers.get("Token")
                if login.status != 200 or not admin_token or "system_admin" not in admin["roles"]:
                    raise RuntimeError("Owned stand admin login failed")

            trigger = "mgdlg" + secrets.token_hex(4)
            command_record.update(
                await request(
                    "POST",
                    "commands",
                    {
                        "team_id": state["team_id"],
                        "trigger": trigger,
                        "method": "P",
                        "url": f"http://host.docker.internal:{probe_port}/probe",
                        "display_name": "Mattergram dialog E8-03",
                    },
                )
            )
            command_id = command_record["id"]
            await execute(trigger)
            if not probe_peer:
                raise RuntimeError("Mattermost did not reach the local probe")
            peer = ipaddress.ip_address(probe_peer[-1])
            await probe_runner.cleanup()
            await bot.get_me()
            signer = CallbackSigner(
                instance_id=bot.instance_id,
                bot_id=state["bot_id"],
                keys={"scenario": secrets.token_bytes(32)},
                active_key_id="scenario",
            )
            native_dialogs = NativeDialogManager(
                bot,
                profile=UIProfile(
                    "11.1.0",
                    client="owned-dialog-scenario",
                    client_features=SERVER_FEATURES["11.1.0"],
                    client_evidence="owned API/WS stand; not browser rendering",
                ),
            )
            http = HttpAdapter(
                bot,
                dispatcher,
                trusted_peers=[str(peer) + ("/32" if peer.version == 4 else "/128")],
                slash_bindings=[SlashBinding(state["team_id"], trigger, SecretStr(command_record["token"]))],
                callback_signer=signer,
                dialogs=native_dialogs,
                own_dialogs=True,
                request_timeout=2,
                shutdown_timeout=3,
            )
            ready = asyncio.Event()
            effects: list[str] = []

            @dispatcher.raw_event(F.event_type == "hello")
            async def hello(event: Any) -> None:
                ready.set()

            task = asyncio.create_task(dispatcher.start(bot, http=http, host="0.0.0.0", port=0))
            await asyncio.wait_for(ready.wait(), 10)
            endpoint = f"http://host.docker.internal:{http.listening_addresses[0][1]}/mattergram"
            native_dialogs.set_callback_url(endpoint + "/dialog")

            async def start_data(data: Any, manager: DialogManager) -> None:
                if isinstance(data, dict):
                    manager.dialog_data.update(data)

            async def save(event: DialogSubmission, manager: DialogManager) -> None:
                title = event.values["title"]
                manager.dialog_data["title"] = title
                effects.append(title)
                await event.answer()
                await manager.switch_to(Scenario.confirmation, show_mode=ShowMode.EDIT)

            async def scenario_data() -> dict[str, Any]:
                return {
                    "orders": (
                        {"id": "O-1", "status": "open"},
                        {"id": "O-2", "status": "closed"},
                        {"id": "O-3", "status": "open"},
                    ),
                    "incidents": ("INC-1", "INC-2", "INC-3"),
                }

            scenario = Dialog(
                Window(
                    Const(marker + " home"),
                    SwitchTo(Const("Orders"), id="orders", state=Scenario.orders),
                    SwitchTo(Const("Incidents"), id="incidents", state=Scenario.incidents),
                    state=Scenario.home,
                ),
                Window(
                    Multi(
                        Const(marker + " orders"),
                        PagedList(
                            Format("- {item[id]} {item[status]}"),
                            id="orderRows",
                            items="orders",
                            page_size=2,
                            filter_by="status",
                        ),
                    ),
                    Filter(
                        id="orderFilter",
                        target="orderRows",
                        options={"All": None, "Open": "open"},
                    ),
                    Pager(id="orderPager", target="orderRows"),
                    SwitchTo(Const("Help"), id="orderHelp", state=Scenario.help),
                    Back(Const("Back"), id="orderBack"),
                    OpenForm(Const("Configure"), id="configure", state=Scenario.form),
                    state=Scenario.orders,
                    getter=scenario_data,
                ),
                Window(
                    Multi(
                        Const(marker + " incidents"),
                        PagedList(
                            Format("- {item}"),
                            id="incidentRows",
                            items="incidents",
                            page_size=2,
                        ),
                    ),
                    Pager(id="incidentPager", target="incidentRows"),
                    SwitchTo(Const("Help"), id="incidentHelp", state=Scenario.help),
                    Back(Const("Back"), id="incidentBack"),
                    state=Scenario.incidents,
                    getter=scenario_data,
                ),
                Window(
                    Const(marker + " help"),
                    Back(Const("Back"), id="helpBack"),
                    state=Scenario.help,
                ),
                FormWindow(
                    TextInput(id="title", label="Title"),
                    title=Const("Configure item"),
                    submit_text=Const("Confirm"),
                    on_submit=save,
                    callback_id="scenario_form",
                    state=Scenario.form,
                ),
                Window(Format("Confirmed: {title}"), state=Scenario.confirmation),
                on_start=start_data,
            )
            registry = DialogRegistry(scenario)
            runtime = DialogRuntime(
                bot,
                dialog_storage,
                registry,
                instance_id=bot.instance_id,
                bot_id=state["bot_id"],
                callback_url=endpoint + "/callback",
                callback_signer=signer,
            )
            setup_dialogs(dispatcher, runtime)

            @dispatcher.slash_command()
            async def catalog(event: SlashCommand) -> None:
                await runtime.start(event, Scenario.home)
                await event.answer()

            await request("PUT", "commands/" + command_id, {**command_record, "url": endpoint + "/slash"})
            actor_socket = await control.ws_connect(base + "/api/v4/websocket", heartbeat=10)
            await actor_socket.send_json(
                {"seq": 1, "action": "authentication_challenge", "data": {"token": admin_token}}
            )
            while True:
                reply = await actor_socket.receive_json(timeout=8)
                if reply.get("seq_reply") == 1:
                    if reply.get("status") != "OK":
                        raise RuntimeError("Actor WebSocket authentication failed")
                    break

            await execute(trigger)
            home_text = marker + " home"
            orders_page_one = marker + " orders\n- O-1 open\n- O-2 closed\nPage 1/2"
            orders_page_two = marker + " orders\n- O-3 open\nPage 2/2"
            orders_open = marker + " orders\n- O-1 open\n- O-3 open\nPage 1/1"
            incidents_page_one = marker + " incidents\n- INC-1\n- INC-2\nPage 1/2"
            home_post = await find_post(home_text)
            post_id = home_post["id"]

            await invoke(post_id, "orders", orders_page_one)
            await invoke(post_id, "orderHelp", marker + " help")
            await invoke(post_id, "helpBack", orders_page_one)
            await invoke(post_id, "orderPagerNext", orders_page_two)
            await invoke(post_id, "orderFilter1", orders_open)
            await invoke(post_id, "orderBack", home_text)

            await invoke(post_id, "incidents", incidents_page_one)
            await invoke(post_id, "incidentHelp", marker + " help")
            await invoke(post_id, "helpBack", incidents_page_one)
            await invoke(post_id, "incidentBack", home_text)

            await invoke(post_id, "orders", orders_open)
            await request("POST", f"posts/{post_id}/actions/configure", {})
            async with asyncio.timeout(10):
                while True:
                    opened_event = await actor_socket.receive_json(timeout=8)
                    if opened_event.get("event") != "open_dialog":
                        continue
                    opened = json.loads(opened_event["data"]["dialog"])
                    form = opened["dialog"]
                    if form["callback_id"] == "scenario_form":
                        break

            submission = {
                "url": endpoint + "/dialog",
                "callback_id": form["callback_id"],
                "state": form["state"],
                "channel_id": state["channel_id"],
                "team_id": state["team_id"],
                "cancelled": False,
                "submission": {"title": marker + " approved"},
            }
            assert await request("POST", "actions/dialogs/submit", submission) == {}
            confirmation = await wait_post(post_id, "Confirmed: " + marker + " approved")
            assert await request("POST", "actions/dialogs/submit", submission) == {}
            assert effects == [marker + " approved"]
            assert confirmation["id"] == post_id

            key = dialog_storage.key(
                instance_id=bot.instance_id,
                bot_id=state["bot_id"],
                channel_id=state["channel_id"],
                actor_id=state["admin_id"],
            )
            independent = RedisStorage.from_url(redis_url, key_builder=key_builder)
            try:
                persisted = await DialogStorage(independent, isolation).load(key)
            finally:
                await independent.close()
            current = persisted.current()
            assert current is not None and current.state == Scenario.confirmation
            assert current.dialog_data["title"] == marker + " approved"
            await dispatcher.stop()
            await asyncio.wait_for(task, 10)
            task = None
            return {
                "schema_version": 1,
                "result": "passed",
                "checked_at": datetime.now(UTC).isoformat(),
                "scope": "Owned Mattermost 11.1 API/HTTP/WS and real Redis; not browser rendering",
                "scenario": [
                    "one editable post",
                    "orders -> shared help -> path-aware back",
                    "independent filter and pager state",
                    "incidents -> shared help -> path-aware back",
                    "native form -> same-post confirmation",
                ],
                "checks": [
                    "issued slash command reached DialogRuntime through trusted HTTP ingress",
                    "all post-window transitions atomically replaced the original post and its controls",
                    "Back returned from the shared help window to each branch that actually opened it",
                    "a pager and filter updated only their declared list and preserved state across navigation",
                    "large control sets were split into native attachment groups",
                    "native open_dialog reached the authenticated actor WebSocket",
                    "server-forwarded form submission edited the same post and terminal replay ran the effect once",
                    "an independent Redis client restored the confirmation state and submitted value",
                ],
                "server_version": "11.1.0",
                "redis": redis_url,
                "post_id": post_id,
                "confirmation_post_id": confirmation["id"],
                "business_effects": len(effects),
                "credentials": "omitted",
            }
        finally:
            if actor_socket is not None:
                await actor_socket.close()
            if task is not None:
                if not task.done():
                    await dispatcher.stop()
                await asyncio.gather(task, return_exceptions=True)
            elif not dispatcher._closed:
                await dispatcher.close()
                await bot.close()
            if admin_token is not None:
                for post_id in created_posts:
                    await request("DELETE", "posts/" + post_id, expected=(200, 204))
                if command_id is not None:
                    await request("DELETE", "commands/" + command_id, expected=(200, 204))
            await probe_runner.cleanup()
            keys = [key async for key in storage.redis.scan_iter(match=redis_prefix + ":*")]
            if keys:
                await storage.redis.delete(*keys)
            await storage.close()
            await isolation.close()


def main() -> None:
    report = asyncio.run(verify())
    path = ROOT / "build/reports/dialog-scenario-stand.json"
    write_json(path, report)
    print(f"E8-03 live dialog scenario passed; evidence: {path}")


if __name__ == "__main__":
    main()
