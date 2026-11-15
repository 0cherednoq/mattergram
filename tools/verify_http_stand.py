"""Exercise native HTTP integrations issued by our owned Mattermost, never a production server.

Administrative requests exist only in this fixture harness. The SDK receives its
own bot token and actual issued slash tokens; it never inherits the admin session.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import secrets
import subprocess
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import aiohttp
from aiohttp import web
from pydantic import SecretStr

from mattergram import Bot, Dispatcher, F
from mattergram.exceptions import ResponseStateError, UnsupportedCapabilityError
from mattergram.filters import CallbackData, Command
from mattergram.fsm.storage.memory import SimpleEventIsolation
from mattergram.http import CallbackSigner, HttpAdapter, HTTPResult, SlashBinding
from mattergram.types import CallbackQuery, SlashCommand
from mattergram.ui.actions import ActionContext, Actions, Attachment, AttachmentField, External
from mattergram.ui.actions import Button as LegacyButton
from mattergram.ui.actions import Select as LegacySelect
from mattergram.ui.blocks import compile_post_ui
from mattergram.ui.forms import Option
from tools._common import ROOT, read_json, write_json
from tools._forms_stand import FormsStandScenario


class RecordingAdapter(HttpAdapter):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.captured: list[dict[str, Any]] = []

    async def handle(self, **kwargs: Any) -> HTTPResult:
        result = await super().handle(**kwargs)
        self.captured.append(
            {
                "peer": kwargs["peer"],
                "path": kwargs["path"],
                "method": kwargs["method"],
                "status": result.status,
            }
        )
        return result


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
    if not port.startswith("127.0.0.1:") or not port.removeprefix("127.0.0.1:").isdigit():
        raise ValueError("Owned Mattermost must expose one loopback endpoint")
    identifier = docker("ps", "-q", "mattermost")
    labels = json.loads(
        subprocess.run(
            ["docker", "inspect", "--format", "{{json .Config.Labels}}", identifier],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    if (
        labels.get("com.docker.compose.project") != "mattergram-native-stand"
        or labels.get("com.docker.compose.service") != "mattermost"
    ):
        raise ValueError("Refusing a server outside the owned fixture project")
    if not (ROOT / "build/native-stand-state.json").exists():
        from tools.verify_native_stand import verify as bootstrap

        await bootstrap()
    state = read_json(ROOT / "build/native-stand-state.json")
    base = "http://" + port
    marker = "mattergram-http-" + secrets.token_hex(8)
    created_commands: list[str] = []
    checks: list[str] = []
    slash_wires: list[dict[str, Any]] = []
    callback_wires: list[dict[str, Any]] = []
    command_record: dict[str, Any] = {}
    actor_socket: aiohttp.ClientWebSocketResponse | None = None
    probe_peer: list[str] = []
    probe = web.Application()

    async def capture_probe(request: web.Request) -> web.Response:
        form = await request.post()
        token = command_record.get("token")
        if not isinstance(token, str) or not secrets.compare_digest(str(form.get("token", "")), token):
            return web.json_response({"error": "unauthorized"}, status=403)
        if request.remote is None:
            raise RuntimeError("Owned Mattermost peer was not available")
        probe_peer.append(request.remote)
        return web.json_response({"response_type": "ephemeral", "text": ""})

    probe.router.add_post("/probe", capture_probe)
    probe_runner = web.AppRunner(probe, access_log=None)
    await probe_runner.setup()
    await web.TCPSite(probe_runner, "0.0.0.0", 0).start()
    probe_port = probe_runner.addresses[0][1]
    task: asyncio.Task[None] | None = None
    dispatcher = Dispatcher()
    bot = Bot(token=state["bot_token"], base_url=base)
    http: RecordingAdapter | None = None
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
                headers={"Authorization": "Bearer " + credential} if credential is not None else {},
            ) as response:
                if response.status not in expected:
                    raise RuntimeError(f"Owned HTTP fixture operation {method} returned {response.status}")
                data = await response.json() if response.content_type == "application/json" else {}
                if not isinstance(data, dict):
                    raise RuntimeError("Owned fixture returned an unexpected response shape")
                return data

        async def execute(trigger: str, text: str, *, expected: tuple[int, ...] = (200,)) -> dict[str, Any]:
            return await request(
                "POST",
                "commands/execute",
                {"channel_id": state["channel_id"], "command": "/" + trigger + (" " + text if text else "")},
                expected=expected,
            )

        async def find_post(text: str) -> dict[str, Any]:
            async with asyncio.timeout(8):
                while True:
                    page = await request("GET", f"channels/{state['channel_id']}/posts?per_page=100")
                    found = next(
                        (post for post in page.get("posts", {}).values() if post.get("message") == text), None
                    )
                    if found:
                        return dict(found)
                    await asyncio.sleep(0.025)

        try:
            for attempt in range(40):
                try:
                    async with control.get(base + "/api/v4/system/ping") as ping:
                        if ping.status == 200:
                            break
                except (aiohttp.ClientError, TimeoutError):
                    pass
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("Owned Mattermost did not become ready")
            async with control.post(
                base + "/api/v4/users/login",
                json={"login_id": state["admin_username"], "password": state["admin_password"]},
            ) as login:
                admin = await login.json()
                admin_token = login.headers.get("Token")
                if (
                    login.status != 200
                    or not admin_token
                    or "system_admin" not in admin.get("roles", "").split()
                ):
                    raise RuntimeError("Owned fixture administrative identity failed")
            trigger = "mge4" + secrets.token_hex(4)
            get_trigger = "mgget" + secrets.token_hex(4)
            command_record.update(
                await request(
                    "POST",
                    "commands",
                    {
                        "team_id": state["team_id"],
                        "trigger": trigger,
                        "method": "P",
                        "url": f"http://host.docker.internal:{probe_port}/probe",
                        "display_name": "Owned HTTP fixture",
                        "auto_complete": True,
                    },
                )
            )
            created_commands.append(command_record["id"])
            await execute(trigger, "probe")
            if not probe_peer:
                raise RuntimeError("Owned Mattermost never reached the explicit local probe")
            peer = ipaddress.ip_address(probe_peer[-1])
            await probe_runner.cleanup()
            await bot.get_me()
            if bot.user_id != state["bot_id"]:
                raise RuntimeError("Runtime bot identity differs from the issued fixture")
            signer = CallbackSigner(
                instance_id=bot.instance_id,
                bot_id=state["bot_id"],
                keys={"fixture": secrets.token_bytes(32)},
                active_key_id="fixture",
            )
            forms_scenario = FormsStandScenario(bot, dispatcher)
            http = RecordingAdapter(
                bot,
                dispatcher,
                trusted_peers=[str(peer) + ("/32" if peer.version == 4 else "/128")],
                slash_bindings=[SlashBinding(state["team_id"], trigger, SecretStr(command_record["token"]))],
                callback_signer=signer,
                dialogs=forms_scenario.manager,
                own_dialogs=True,
                request_timeout=1,
                shutdown_timeout=3,
            )
            ready, deferred_done, deferred_release, timed_out = (
                asyncio.Event(),
                asyncio.Event(),
                asyncio.Event(),
                asyncio.Event(),
            )
            six_blocked: list[bool] = []
            action_calls: list[str] = []

            class Action(CallbackData, prefix="http"):
                verb: str

            @dispatcher.raw_event(F.event_type == "hello")
            async def hello(event: Any) -> None:
                ready.set()

            @dispatcher.slash_command(Command(trigger, get_trigger))
            async def slash(command: SlashCommand) -> None:
                slash_wires.append(dict(command.unsafe_raw()))
                if command.text == "sync":
                    await command.answer(marker + " public", ephemeral=False)
                elif command.text == "defer":
                    await command.defer(marker + " working")
                    await deferred_release.wait()
                    await command.followup(marker + " deferred", ephemeral=False)
                    deferred_done.set()
                elif command.text == "five":
                    await command.defer()
                    for index in range(5):
                        await command.followup(marker + " batch " + str(index), ephemeral=False)
                    try:
                        await command.followup("six must not be sent")
                    except ResponseStateError:
                        six_blocked.append(True)
                    deferred_done.set()
                elif command.text == "timeout":
                    try:
                        await asyncio.Event().wait()
                    finally:
                        timed_out.set()
                elif command.text.startswith("form:"):
                    await forms_scenario.open_from_command(command)

            @dispatcher.callback_query(Action.filter())
            async def action(query: CallbackQuery, callback_data: Action) -> None:
                callback_wires.append(dict(query.unsafe_raw()))
                action_calls.append(callback_data.verb)
                if callback_data.verb == "update":
                    assert query.message.post_id == query.post_id and not hasattr(query.message, "text")
                    await query.update_message(marker + " updated")
                elif callback_data.verb == "select":
                    assert query.data == "http:select" and query.selected_option == "yes"
                    await query.update_message(marker + " selected yes")
                elif callback_data.verb == "navigate":
                    await query.navigate("https://example.test")
                else:
                    await query.answer()

            unsupported: list[bool] = []

            @dispatcher.error(lambda error: isinstance(error.exception, UnsupportedCapabilityError))
            async def capability(error: Any) -> None:
                unsupported.append(True)
                await error.event.answer(marker + " unavailable")

            task = asyncio.create_task(dispatcher.start(bot, http=http, host="0.0.0.0", port=0))
            await asyncio.wait_for(ready.wait(), 10)
            http_port = http.listening_addresses[0][1]
            endpoint = f"http://host.docker.internal:{http_port}/mattergram"
            forms_scenario.manager.set_callback_url(endpoint + "/dialog")
            await request(
                "PUT", "commands/" + command_record["id"], {**command_record, "url": endpoint + "/slash"}
            )
            await execute(trigger, "sync")
            public = await find_post(marker + " public")
            get_record = await request(
                "POST",
                "commands",
                {
                    "team_id": state["team_id"],
                    "trigger": get_trigger,
                    "method": "G",
                    "url": endpoint + "/slash",
                },
            )
            created_commands.append(get_record["id"])
            http.add_slash_binding(
                SlashBinding(state["team_id"], get_trigger, SecretStr(get_record["token"]), method="GET")
            )
            await execute(get_trigger, "GET & escaped + value")
            assert slash_wires[-1]["command"] == "/" + get_trigger
            assert slash_wires[-1]["text"] == "GET & escaped + value"
            checks.append(
                "real GET slash preserves percent-escaped separators and uses its own issued command token"
            )
            await execute(trigger, "")
            checks.extend(
                [
                    "real issued slash token/body/header through SDK HTTP to public response with REST read-back",
                    "empty handler returns accepted explicit ephemeral JSON ACK",
                ]
            )
            await execute(trigger, "defer")
            deferred_release.set()
            await asyncio.wait_for(deferred_done.wait(), 10)
            await find_post(marker + " deferred")
            checks.append("explicit deferred ACK and response_url followup accepted by real server")
            deferred_done.clear()
            await execute(trigger, "five")
            await asyncio.wait_for(deferred_done.wait(), 10)
            assert six_blocked == [True]
            for index in range(5):
                await find_post(marker + " batch " + str(index))
            checks.append("five actual response_url posts read back; sixth SDK attempt blocked locally")
            await execute(trigger, "timeout", expected=(400, 500))
            await asyncio.wait_for(timed_out.wait(), 5)
            checks.append(
                "slow slash handler rejected by SDK deadline, cancelled and surfaced as provider error"
            )
            created = await bot.send_message(
                state["channel_id"],
                marker + " actions",
                attachments=[
                    Attachment(
                        fallback="Owned UI fixture",
                        text="Owned integration fixture",
                        fields=(AttachmentField(title="Compiler", value="SDK", short=True),),
                    )
                ],
            )
            post = dict(created.raw)

            def actions() -> list[dict[str, Any]]:
                result: list[LegacyButton | LegacySelect] = []
                for verb in ("ack", "update", "select", "navigate"):
                    definition = External(url=endpoint + "/callback", data=Action(verb=verb).pack())
                    item: LegacyButton | LegacySelect
                    if verb == "select":
                        item = LegacySelect(
                            id=verb,
                            name=verb,
                            action=definition,
                            options=tuple(Option(text=value, value=value) for value in ("yes", "no")),
                        )
                    else:
                        item = LegacyButton(id=verb, name=verb, action=definition)
                    result.append(item)
                compiled = compile_post_ui(
                    actions=Actions(items=tuple(result)),
                    context=ActionContext(signer, state["channel_id"], post["id"], state["admin_id"]),
                )
                rendered: list[dict[str, Any]] = compiled["attachments"][0]["actions"]
                return rendered

            props: dict[str, Any] = {
                "attachments": [{"text": "Owned integration fixture", "actions": actions()}]
            }
            await request("PUT", "posts/" + post["id"] + "/patch", {"props": props}, token=state["bot_token"])
            checks.append(
                "SDK Bot.send_message rich attachment accepted; shared UI compiler signed real button/menu callbacks"
            )
            actor_socket = await control.ws_connect(base + "/api/v4/websocket", heartbeat=10)
            await actor_socket.send_json(
                {"seq": 1, "action": "authentication_challenge", "data": {"token": admin_token}}
            )
            for _ in range(32):
                acknowledgement = await actor_socket.receive_json(timeout=8)
                if acknowledgement.get("seq_reply") == 1:
                    assert acknowledgement.get("status") == "OK"
                    break
            else:
                raise RuntimeError("Owned actor WebSocket did not authenticate")
            for verb in ("ack", "update", "select", "select", "navigate"):
                await request(
                    "POST",
                    f"posts/{post['id']}/actions/{verb}",
                    {"selected_option": "yes"} if verb == "select" else {},
                )
            read_back = await request("GET", "posts/" + post["id"])
            assert read_back["message"] == marker + " selected yes"
            assert action_calls == ["ack", "update", "select", "select", "navigate"] and unsupported == [True]
            async with asyncio.timeout(8):
                while True:
                    observed = await actor_socket.receive_json()
                    wire_post = observed.get("data", {}).get("post")
                    if isinstance(wire_post, str):
                        wire_post = json.loads(wire_post)
                    if isinstance(wire_post, dict) and wire_post.get("message") == marker + " unavailable":
                        assert observed["event"] == "ephemeral_message"
                        assert observed["broadcast"]["user_id"] == state["admin_id"]
                        break
            checks.append(
                "native callback ephemeral response observed on the actual actor WebSocket with actor-targeted broadcast"
            )
            checks.extend(
                [
                    "real post-action signed context, native post reference, ACK and update read back",
                    "real menu selected_option separated from typed data; repeated intentional presses both dispatched",
                    "11.1 unsupported navigation raises SDK capability error and app explicitly answers ephemerally",
                ]
            )
            bad_props = deepcopy(props)
            bad_props["attachments"][0]["actions"][0]["integration"]["context"]["mattergram"]["payload"][
                "data"
            ] = "tampered"
            await request(
                "PUT", "posts/" + post["id"] + "/patch", {"props": bad_props}, token=state["bot_token"]
            )
            await request("POST", f"posts/{post['id']}/actions/ack", {}, expected=(400, 500))
            assert len(action_calls) == 5 and http.rejected_requests >= 1
            checks.append("tampered context actually forwarded by trusted Mattermost rejected before handler")
            forms_report = await forms_scenario.verify(
                request=request, execute=execute, trigger=trigger, actor_socket=actor_socket, state=state
            )
            write_json(
                ROOT / "build/http-private-captures.json", {"slash": slash_wires, "callback": callback_wires}
            )
            assert isinstance(dispatcher.events_isolation, SimpleEventIsolation)
            assert not dispatcher.events_isolation._locks
            await dispatcher.stop()
            await asyncio.wait_for(task, 10)
            assert bot.is_closed and dispatcher._closed and http.listening_addresses == ()
            checks.append(
                "combined HTTP/WS shutdown closed listener/client, drained tasks and released state locks"
            )
            return {
                "result": "passed",
                "checked_at": datetime.now(UTC).isoformat(),
                "scope": "Real owned Mattermost 11.1.0 -> SDK HTTP/WS, actual issued slash token and post actions. API/transport proof, not client UI rendering.",
                "checks": checks,
                "peer": str(peer),
                "callback_requests": len(callback_wires),
                "slash_requests": len(slash_wires),
                "public_post_id": public["id"],
                "action_post_id": post["id"],
                "http_statuses": [item["status"] for item in http.captured],
                "credentials": "not included; private capture only in ignored build/",
                "managed_forms": forms_report,
            }
        finally:
            if actor_socket is not None:
                await actor_socket.close()
            if task is not None:
                if not task.done():
                    await dispatcher.stop()
                await asyncio.gather(task, return_exceptions=True)
            else:
                await dispatcher.close()
                await bot.close()
            await probe_runner.cleanup()
            if admin_token:
                for command_id in created_commands:
                    await request("DELETE", "commands/" + command_id, expected=(200, 204))


def main() -> None:
    report = asyncio.run(verify())
    write_json(ROOT / "build/reports/http-stand.json", report)
    print("Real Mattermost HTTP integration profile passed; evidence: build/reports/http-stand.json")


if __name__ == "__main__":
    main()
