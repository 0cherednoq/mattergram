"""Run with MM_URL and MM_BOT_TOKEN; send 'ping' in a channel joined by the bot."""

from __future__ import annotations

import asyncio
import os

from mattergram import Bot, Dispatcher, F, Router
from mattergram.types import Message


async def main() -> None:
    router = Router(name="ping")

    @router.message(F.text == "ping")
    async def ping(message: Message) -> None:
        await message.answer("pong")

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    bot = Bot(token=os.environ["MM_BOT_TOKEN"], base_url=os.environ["MM_URL"])
    await dispatcher.start_websocket(bot)


if __name__ == "__main__":
    asyncio.run(main())
