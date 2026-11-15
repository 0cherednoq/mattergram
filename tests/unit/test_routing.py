from __future__ import annotations

import asyncio
import json
import unittest

from mattergram import Bot, Dispatcher, F, Router
from mattergram.dispatcher.event.bases import UNHANDLED, SkipHandler
from mattergram.exceptions import ContextCollisionError
from mattergram.filters import and_f
from mattergram.fsm.context import FSMContext
from mattergram.fsm.state import State, StatesGroup
from mattergram.fsm.storage.base import DefaultKeyBuilder, StorageKey
from mattergram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from mattergram.handlers import BaseHandler
from mattergram.types import ErrorEvent, Message, User


def message(**changes):
    return Message.from_wire(
        {"id": "p", "channel_id": "c", "user_id": "u", "message": "ping", "create_at": 0, **changes}
    )


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = Bot(token="fixture", base_url="http://127.0.0.1:1")
        self.bot._me = User(id="bot")
        self.dp = Dispatcher(marker=42)

    async def asyncTearDown(self):
        await self.dp.close()
        await self.bot.close()

    async def test_filter_middleware_order_named_di_and_first_none_is_handled(self):
        order = []
        child = self.dp.include_router(Router(name="child"))

        async def outer(handler, event, data):
            order.append("outer-before")
            result = await handler(event, data)
            order.append("outer-after")
            return result

        async def inner(handler, event, data):
            order.append("inner-before")
            result = await handler(event, data)
            order.append("inner-after")
            return result

        def root_filter(event):
            order.append("root")
            return {"found": event.text}

        child.message.outer_middleware(outer)
        child.message.middleware(inner)
        child.message.filter(root_filter)

        @child.message(F.text == "ping")
        async def first(event, bot, found, marker, state):
            order.append("handler")
            self.assertIs(bot, self.bot)
            self.assertEqual((found, marker), ("ping", 42))
            self.assertIsInstance(state, FSMContext)
            self.assertEqual(event.context.bot_id, "bot")
            self.assertIsNotNone(event.context.received_at.tzinfo)
            return None

        @child.message()
        async def second(event):
            self.fail("None is a handled result, not UNHANDLED")

        self.assertIsNone(await self.dp.feed_event(self.bot, message()))
        self.assertEqual(
            order, ["outer-before", "root", "inner-before", "handler", "inner-after", "outer-after"]
        )

    async def test_skip_handler_filter_data_magic_capture_and_logic(self):
        @self.dp.message()
        async def skip(event):
            raise SkipHandler()

        @self.dp.message(and_f(F.text.regexp(r"^p(.*)$").as_("match"), lambda event: {"value": 7}))
        async def chosen(event, match, value):
            return match.group(1), value

        self.assertEqual(await self.dp.feed_event(self.bot, message()), ("ing", 7))
        empty = Dispatcher()
        self.assertIs(await empty.feed_event(self.bot, message()), UNHANDLED)
        await empty.close()

    async def test_class_and_async_callable_handlers_are_awaited(self):
        class ClassHandler(BaseHandler):
            async def handle(self):
                return self.update.post_id, self.bot.user_id

        self.dp.message.register(ClassHandler)
        self.assertEqual(await self.dp.feed_event(self.bot, message()), ("p", "bot"))
        other = Dispatcher()

        class AsyncCallable:
            async def __call__(self, event):
                return event.text

        other.message.register(AsyncCallable())
        self.assertEqual(await other.feed_event(self.bot, message()), "ping")
        await other.close()

    async def test_reserved_filter_or_application_data_are_rejected(self):
        self.dp.message.register(lambda event: None, lambda event: {"bot": object()})
        with self.assertRaises(ContextCollisionError):
            await self.dp.feed_event(self.bot, message())
        with self.assertRaises(ContextCollisionError):
            await self.dp.feed_event(self.bot, message(), state="forged")
        with self.assertRaises(ContextCollisionError):
            Dispatcher(bot="forged")

    async def test_error_observer_keeps_original_event_and_context(self):
        original = message()

        @self.dp.message()
        async def broken(event):
            raise ValueError("business error")

        @self.dp.error(lambda event: isinstance(event.exception, ValueError))
        async def error(event, marker, exception, state):
            self.assertIsInstance(event, ErrorEvent)
            self.assertIs(event.event, original)
            self.assertEqual(marker, 42)
            self.assertIsInstance(exception, ValueError)
            return "handled-error"

        self.assertEqual(await self.dp.feed_event(self.bot, original), "handled-error")

    async def test_real_posted_input_and_self_event_never_fall_back_to_raw(self):
        events = []

        @self.dp.message(F.text == "ping")
        async def selected(event):
            events.append("message")

        @self.dp.raw_event()
        async def raw(event):
            events.append(event.event_type)

        payload = {"event": "posted", "data": {"post": json.dumps(dict(message().raw))}}
        await self.dp.feed_update(self.bot, payload)
        payload["data"]["post"] = json.dumps(dict(message(user_id="bot").raw))
        await self.dp.feed_update(self.bot, payload)
        await self.dp.feed_update(self.bot, {"event": "future_native_event", "data": {"extra": 1}})
        self.assertEqual(events, ["message", "future_native_event"])

    async def test_router_cycles_and_duplicates_rejected(self):
        child = self.dp.include_router(Router())
        with self.assertRaises(RuntimeError):
            child.include_router(self.dp)
        with self.assertRaises(RuntimeError):
            self.dp.include_router(child)
        with self.assertRaises(RuntimeError):
            child.include_router(child)

    async def test_state_transitions_are_serialized_and_isolated(self):
        class Flow(StatesGroup):
            title = State()

        seen = []

        @self.dp.message(Flow.title)
        async def waiting(event, state, raw_state):
            self.assertEqual(raw_state, Flow.title.state)
            seen.append("waiting:" + event.user_id)
            await state.clear()

        @self.dp.message()
        async def first(event, state):
            await asyncio.sleep(0.005)
            seen.append("first:" + event.user_id)
            await state.set_state(Flow.title)

        await asyncio.gather(self.dp.feed_event(self.bot, message()), self.dp.feed_event(self.bot, message()))
        await self.dp.feed_event(self.bot, message(user_id="other"))
        self.assertEqual(seen, ["first:u", "waiting:u", "first:other"])
        self.assertEqual(self.dp.events_isolation._locks, {})

    async def test_namespace_keys_and_native_strategy_do_not_invent_ids(self):
        shared = MemoryStorage()
        d1, d2 = Dispatcher(storage=shared), Dispatcher(storage=shared)

        async def increment(event, state):
            data = await state.get_data()
            count = data.get("count", 0) + 1
            await state.update_data(count=count)
            return count

        d1.message.register(increment)
        d2.message.register(increment)
        second_bot = Bot(token="fixture", base_url="http://127.0.0.1:2")
        second_bot._me = User(id="bot")
        self.assertEqual(await d1.feed_event(self.bot, message()), 1)
        self.assertEqual(await d2.feed_event(second_bot, message()), 1)
        self.assertEqual(await d1.feed_event(self.bot, message()), 2)
        await d1.close()
        await d2.close()
        self.assertTrue(shared.storage)
        await shared.close()
        await second_bot.close()
        key = StorageKey("instance", "bot", "channel", None)
        self.assertIsNone(key.user_id)
        builder = DefaultKeyBuilder()
        self.assertNotEqual(
            builder.build(key), builder.build(StorageKey("instance:bot", "other", "channel", None))
        )
        with self.assertRaises(ValueError):
            StorageKey("instance", 123, "channel", "user")

    async def test_close_rejects_active_manual_feeds_then_can_be_retried(self):
        entered, release = asyncio.Event(), asyncio.Event()

        @self.dp.message()
        async def wait(event):
            entered.set()
            await release.wait()

        task = asyncio.create_task(self.dp.feed_event(self.bot, message()))
        await entered.wait()
        with self.assertRaises(RuntimeError):
            await self.dp.close()
        release.set()
        await task
        await self.dp.close()
        with self.assertRaises(RuntimeError):
            await self.dp.feed_update(self.bot, {"event": "typing"})

    async def test_cancelled_waiter_releases_lock_references(self):
        isolation = SimpleEventIsolation()
        key = StorageKey("instance", "bot", "channel", "user")
        entered = asyncio.Event()

        async def waiting():
            entered.set()
            async with isolation.lock(key):
                self.fail("waiter acquired locked state")

        async with isolation.lock(key):
            task = asyncio.create_task(waiting())
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(isolation._locks, {})
        await isolation.close()


if __name__ == "__main__":
    unittest.main()
