"""Persist native issued credentials, reopen them in a second process, execute a command."""

from __future__ import annotations

import asyncio
import json
import secrets
import socket
import ssl
import subprocess
import sys
from typing import Any

import aiohttp
from aiohttp import web
from cryptography.fernet import Fernet
from pydantic import SecretBytes

from mattergram import Bot, Dispatcher
from mattergram.filters import Command
from mattergram.http import HttpAdapter, SlashBinding
from mattergram.provisioning import (
    CommandSpec,
    FernetCodec,
    ProvisioningScope,
    ResourceKey,
    SetupClient,
    SQLiteProvisioningStore,
)
from mattergram.provisioning.models import AccessToken, NativeCommand
from mattergram.provisioning.store import ProvisioningStoreError
from mattergram.types import SlashCommand
from tools import ui_stand as ui
from tools._common import ROOT, read_json, write_json

CHILD = """
import asyncio,json,sys
from pydantic import SecretBytes
from mattergram import Bot
from mattergram.provisioning import FernetCodec,ProvisioningScope,ResourceKey,SQLiteProvisioningStore
data=json.loads(sys.stdin.readline())
async def run():
    store=SQLiteProvisioningStore(data['directory'],codec=FernetCodec(SecretBytes(data['master'].encode())))
    async with store.session(ProvisioningScope(**data['scope'])) as session:
        secret=session.secret(ResourceKey(**data['key']))
        assert secret is not None
    async with Bot(token=secret.get_secret_value(),base_url=data['base']) as bot:
        assert bot.user_id==data['bot_id']
    print('stored-native-bot-verified')
asyncio.run(run())
"""


