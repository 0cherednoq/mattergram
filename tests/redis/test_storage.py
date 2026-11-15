"""Real Redis profile. Explicitly skipped without MATTERGRAM_REDIS_URL; never faked."""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from dataclasses import replace
from datetime import timedelta
from types import MappingProxyType
from unittest.mock import patch
from uuid import uuid4

from redis.asyncio import Redis
from redis.asyncio.lock import Lock
from redis.exceptions import LockError, LockNotOwnedError

from mattergram import Bot, Dispatcher
from mattergram.exceptions import StorageDataError
from mattergram.fsm.context import FSMContext
from mattergram.fsm.state import State
from mattergram.fsm.storage.base import DefaultKeyBuilder, StorageKey
from mattergram.fsm.storage.redis import RedisEventIsolation, RedisStorage
from mattergram.fsm.strategy import FSMStrategy
from mattergram.types import Message, User


@unittest.skipUnless(os.environ.get("MATTERGRAM_REDIS_URL"), "Real Redis profile not requested")
class RedisStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.url = os.environ["MATTERGRAM_REDIS_URL"]
        self.prefix = "mattergram-test-" + uuid4().hex
        self.builder = DefaultKeyBuilder(prefix=self.prefix)
        self.client = Redis.from_url(self.url, socket_timeout=2, socket_connect_timeout=2)
        await self.client.ping()
        self.key = StorageKey("https://instance.test", "bot", "channel", "user")
        self.resources = []
        self.storage = self.keep(RedisStorage(self.client, key_builder=self.builder))

    def keep(self, resource):
        self.resources.append(resource)
        return resource

    def storage_from_url(self, **kwargs):
        return self.keep(RedisStorage.from_url(self.url, key_builder=self.builder, **kwargs))

    def isolation(self, storage=None, **kwargs):
        return self.keep((storage or self.storage).create_isolation(**kwargs))

    async def asyncTearDown(self):
        try:
            for resource in reversed(self.resources):
                await resource.close()
        finally:
            try:
                keys = [key async for key in self.client.scan_iter(match=self.prefix + ":*")]
                if keys:
                    await self.client.delete(*keys)
            finally:
                await self.client.aclose(close_connection_pool=True)

    async def test_state_mapping_data_clear_and_client_reconnect(self):
        ctx = FSMContext(self.storage, self.key)
        await ctx.set_state(State("waiting", group_name="Flow"))
        await ctx.set_data(MappingProxyType({"nested": [1]}))
        data = await ctx.get_data()
        data["nested"].append(2)
        self.assertEqual(await ctx.get_data(), {"nested": [1]})
        self.assertEqual(await ctx.get_value("missing", "fallback"), "fallback")
        await ctx.update_data(title="native")
        self.assertEqual(await ctx.get_data(), {"nested": [1], "title": "native"})
        # State is on the server, independent of any client instance or its pool.
        await self.client.connection_pool.disconnect()
        second = self.storage_from_url()
        self.assertEqual(await second.get_state(self.key), "Flow:waiting")
        self.assertEqual(await second.get_data(self.key), {"nested": [1], "title": "native"})
        await ctx.clear()
        self.assertEqual(await second.get_data(self.key), {})
        self.assertIsNone(await second.get_state(self.key))
        await ctx.set_state(State())
        self.assertIsNone(await ctx.get_state())

    async def test_ttl_is_applied_and_really_expires(self):
        storage = self.storage_from_url(state_ttl=timedelta(seconds=2), data_ttl=1)
        await storage.set_state(self.key, "waiting")
        await storage.set_data(self.key, {"answer": 42})
        self.assertIn(await self.client.ttl(self.builder.build(self.key, "state")), (1, 2))
        self.assertIn(await self.client.ttl(self.builder.build(self.key, "data")), (0, 1))
        async with asyncio.timeout(4):
            while await storage.get_state(self.key) is not None:
                await asyncio.sleep(0.025)
        self.assertEqual(await storage.get_data(self.key), {})

    async def test_all_native_key_scopes_and_two_form_sessions_are_isolated(self):
        keys = [self.key]
        for name, value in (
            ("instance_id", "https://other-instance.test"),
            ("bot_id", "other-bot"),
            ("channel_id", "other-channel"),
            ("user_id", "other-user"),
            ("thread_id", "native-root"),
            ("team_id", "native-team"),
            ("dialog_session_id", "form-one"),
            ("dialog_session_id", "form-two"),
            ("destiny", "another-flow"),
        ):
            keys.append(replace(self.key, **{name: value}))
        for number, key in enumerate(keys):
            await self.storage.set_state(key, "state-" + str(number))
            await self.storage.set_data(key, {"form": number})
        for number, key in enumerate(keys):
            self.assertEqual(await self.storage.get_state(key), "state-" + str(number))
            self.assertEqual(await self.storage.get_data(key), {"form": number})
        self.assertEqual(len({self.builder.build(key, "data") for key in keys}), len(keys))

    async def test_decode_modes_custom_serializer_and_corrupt_values(self):
        for decode in (False, True):
            storage = self.storage_from_url(connection_kwargs={"decode_responses": decode})
            await storage.set_state(self.key, "состояние")
            await storage.set_data(self.key, {"текст": "значение"})
            self.assertEqual(await storage.get_state(self.key), "состояние")
            self.assertEqual(await storage.get_data(self.key), {"текст": "значение"})
        custom = self.storage_from_url(
            json_dumps=lambda data: json.dumps({"payload": data}),
            json_loads=lambda value: json.loads(value)["payload"],
        )
        await custom.set_data(self.key, {"custom": True})
        self.assertEqual(await custom.get_data(self.key), {"custom": True})
        for value in (b"broken-json secret", b"[]", b"null", b'"secret"', b"\xff"):
            with self.subTest(value=value):
                await self.client.set(self.builder.build(self.key, "data"), value)
                with self.assertRaises(StorageDataError) as caught:
                    await self.storage.get_data(self.key)
                self.assertNotIn("secret", str(caught.exception))
        await self.client.set(self.builder.build(self.key, "state"), b"\xff")
        with self.assertRaises(StorageDataError):
            await self.storage.get_state(self.key)

    async def test_borrowed_and_owned_pool_lifecycle(self):
        isolation = self.isolation()
        await isolation.close()
        self.assertTrue(isolation.is_closed)
        await self.storage.close()
        self.assertTrue(self.storage.is_closed)
        self.assertTrue(await self.client.ping())
        with self.assertRaises(RuntimeError):
            await self.storage.get_data(self.key)
        with self.assertRaises(RuntimeError):
            async with isolation.lock(self.key):
                self.fail("closed isolation entered")
        for factory in (
            self.storage_from_url,
            lambda **kw: self.keep(RedisEventIsolation.from_url(self.url, key_builder=self.builder, **kw)),
        ):
            name = self.prefix + uuid4().hex
            owned = factory(connection_kwargs={"client_name": name})
            await owned.redis.ping()
            self.assertIn(name, [entry["name"] for entry in await self.client.client_list()])
            await owned.close()
            # wait_closed confirms the local transport is closed; Redis processes
            # its FIN independently of CLIENT LIST on this second connection.
            # Verify bounded server convergence rather than assume cross-socket ordering.
            async with asyncio.timeout(1):
                while name in [entry["name"] for entry in await self.client.client_list()]:
                    await asyncio.sleep(0.01)
            await owned.close()

    async def test_independent_clients_serialize_read_modify_write(self):
        other = self.storage_from_url()
        isolation1 = self.isolation(lock_kwargs={"sleep": 0.01})
        isolation2 = self.isolation(other, lock_kwargs={"sleep": 0.01})

        async def increment(storage, isolation):
            async with isolation.lock(self.key):
                data = await storage.get_data(self.key)
                await asyncio.sleep(0.003)
                await storage.set_data(self.key, {"count": data.get("count", 0) + 1})

        async with asyncio.TaskGroup() as group:
            for number in range(20):
                group.create_task(
                    increment(self.storage if number % 2 else other, isolation1 if number % 2 else isolation2)
                )
        self.assertEqual(await other.get_data(self.key), {"count": 20})
        self.assertFalse(await self.client.exists(self.builder.build(self.key, "lock")))

    async def test_cancelled_waiter_never_releases_another_owners_lock(self):
        owner = self.isolation()
        contender = self.isolation(self.storage_from_url(), lock_kwargs={"sleep": 0.01})
        entered = asyncio.Event()

        async def waiting():
            entered.set()
            async with contender.lock(self.key):
                self.fail("cancelled waiter acquired the owner's key")

        async with owner.lock(self.key):
            original = await self.client.get(self.builder.build(self.key, "lock"))
            task = asyncio.create_task(waiting())
            await entered.wait()
            await asyncio.sleep(0.02)
            cancelled_at = asyncio.get_running_loop().time()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertLess(asyncio.get_running_loop().time() - cancelled_at, 2)
            self.assertEqual(await self.client.get(self.builder.build(self.key, "lock")), original)
        self.assertFalse(await self.client.exists(self.builder.build(self.key, "lock")))

    async def test_active_close_rejected_and_cancelled_holder_releases(self):
        isolation = self.isolation()
        entered = asyncio.Event()

        async def holding():
            async with isolation.lock(self.key):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(holding())
        await entered.wait()
        try:
            with self.assertRaises(RuntimeError):
                await isolation.close()
            self.assertFalse(isolation.is_closed)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(await self.client.exists(self.builder.build(self.key, "lock")))
        await isolation.close()

    async def test_wait_and_handler_bounds_release_before_lease_expiry(self):
        owner = self.isolation()
        contender = self.isolation(lock_kwargs={"blocking_timeout": 0.05, "sleep": 0.01})
        async with owner.lock(self.key):
            with self.assertRaises((TimeoutError, LockError)):
                async with contender.lock(self.key):
                    self.fail("contender acquired held key")
        bounded = self.isolation(lock_kwargs={"timeout": 0.5}, hold_timeout=0.05)
        with self.assertRaises(TimeoutError):
            async with bounded.lock(self.key):
                await asyncio.Event().wait()
        # A swallowed cancellation must not turn an expired budget into success.
        with self.assertRaises(TimeoutError):
            async with bounded.lock(self.key):
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    pass
        self.assertFalse(await self.client.exists(self.builder.build(self.key, "lock")))
        async with owner.lock(self.key):
            self.assertTrue(await self.client.exists(self.builder.build(self.key, "lock")))

    async def test_lost_lock_is_reported_instead_of_silently_committing(self):
        isolation = self.isolation()
        with self.assertRaises(LockNotOwnedError):
            async with isolation.lock(self.key):
                await self.client.delete(self.builder.build(self.key, "lock"))

    async def test_second_cancellation_during_release_still_releases_real_lock(self):
        isolation = self.isolation()
        entered, releasing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = Lock.release

        async def delayed(lock):
            releasing.set()
            await release.wait()
            await original(lock)

        async def holding():
            async with isolation.lock(self.key):
                entered.set()
                await asyncio.Event().wait()

        with patch.object(Lock, "release", delayed):
            task = asyncio.create_task(holding())
            await entered.wait()
            task.cancel()
            await releasing.wait()
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(await self.client.exists(self.builder.build(self.key, "lock")))

    async def test_acquisition_latency_does_not_extend_handler_lease(self):
        owner = self.isolation()
        contender = self.isolation(lock_kwargs={"timeout": 1, "sleep": 0.01}, hold_timeout=0.02)
        entered = asyncio.Event()

        async def waiting():
            entered.set()
            async with contender.lock(self.key):
                self.fail("handler ran after its local lease budget expired")

        async with owner.lock(self.key):
            task = asyncio.create_task(waiting())
            await entered.wait()
            await asyncio.sleep(0.05)
        with self.assertRaises(TimeoutError):
            await task
        self.assertFalse(await self.client.exists(self.builder.build(self.key, "lock")))

    async def test_dispatcher_strategies_preserve_the_native_scope(self):
        bot = Bot(token="unit-fixture", base_url="https://instance.test")
        bot._me = User(id="bot")
        try:
            for strategy, expected in (
                (FSMStrategy.USER_IN_CHANNEL, [1, 1, 1, 2]),
                (FSMStrategy.CHANNEL, [1, 2, 1, 3]),
                (FSMStrategy.GLOBAL_USER, [1, 1, 2, 3]),
                (FSMStrategy.USER_IN_THREAD, [1, 1, 1, 1]),
                (FSMStrategy.CHANNEL_THREAD, [1, 2, 1, 1]),
            ):
                with self.subTest(strategy=strategy):
                    scoped = self.storage_from_url()
                    # Each strategy has its own application namespace, not a fabricated ID.
                    scoped.key_builder = DefaultKeyBuilder(prefix=self.prefix + ":" + strategy.name)
                    isolation = self.isolation(scoped)
                    dp = Dispatcher(storage=scoped, events_isolation=isolation, fsm_strategy=strategy)

                    @dp.message()
                    async def count(event, state):
                        data = await state.get_data()
                        value = data.get("count", 0) + 1
                        await state.update_data(count=value)
                        return value

                    results = []
                    try:
                        for user, channel, root in (
                            ("u", "c", "root-a"),
                            ("other", "c", "root-a"),
                            ("u", "other", "root-a"),
                            ("u", "c", "root-b"),
                        ):
                            event = Message.from_wire(
                                {
                                    "id": "post",
                                    "channel_id": channel,
                                    "user_id": user,
                                    "message": "test",
                                    "create_at": 0,
                                    "root_id": root,
                                }
                            )
                            results.append(await dp.feed_event(bot, event))
                        self.assertEqual(results, expected)
                    finally:
                        await dp.close()
                    self.assertFalse(scoped.is_closed)
                    self.assertFalse(isolation.is_closed)
        finally:
            await bot.close()


if __name__ == "__main__":
    unittest.main()
