"""Exercise automatic resource/command reconciliation on the owned native stand."""

from __future__ import annotations

import asyncio
import secrets
import socket
import ssl
from collections import Counter
from typing import Any

import aiohttp
from aiohttp import web
from cryptography.fernet import Fernet
from pydantic import SecretBytes

from mattergram import Bot, Dispatcher
from mattergram.filters import Command
from mattergram.http import HttpAdapter
from mattergram.provisioning import (
    Bootstrap,
    BotSpec,
    ChannelSpec,
    FernetCodec,
    ProvisioningScope,
    ResourceKey,
    SQLiteProvisioningStore,
    TeamSpec,
)
from mattergram.types import SlashCommand
from tools import ui_stand as ui
from tools._common import ROOT, read_json, write_json


async def verify() -> None:
    ui.configure_profile("12.0.2")
    state, base = read_json(ui.STATE), ui.base_url()
    if state["base_url"] != base:
        raise RuntimeError("Refresh isolated stand fixtures")
    marker = "mgboot" + secrets.token_hex(4)
    store = SQLiteProvisioningStore(
        ROOT / "build" / marker, codec=FernetCodec(SecretBytes(Fernet.generate_key()))
    )
    scope = ProvisioningScope(server=base, application=marker)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    callback_base = f"https://host.docker.internal:{listener.getsockname()[1]}"
    runtimes: list[Bot] = []
    dispatchers: list[Dispatcher] = []
    runner: web.AppRunner | None = None
    events: dict[str, asyncio.Event] = {}
    posts: list[str] = []
    report: dict[str, Any] = {
        "scope": (
            "Native Mattermost 12.0.2 automatic Bootstrap repeat/update/multi-team/callback proof; "
            "fault recovery is verified separately against the local Postman stand-in"
        ),
        "server_version": state["server_version"],
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as client:
        control = ui.Control(base, client)
        _, setup_token = await control.login(state, "admin")
        actor, actor_token = await control.login(state, "actor")

        def deployment(updated: bool = False) -> Bootstrap:
            return Bootstrap(
                base_url=base,
                token=setup_token,
                application=marker,
                bot=BotSpec(username=marker),
                store=store,
                callback_base_url=callback_base,
                teams=tuple(
                    TeamSpec(
                        key=key,
                        name=marker + key,
                        display_name="Owned bootstrap " + key,
                        visibility="open",
                        create_if_missing=True,
                    )
                    for key in ("a", "b")
                ),
                channels=tuple(
                    ChannelSpec(
                        key="alerts" + key,
                        name="alerts",
                        team=key,
                        display_name="Owned private alerts",
                        visibility="private",
                        header="updated" if updated else "initial",
                        create_if_missing=True,
                        members=(actor["id"],),
                    )
                    for key in ("a", "b")
                ),
            )

        def routes(updated: bool = False) -> Dispatcher:
            dispatcher = Dispatcher()
            dispatchers.append(dispatcher)

            async def handle(event: SlashCommand, bot: Bot) -> None:
                assert event.from_user.id == actor["id"]
                await event.answer("Bootstrap accepted")
                post = await bot.send_message(event.channel_id, "Bootstrap native " + event.name)
                posts.append(post.post_id)
                events[event.channel_id + event.name].set()

            dispatcher.slash_command.register(
                handle,
                Command(marker + "help"),
                description="Updated" if updated else "Initial",
                hint="[changed]" if updated else "[topic]",
            )
            dispatcher.slash_command.register(handle, Command(marker + "ticket"), description="Ticket")
            return dispatcher

        try:
            first = await deployment().prepare(routes())
            runtimes.append(first)
            first_steps = Counter(step.status for step in first.resources.report.steps)
            assert first.resources.report.runtime_verified and not first.resources.report.callback_verified
            assert len(first.resources.teams) == len(first.resources.channels) == 2
            assert len(first.resources.slash_bindings) == 4
            assert len({b.token.get_secret_value() for b in first.resources.slash_bindings}) == 4
            assert first._token != setup_token
            first_ids = (
                {key: t.id for key, t in first.resources.teams.items()},
                {key: c.id for key, c in first.resources.channels.items()},
            )
            second = await deployment().prepare(routes())
            runtimes.append(second)
            assert all(step.status == "reused" for step in second.resources.report.steps)
            assert first._token == second._token
            assert first_ids == (
                {key: t.id for key, t in second.resources.teams.items()},
                {key: c.id for key, c in second.resources.channels.items()},
            )
            assert first.resources.slash_bindings == second.resources.slash_bindings
            dispatcher = routes(updated=True)
            third = await deployment(updated=True).prepare(dispatcher)
            runtimes.append(third)
            changes = Counter(step.status for step in third.resources.report.steps)
            assert changes["updated"] == 4 and not changes["created"]
            assert (
                first._token == third._token
                and first.resources.slash_bindings == third.resources.slash_bindings
            )
            for channel in third.resources.channels.values():
                for trigger in (marker + "help", marker + "ticket"):
                    events[channel.id + trigger] = asyncio.Event()
            adapter = HttpAdapter(
                third,
                dispatcher,
                trusted_peers=["127.0.0.1/32"],
                slash_bindings=third.resources.slash_bindings,
            )
            runner = web.AppRunner(adapter.create_app(), access_log=None)
            await runner.setup()
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(ui.TLS / "server.pem", ui.TLS / "server.key")
            await web.SockSite(runner, listener, ssl_context=context).start()
            for channel in third.resources.channels.values():
                assert channel.type == "P" and channel.header == "updated"
                for trigger in (marker + "help", marker + "ticket"):
                    await control.request(
                        "POST",
                        "commands/execute",
                        {"channel_id": channel.id, "command": "/" + trigger},
                        token=actor_token,
                    )
                    await asyncio.wait_for(events[channel.id + trigger].wait(), 5)
            for identifier in posts:
                post, _ = await control.request("GET", "posts/" + identifier, token=actor_token)
                assert post["user_id"] == third.user_id
            report.update(
                result="passed",
                first_statuses=dict(first_steps),
                second_statuses=dict(Counter(s.status for s in second.resources.report.steps)),
                changed_statuses=dict(changes),
                actual_teams=2,
                actual_private_channels=2,
                native_commands=4,
                distinct_command_tokens=True,
                bot_token_preserved=True,
                resource_ids_preserved=True,
                command_tokens_preserved=True,
                sdk_callbacks=4,
                ordinary_actor_post_readbacks=4,
                callback_readiness_not_inferred_from_registration=True,
            )
        finally:
            if runner is not None:
                await runner.cleanup()
            listener.close()
            for dispatcher in dispatchers:
                await dispatcher.close()
            for runtime in runtimes:
                await runtime.close()
            async with store.session(scope) as journal:
                teams = {key: journal.binding(ResourceKey(kind="team", name=key)) for key in ("a", "b")}
                for team in teams.values():
                    if team is None:
                        continue
                    for trigger in (marker + "help", marker + "ticket"):
                        binding = journal.binding(
                            ResourceKey(kind="command", team_id=team.resource_id, name=trigger)
                        )
                        if binding is not None and binding.ownership == "owned":
                            await control.request(
                                "DELETE", "commands/" + binding.resource_id, token=setup_token
                            )
                token_binding = journal.binding(ResourceKey(kind="bot-token", name="runtime"))
                if token_binding is not None and token_binding.ownership == "owned":
                    await control.request(
                        "POST",
                        "users/tokens/revoke",
                        {"token_id": token_binding.resource_id},
                        token=setup_token,
                    )
                for key, team in teams.items():
                    if team is not None:
                        channel_binding = journal.binding(
                            ResourceKey(kind="channel", team_id=team.resource_id, name="alerts" + key)
                        )
                        if channel_binding is not None and channel_binding.ownership == "owned":
                            await control.request(
                                "DELETE", "channels/" + channel_binding.resource_id, token=setup_token
                            )
                        if team.ownership == "owned":
                            await control.request("DELETE", "teams/" + team.resource_id, token=setup_token)
                account = journal.binding(ResourceKey(kind="bot", name="runtime"))
                if account is not None and account.ownership == "owned":
                    await control.request(
                        "POST", "bots/" + account.resource_id + "/disable", token=setup_token
                    )
            report["cleanup"] = (
                "All SDK/listener resources closed; only owned fixture commands/PAT removed/revoked, channels/teams archived and bot disabled. Ephemeral test key was not persisted."
            )
            write_json(ROOT / "build/reports/bootstrap-stand.json", report)
    print("Real automatic Bootstrap repeat/update/multi-team/runtime verification passed")


if __name__ == "__main__":
    asyncio.run(verify())
