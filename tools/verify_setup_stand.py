"""Verify explicit SetupClient operations on the owned native fixture, not Bootstrap."""

from __future__ import annotations

import asyncio
import secrets
import socket
import ssl
from typing import Any

import aiohttp
from aiohttp import web

from mattergram import Bot, Dispatcher
from mattergram.exceptions import AuthenticationError, PermissionDeniedError
from mattergram.filters import Command
from mattergram.http import HttpAdapter, SlashBinding
from mattergram.provisioning import CommandSpec, SetupClient
from mattergram.provisioning.models import NativeCommand
from mattergram.types import SlashCommand
from tools import ui_stand as ui
from tools._common import ROOT, read_json, write_json


async def verify() -> None:
    ui.configure_profile("12.0.2")
    state = read_json(ui.STATE)
    base = ui.base_url()
    if state["base_url"] != base:
        raise RuntimeError("Refresh the isolated native UI fixtures")
    marker = "mgsetup" + secrets.token_hex(4)
    private: dict[str, Any] = {"state": "running", "marker": marker}
    private_path = ROOT / "build/setup-stand-private.json"
    report: dict[str, Any] = {
        "scope": "E6-01 partial: explicit SDK setup operations, not ownership/reconciliation or a protected store",
        "server_version": state["server_version"],
    }
    dispatcher = Dispatcher()
    completed = asyncio.Event()
    command: NativeCommand | None = None
    bot: Bot | None = None
    listener: socket.socket | None = None
    runner: web.AppRunner | None = None
    posts: list[str] = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        control = ui.Control(base, session)
        admin, setup_token = await control.login(state, "admin")
        actor, actor_token = await control.login(state, "actor")
        async with SetupClient(token=setup_token, base_url=base) as setup:
            try:
                assert setup.user_id == admin["id"] != actor["id"]
                account = await setup.create_bot(username=marker, display_name="Owned setup fixture")
                private["bot_id"] = account.user_id
                write_json(private_path, private)
                assert account.owner_id == admin["id"]
                assert (await setup.get_bot(account.user_id)).user_id == account.user_id
                assert (await setup.get_user_by_username(marker)).id == account.user_id
                issued = await setup.issue_access_token(
                    account.user_id, description="Owned setup verification"
                )
                private.update(token_id=issued.id, runtime_token=issued.token.get_secret_value())
                write_json(private_path, private)
                assert "token" not in issued.model_dump() and issued.token.get_secret_value() not in repr(
                    issued
                )
                team = await setup.create_team(name=marker, display_name="Owned setup team", type="O")
                private["team_id"] = team.id
                write_json(private_path, private)
                assert (
                    (await setup.get_team(team.id)).id == (await setup.get_team_by_name(marker)).id == team.id
                )
                for user_id in (account.user_id, actor["id"]):
                    await setup.add_team_member(team.id, user_id)
                    assert (await setup.get_team_member(team.id, user_id)).user_id == user_id
                channel = await setup.create_channel(
                    team_id=team.id,
                    name=marker,
                    display_name="Owned private setup channel",
                    type="P",
                    purpose="Only SDK verification",
                    header="Owned fixture",
                )
                private["channel_id"] = channel.id
                write_json(private_path, private)
                assert (await setup.get_channel(channel.id)).type == "P"
                assert (await setup.get_channel_by_name(team.id, marker)).id == channel.id
                for user_id in (account.user_id, actor["id"]):
                    await setup.add_channel_member(channel.id, user_id)
                    assert (await setup.get_channel_member(channel.id, user_id)).user_id == user_id
                bot = Bot(token=issued.token.get_secret_value(), base_url=base)
                identity = await bot.get_me()
                assert (
                    identity.id == account.user_id
                    and "system_admin" not in identity.raw.get("roles", "").split()
                )
                assert setup._session is not bot._session
                listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                listener.bind(("127.0.0.1", 0))
                listener.listen(128)
                listener.setblocking(False)
                endpoint = f"https://host.docker.internal:{listener.getsockname()[1]}/mattergram/slash"
                command = await setup.create_command(
                    CommandSpec(marker, description="Owned initial", hint="[old]"),
                    team_id=team.id,
                    url=endpoint,
                )
                private["command_id"] = command.id
                write_json(private_path, private)
                assert command.token is not None
                updated = await setup.update_command(
                    command.id,
                    CommandSpec(marker, description="Owned updated", hint="[topic]"),
                    team_id=team.id,
                    url=endpoint,
                )
                assert updated.auto_complete_hint == "[topic]" and updated.description == "Owned updated"
                assert (
                    updated.token is not None
                    and updated.token.get_secret_value() == command.token.get_secret_value()
                )
                saved = await setup.get_command(command.id)
                assert saved.id == command.id and saved.url == endpoint
                assert command.id in {c.id for c in await setup.list_commands(team.id)}

                @dispatcher.slash_command(Command(marker))
                async def handle(event: SlashCommand) -> None:
                    assert event.from_user.id == actor["id"] and event.channel_id == channel.id
                    await event.answer("Explicit setup command accepted")
                    assert bot is not None
                    post = await bot.send_message(channel.id, "Setup runtime verification " + marker)
                    posts.append(post.post_id)
                    completed.set()

                adapter = HttpAdapter(
                    bot,
                    dispatcher,
                    trusted_peers=["127.0.0.1/32"],
                    slash_bindings=[SlashBinding(team.id, marker, command.token)],
                )
                runner = web.AppRunner(adapter.create_app(), access_log=None)
                await runner.setup()
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(ui.TLS / "server.pem", ui.TLS / "server.key")
                await web.SockSite(runner, listener, ssl_context=context).start()
                await control.request(
                    "POST",
                    "commands/execute",
                    {"channel_id": channel.id, "command": "/" + marker + " topic"},
                    token=actor_token,
                )
                await asyncio.wait_for(completed.wait(), 5)
                post, _ = await control.request("GET", "posts/" + posts[0], token=actor_token)
                assert post["user_id"] == account.user_id and post["channel_id"] == channel.id
                async with SetupClient(token=actor_token, base_url=base) as limited:
                    # An ordinary identity may enter setup; authorization is per operation.
                    try:
                        await limited.create_bot(username=marker + "denied")
                    except PermissionDeniedError:
                        report["ordinary_actor_create_bot"] = "403 without admin fallback"
                    else:
                        raise AssertionError("Fixture ordinary actor unexpectedly has create_bot permission")
                await setup.revoke_access_token(issued.id)
                private["token_revoked"] = True
                write_json(private_path, private)
                try:
                    await bot.get_me()
                except AuthenticationError:
                    report["revoked_runtime_token"] = "401; no setup fallback"
                else:
                    raise AssertionError("Revoked native token was still accepted")
                report.update(
                    result="passed",
                    bot_created=True,
                    native_pat_status=200,
                    team_created=True,
                    private_channel_created=True,
                    memberships_round_trip=4,
                    command_create_read_update_list=True,
                    command_token_preserved_on_update=True,
                    sdk_callback=True,
                    ordinary_actor_runtime_post_readback=True,
                    setup_runtime_separate=True,
                )
            finally:
                if runner is not None:
                    await runner.cleanup()
                if listener is not None:
                    listener.close()
                await dispatcher.close()
                if bot is not None:
                    await bot.close()
                if command is not None:
                    await setup.delete_command(command.id)
                if private.get("token_id") and not private.get("token_revoked"):
                    await setup.revoke_access_token(private["token_id"])
                if private.get("channel_id"):
                    await control.request("DELETE", "channels/" + private["channel_id"], token=setup_token)
                if private.get("team_id"):
                    await control.request("DELETE", "teams/" + private["team_id"], token=setup_token)
                if private.get("bot_id"):
                    await control.request("POST", "bots/" + private["bot_id"] + "/disable", token=setup_token)
                private["state"] = "closed"
                write_json(private_path, private)
                report["cleanup"] = (
                    "Owned command deleted, token revoked, private channel/team archived, fixture bot disabled; listener/SDK resources closed"
                )
                write_json(ROOT / "build/reports/setup-stand.json", report)
    print("Real explicit setup bot/token/team/channel/membership/command/runtime verification passed")


if __name__ == "__main__":
    asyncio.run(verify())
