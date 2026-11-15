"""Provision once with a setup token, then run from persisted runtime secrets."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from pydantic import SecretBytes

from mattergram import Dispatcher
from mattergram.filters import Command, CommandObject
from mattergram.http import HttpAdapter
from mattergram.provisioning import (
    Bootstrap,
    BotSpec,
    ChannelSpec,
    FernetCodec,
    MemberSpec,
    SQLiteProvisioningStore,
    TeamSpec,
)
from mattergram.types import SlashCommand


async def main() -> None:
    dispatcher = Dispatcher()

    @dispatcher.slash_command(
        Command("request"),
        description="Create a request",
        hint="[topic]",
    )
    async def request(event: SlashCommand, command: CommandObject) -> None:
        await event.answer(f"Received: {command.args or '(no topic)'}")

    store = SQLiteProvisioningStore(
        Path(os.environ["MM_STATE_DIR"]),
        codec=FernetCodec(SecretBytes(os.environ["MM_PROVISIONING_KEY"].encode("ascii"))),
    )
    setup_token = os.environ.get("MM_SETUP_TOKEN")
    bootstrap = Bootstrap(
        base_url=os.environ["MM_URL"],
        token=setup_token,
        application=os.environ.get("MM_APPLICATION", "request-service"),
        bot=BotSpec(username="request-bot", display_name="Request Bot"),
        store=store,
        callback_base_url=os.environ["MM_CALLBACK_URL"],
        team=TeamSpec(
            key="main",
            name=os.environ["MM_TEAM"],
            existing_policy="reuse",
        ),
        channels=(
            ChannelSpec(
                key="requests",
                team="main",
                name="service-requests",
                display_name="Service requests",
                visibility="private",
                create_if_missing=True,
                existing_policy="reuse",
                members=(MemberSpec(username=os.environ["MM_OPERATOR_USERNAME"]),),
            ),
        ),
    )
    bot = (
        await bootstrap.prepare(dispatcher)
        if setup_token is not None
        else await bootstrap.load_runtime(dispatcher)
    )
    http = HttpAdapter(
        bot,
        dispatcher,
        trusted_peers=os.environ["MM_HTTP_TRUSTED_PEERS"].split(","),
        slash_bindings=bot.resources.slash_bindings,
        path_prefix=bot.resources.http_path_prefix,
    )
    await dispatcher.start(bot, http=http, host="127.0.0.1", port=8080)


if __name__ == "__main__":
    asyncio.run(main())
