"""Run with MM_URL, MM_BOT_TOKEN and MM_REDIS_URL; send 'start', then a title."""

from __future__ import annotations

import asyncio
import os

from mattergram import Bot, Dispatcher, F
from mattergram.fsm.context import FSMContext
from mattergram.fsm.state import State, StatesGroup
from mattergram.fsm.storage.redis import RedisStorage
from mattergram.types import Message


class Request(StatesGroup):
    waiting_title = State()


async def main() -> None:
    storage = RedisStorage.from_url(os.environ["MM_REDIS_URL"], state_ttl=3600, data_ttl=3600)
    isolation = storage.create_isolation()
    try:
        dispatcher = Dispatcher(storage=storage, events_isolation=isolation)

        @dispatcher.message(Request.waiting_title)
        async def title(message: Message, state: FSMContext) -> None:
            await state.clear()
            await message.reply("Тема получена: " + message.text)

        @dispatcher.message(F.text == "start")
        async def start(message: Message, state: FSMContext) -> None:
            await state.set_state(Request.waiting_title)
            await message.answer("Введите тему")

        bot = Bot(token=os.environ["MM_BOT_TOKEN"], base_url=os.environ["MM_URL"])
        await dispatcher.start_websocket(bot)
    finally:
        # Dispatcher borrows application-supplied resources. Close the isolation
        # before its owning storage, after all handlers have finished.
        try:
            await isolation.close()
        finally:
            await storage.close()


if __name__ == "__main__":
    asyncio.run(main())
