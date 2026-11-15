"""Own an HTTP/WS runtime for an already provisioned native slash command."""

from __future__ import annotations

import asyncio
import os

from pydantic import SecretStr

from mattergram import Bot, Dispatcher
from mattergram.filters import Command, CommandObject
from mattergram.http import HttpAdapter, SlashBinding
from mattergram.types import SlashCommand


async def main() -> None:
    bot = Bot(token=os.environ["MM_BOT_TOKEN"], base_url=os.environ["MM_URL"])
    dispatcher = Dispatcher()

    @dispatcher.slash_command(Command("request"))
    async def request(event: SlashCommand, command: CommandObject) -> None:
        await event.answer(f"Received: {command.args or '(no arguments)'}")

    http = HttpAdapter(
        bot,
        dispatcher,
        trusted_peers=os.environ["MM_HTTP_TRUSTED_PEERS"].split(","),
        slash_bindings=[
            SlashBinding(os.environ["MM_TEAM_ID"], "request", SecretStr(os.environ["MM_SLASH_TOKEN"]))
        ],
    )
    await dispatcher.start(bot, http=http, host="127.0.0.1", port=8080)


if __name__ == "__main__":
    asyncio.run(main())