async def verify() -> None:
    ui.configure_profile("12.0.2")
    state, base = read_json(ui.STATE), ui.base_url()
    if state["base_url"] != base:
        raise RuntimeError("Refresh isolated native fixtures")
    marker = "mgstore" + secrets.token_hex(4)
    directory = ROOT / "build" / marker
    master = SecretBytes(Fernet.generate_key())  # Test-only external key, never written into state_dir.
    scope = ProvisioningScope(server=base, application=marker)
    token_key = ResourceKey(kind="runtime-token", name="runtime")
    command_key = ResourceKey(kind="command", team_id=state["team_id"], name=marker)
    store = SQLiteProvisioningStore(directory, codec=FernetCodec(master))
    issued: AccessToken | None = None
    command: NativeCommand | None = None
    bot: Bot | None = None
    runner: web.AppRunner | None = None
    dispatcher = Dispatcher()
    completed = asyncio.Event()
    posts: list[str] = []
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    endpoint = f"https://host.docker.internal:{listener.getsockname()[1]}/mattergram/slash"
    report: dict[str, Any] = {
        "scope": "E6 partial store/journal proof using explicit setup operations, not Bootstrap reconciliation",
        "server_version": state["server_version"],
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        control = ui.Control(base, session)
        _, setup_token = await control.login(state, "admin")
        actor, actor_token = await control.login(state, "actor")
        async with SetupClient(token=setup_token, base_url=base) as setup:
            try:
                async with store.session(scope) as journal:
                    intent = journal.begin(token_key, "create", require_secret=True)
                    issued = await setup.issue_access_token(
                        state["bot_id"], description="Owned store verification"
                    )
                    journal.complete(intent, resource_id=issued.id, secret=issued.token)
                    intent = journal.begin(command_key, "create", require_secret=True)
                    command = await setup.create_command(
                        CommandSpec(marker, description="Persistent store proof"),
                        team_id=state["team_id"],
                        url=endpoint,
                    )
                    assert command.token is not None
                    journal.complete(intent, resource_id=command.id, secret=command.token)
                child = await asyncio.to_thread(
                    subprocess.run,
                    [sys.executable, "-I", "-B", "-W", "error", "-c", CHILD],
                    input=json.dumps(
                        {
                            "directory": str(directory),
                            "master": master.get_secret_value().decode(),
                            "scope": scope.model_dump(),
                            "key": token_key.model_dump(),
                            "base": base,
                            "bot_id": state["bot_id"],
                        }
                    )
                    + "\n",
                    text=True,
                    capture_output=True,
                    timeout=20,
                    check=True,
                )
                assert child.stdout.strip() == "stored-native-bot-verified"
                reopened = SQLiteProvisioningStore(directory, codec=FernetCodec(master))
                async with reopened.session(scope) as journal:
                    runtime_token, slash_token = journal.secret(token_key), journal.secret(command_key)
                    assert runtime_token is not None and slash_token is not None
                    token_binding, command_binding = journal.binding(token_key), journal.binding(command_key)
                    assert token_binding is not None and token_binding.resource_id == issued.id
                    assert command_binding is not None and command_binding.resource_id == command.id
                    assert [i.phase for i in journal.intents()] == ["completed", "completed"]
                    for key in (token_key, command_key):
                        try:
                            journal.begin(key, "create", require_secret=True)
                        except ProvisioningStoreError:
                            pass
                        else:
                            raise AssertionError("Existing binding admitted duplicate create")
                bot = Bot(token=runtime_token.get_secret_value(), base_url=base)

                @dispatcher.slash_command(Command(marker))
                async def handle(event: SlashCommand) -> None:
                    assert event.from_user.id == actor["id"]
                    await event.answer("Persisted command accepted")
                    assert bot is not None
                    posts.append(
                        (
                            await bot.send_message(
                                state["channel_id"], "Persistent runtime verification " + marker
                            )
                        ).post_id
                    )
                    completed.set()

                adapter = HttpAdapter(
                    bot,
                    dispatcher,
                    trusted_peers=["127.0.0.1/32"],
                    slash_bindings=[SlashBinding(state["team_id"], marker, slash_token)],
                )
                runner = web.AppRunner(adapter.create_app(), access_log=None)
                await runner.setup()
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(ui.TLS / "server.pem", ui.TLS / "server.key")
                await web.SockSite(runner, listener, ssl_context=context).start()
                await control.request(
                    "POST",
                    "commands/execute",
                    {"channel_id": state["channel_id"], "command": "/" + marker},
                    token=actor_token,
                )
                await asyncio.wait_for(completed.wait(), 5)
                post, _ = await control.request("GET", "posts/" + posts[0], token=actor_token)
                assert post["user_id"] == state["bot_id"]
                for path in directory.iterdir():
                    data = path.read_bytes()
                    assert (
                        issued.token.get_secret_value().encode() not in data
                        and command.token.get_secret_value().encode() not in data
                        and master.get_secret_value() not in data
                    )
                report.update(
                    result="passed",
                    native_secrets_persisted=2,
                    external_key_not_in_store=True,
                    no_plaintext_secrets_in_files=True,
                    second_process_native_identity=True,
                    reopened_actual_ids=True,
                    repeated_create_blocked=True,
                    completed_intents=2,
                    persisted_slash_binding_callback=True,
                    runtime_post_read_back_by_ordinary_actor=True,
                )
            finally:
                if runner is not None:
                    await runner.cleanup()
                listener.close()
                await dispatcher.close()
                if bot is not None:
                    await bot.close()
                if command is not None:
                    await setup.delete_command(command.id)
                if issued is not None:
                    await setup.revoke_access_token(issued.id)
                report["cleanup"] = (
                    "Only newly created command/PAT removed/revoked; all SDK/listener resources closed. Existing fixture bot/team/channel credentials unchanged. Test store remains encrypted; ephemeral test key was not persisted."
                )
                write_json(ROOT / "build/reports/provisioning-store-stand.json", report)
    print("Real native encrypted store/reopen/second-process/runtime verification passed")


if __name__ == "__main__":
    asyncio.run(verify())
