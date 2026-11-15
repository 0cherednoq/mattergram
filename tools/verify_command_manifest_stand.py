"""Exercise collected slash metadata on the owned native stand, not Bootstrap yet."""

from __future__ import annotations

import asyncio
import secrets
import socket
import ssl
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
from aiohttp import web
from pydantic import SecretStr

from mattergram import Bot, Dispatcher, Router
from mattergram.filters import Command
from mattergram.http import HttpAdapter, SlashBinding
from mattergram.provisioning import collect_commands
from mattergram.types import SlashCommand
from tools import ui_stand as ui
from tools._common import ROOT, read_json, write_json


async def verify() -> None:
    ui.configure_profile("12.0.2")
    state = read_json(ui.STATE)
    base = ui.base_url()
    if base != state["base_url"]:
        raise RuntimeError("Refresh the owned UI fixtures before verification")
    dispatcher = Dispatcher()
    child = Router(name="manifest-child")
    dispatcher.include_router(child)
    stem = "mgm" + secrets.token_hex(3)
    names = (stem + "help", stem + "request", stem + "ticket")
    events: list[dict[str, Any]] = []
    posts: list[str] = []
    completed = {name: asyncio.Event() for name in names}
    peers: list[str] = []
    owned: list[dict[str, Any]] = []
    private = ROOT / "build/command-manifest-stand-private.json"
    report: dict[str, Any] = {
        "scope": "E6-01 partial: manifest -> native command definitions -> issued-token SDK ingress. Not Bootstrap or durable reconciliation.",
        "events": events,
        "server_version": state["server_version"],
    }
    bot = Bot(token=state["bot_token"], base_url=base)

    async def handle(event: SlashCommand) -> None:
        if event.from_user.id != state["actor_id"] or event.channel_id != state["channel_id"]:
            raise AssertionError("Native actor/channel binding mismatch")
        await event.answer("Collected command accepted")
        post = await bot.send_message(event.channel_id, "Manifest executed " + event.name)
        posts.append(post.post_id)
        events.append({"trigger": event.name, "args": event.text, "ordinary_actor": True})
        completed[event.name].set()

    dispatcher.slash_command.register(handle, Command(names[0]), description="Owned help", hint="[topic]")
    child.slash_command.register(handle, Command(*names[1:]), description="Owned request", hint="[title]")
    manifest = collect_commands(dispatcher)
    if len(manifest.commands) != 3 or collect_commands(dispatcher).fingerprint != manifest.fingerprint:
        raise AssertionError("Manifest was not a deterministic collection of the attached handlers")
    report["manifest_fingerprint"] = manifest.fingerprint
    report["commands"] = [
        {"trigger": c.trigger, "description": c.description, "hint": c.hint} for c in manifest.commands
    ]
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    endpoint = f"https://host.docker.internal:{listener.getsockname()[1]}/mattergram/slash"
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(ui.TLS / "server.pem", ui.TLS / "server.key")
    runner: web.AppRunner | None = None
    adapter: HttpAdapter | None = None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
        control = ui.Control(base, client)
        admin, setup_token = await control.login(state, "admin")
        actor, actor_token = await control.login(state, "actor")
        if admin["id"] == actor["id"] or actor["roles"] != "system_user" or actor.get("is_bot"):
            raise AssertionError("Fixture identity separation failed")
        try:
            identity = await bot.get_me()
            if identity.id != state["bot_id"] or identity.id == admin["id"]:
                raise AssertionError("Runtime bot must use its separately issued token")
            bindings = []
            for spec in manifest.commands:
                payload = spec.to_payload(team_id=state["team_id"], url=endpoint)
                created, _ = await control.request("POST", "commands", payload, token=setup_token)
                owned.append(created)
                # Private development fixture receipt, not a production secret store.
                write_json(private, {"owned_commands": owned, "state": "running"})
                saved, _ = await control.request("GET", "commands/" + created["id"], token=setup_token)
                for key in (
                    "trigger",
                    "description",
                    "auto_complete_desc",
                    "auto_complete_hint",
                    "url",
                    "method",
                    "team_id",
                ):
                    if saved[key] != payload[key]:
                        raise AssertionError("Native command metadata did not round-trip")
                if created["creator_id"] != admin["id"]:
                    raise AssertionError("Native command creator differs from setup identity")
                bindings.append(SlashBinding(state["team_id"], spec.trigger, SecretStr(created["token"])))
            if len({c["token"] for c in owned}) != 3:
                raise AssertionError("Commands must have distinct native-issued tokens")
            adapter = HttpAdapter(bot, dispatcher, trusted_peers=["127.0.0.1/32"], slash_bindings=bindings)

            @web.middleware
            async def observed(
                request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
            ) -> web.StreamResponse:
                peers.append(request.remote or "")
                return await handler(request)

            app = adapter.create_app()
            app.middlewares.append(observed)
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            await web.SockSite(runner, listener, ssl_context=context).start()
            for name in names:
                await control.request(
                    "POST",
                    "commands/execute",
                    {"channel_id": state["channel_id"], "command": "/" + name + " fixture-topic"},
                    token=actor_token,
                )
                await asyncio.wait_for(completed[name].wait(), 5)
            if peers != ["127.0.0.1"] * 3:
                raise AssertionError("Unexpected native callback transport peers")
            for identifier in posts:
                post, _ = await control.request("GET", "posts/" + identifier, token=actor_token)
                if post["user_id"] != identity.id or not post["message"].startswith("Manifest executed "):
                    raise AssertionError("Runtime bot post failed ordinary-actor read-back")
            report.update(
                {
                    "result": "passed",
                    "native_commands": 3,
                    "native_tokens_distinct": True,
                    "sdk_callbacks": 3,
                    "runtime_posts_read_back": 3,
                    "setup_runtime_separate": True,
                    "metadata_round_trip": True,
                    "peer": peers[0],
                }
            )
        finally:
            if runner is not None:
                await runner.cleanup()
            listener.close()
            if adapter is not None:
                await adapter.shutdown()
            await dispatcher.close()
            await bot.close()
            for created in owned:
                await control.request("DELETE", "commands/" + created["id"], token=setup_token)
            write_json(private, {"owned_commands": owned, "state": "deleted"})
            report["cleanup"] = (
                "Created commands deleted; listener, adapter, dispatcher and runtime bot closed"
            )
            write_json(ROOT / "build/reports/command-manifest-stand.json", report)
    print("Real command manifest registration/issued-token/runtime verification passed")


if __name__ == "__main__":
    asyncio.run(verify())
