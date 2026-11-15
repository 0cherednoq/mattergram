from __future__ import annotations

import asyncio
import os
import unittest
from uuid import uuid4

from aiohttp import web
from pydantic import SecretStr

from mattergram import Bot
from mattergram.http import DialogManager
from mattergram.http.adapter import HTTPResult
from mattergram.http.redis_dialogs import RedisDialogStore
from mattergram.types import SlashCommand, User
from mattergram.ui import UIProfile
from mattergram.ui.forms import Form, TextInput


@unittest.skipUnless(os.environ.get("MATTERGRAM_REDIS_URL"), "Real Redis profile not requested")
class RedisDialogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.prefix = "mattergram-dialog-test-" + uuid4().hex
        self.opened = []
        app = web.Application()

        async def native_open(request):
            self.opened.append(await request.json())
            return web.json_response({"status": "OK"})

        app.router.add_post("/api/v4/actions/dialogs/open", native_open)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", 0).start()
        base = f"http://127.0.0.1:{self.runner.addresses[0][1]}"
        self.bot = Bot(token="fixture-token", base_url=base)
        self.bot._me = User(id="bot")
        self.stores = [
            RedisDialogStore.from_url(os.environ["MATTERGRAM_REDIS_URL"], prefix=self.prefix)
            for _ in range(2)
        ]
        profile = UIProfile(
            "11.1.0", client_features=frozenset({"forms.basic"}), client_evidence="test fixture"
        )
        self.managers = [
            DialogManager(
                self.bot, profile=profile, store=store, callback_url="https://integration.test/dialog"
            )
            for store in self.stores
        ]
        for manager in self.managers:
            await manager.startup()
        form = Form(callback_id="f", title="Form", elements=(TextInput(name="title", label="Title"),))
        command = SlashCommand(
            user_id="user",
            channel_id="channel",
            team_id="team",
            command="/form",
            token=SecretStr("command-token"),
            trigger_id=SecretStr("fixture-trigger"),
        )
        self.handle = await self.managers[0].open(command, form)
        self.wire = {
            "type": "dialog_submission",
            "callback_id": "f",
            "state": self.handle.state.get_secret_value(),
            "user_id": "user",
            "channel_id": "channel",
            "team_id": "team",
            "cancelled": False,
            "submission": {"title": "value"},
        }

    async def asyncTearDown(self):
        for manager in self.managers:
            await manager.close()
        client = self.stores[0].storage.redis
        keys = [key async for key in client.scan_iter(match=self.prefix + ":*")]
        if keys:
            await client.delete(*keys)
        for store in self.stores:
            await store.close()
        await self.bot.close()
        await self.runner.cleanup()

    async def test_shared_session_claim_and_terminal_replay_across_independent_clients(self):
        calls = []

        async def serve(event, response):
            calls.append(event.session_id)
            await asyncio.sleep(0.02)
            response.ack()
            return HTTPResult(200, response.payload)

        replies = await asyncio.gather(
            *(
                self.managers[i % 2].handle(
                    self.wire, deadline=asyncio.get_running_loop().time() + 3, serve=serve
                )
                for i in range(8)
            )
        )
        self.assertEqual(len(calls), 1)
        self.assertTrue(all(reply.status == 200 and reply.payload == {} for reply in replies))
        record = await self.stores[1].get(self.managers[1]._key(self.handle.session_id))
        self.assertEqual(record["status"], "closed")
        self.assertGreater(
            await self.stores[0].storage.redis.ttl(
                self.stores[0].storage.key_builder.build(
                    self.managers[0]._key(self.handle.session_id), "data"
                )
            ),
            0,
        )

    async def test_unknown_claim_survives_worker_change_and_requires_reconciliation(self):
        calls = []

        async def interrupted(event, response):
            calls.append("effect")
            raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            await self.managers[0].handle(
                self.wire, deadline=asyncio.get_running_loop().time() + 3, serve=interrupted
            )

        async def must_not_run(event, response):
            self.fail("Unknown business outcome was retried on another worker")

        retry = await self.managers[1].handle(
            self.wire, deadline=asyncio.get_running_loop().time() + 3, serve=must_not_run
        )
        self.assertEqual(retry.status, 409)
        await self.managers[1].reconcile(self.handle.state, response={})
        completed = await self.managers[0].handle(
            self.wire, deadline=asyncio.get_running_loop().time() + 3, serve=must_not_run
        )
        self.assertEqual((completed.status, completed.payload), (200, {}))
        self.assertEqual(calls, ["effect"])

    async def test_manager_borrows_shared_store_and_wrapper_rejects_active_close(self):
        await self.managers[0].close()
        self.assertTrue(await self.stores[0].storage.redis.ping())
        async with self.stores[0].lock(self.managers[1]._key(self.handle.session_id)):
            with self.assertRaises(RuntimeError):
                await self.stores[0].close()
        self.assertFalse(self.stores[0].storage.is_closed)
