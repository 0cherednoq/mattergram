from __future__ import annotations

import asyncio
import json
import unittest

from aiohttp import ClientSession, WSMsgType, web
from pydantic import SecretStr

from mattergram import Bot, Dispatcher, F, Router
from mattergram.exceptions import (
    AuthenticationError,
    PermissionDeniedError,
    ProtocolError,
    ShutdownTimeoutError,
)
from mattergram.http import HttpAdapter, SlashBinding


class DispatcherWSTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mode = "normal"
        self.sockets = []
        self.connected = asyncio.Queue()
        self.challenges = []
        self.created = []
        app = web.Application()
        app.router.add_get("/sub/api/v4/users/me", self.identity)
        app.router.add_get("/sub/api/v4/websocket", self.websocket)
        app.router.add_post("/sub/api/v4/posts", self.post)
        self.runner = web.AppRunner(app, shutdown_timeout=0.1)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", 0).start()
        self.base = f"http://127.0.0.1:{self.runner.addresses[0][1]}/sub"
        self.bot = Bot(token="fixture-secret", base_url=self.base)
        # Keep ordinary cleanup above transient Windows/Proactor scheduling stalls.
        # The dedicated overdue-handler test below restores the short bound it needs.
        self.dp = Dispatcher(reconnect_delay=0.01, shutdown_timeout=0.5)
        self.task = None

    async def asyncTearDown(self):
        if self.task is not None and not self.task.done():
            await self.dp.stop()
        if self.task is not None:
            await asyncio.gather(self.task, return_exceptions=True)
        await self.bot.close()
        await self.dp.close()
        for ws in self.sockets:
            await ws.close()
        await self.runner.cleanup()

    async def identity(self, request):
        if self.mode == "invalid-token":
            return web.json_response({"id": "invalid_token"}, status=401)
        return web.json_response({"id": "bot", "username": "fixture"})

    async def post(self, request):
        body = await request.json()
        if self.mode == "forbidden":
            return web.json_response({"id": "permission_denied"}, status=403)
        post = {"id": "answer", "user_id": "bot", "create_at": 1, **body}
        self.created.append(post)
        await self.emit(post)
        return web.json_response(post, status=201)

    async def websocket(self, request):
        if self.mode == "redirect":
            return web.Response(status=302, headers={"Location": self.base + "/api/v4/users/me"})
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sockets.append(ws)
        self.challenges.append(await ws.receive_json(timeout=2))
        if self.mode == "bad-auth":
            await ws.send_json({"seq_reply": 1, "status": "FAIL"})
        elif self.mode == "bad-json":
            await ws.send_str("invalid-json")
        else:
            # Real servers may emit hello before the authentication acknowledgement.
            await ws.send_json({"event": "hello", "data": {"server_version": "fixture"}})
            await ws.send_json({"seq_reply": 1, "status": "OK"})
        self.connected.put_nowait(ws)
        async for frame in ws:
            if frame.type == WSMsgType.ERROR:
                break
        return ws

    async def emit(self, post=None):
        post = post or {
            "id": "incoming",
            "channel_id": "channel",
            "user_id": "human",
            "message": "ping",
            "create_at": 0,
        }
        await self.sockets[-1].send_json({"event": "posted", "data": {"post": json.dumps(post)}})

    async def start(self):
        ready = asyncio.Event()

        @self.dp.raw_event(F.event_type == "hello")
        async def hello(event):
            self.assertEqual(event.context.source, "websocket")
            ready.set()

        self.task = asyncio.create_task(self.dp.start_websocket(self.bot))
        await asyncio.wait_for(ready.wait(), 2)
        return await asyncio.wait_for(self.connected.get(), 2)

    async def finish(self):
        await self.dp.stop()
        await asyncio.wait_for(self.task, 2)
        self.assertTrue(self.bot.is_closed)
        self.assertFalse(self.dp._running)
        self.assertEqual(self.dp.events_isolation._locks, {})

    async def test_routing_answer_self_suppression_and_subpath(self):
        received = []
        barrier = asyncio.Event()

        @self.dp.message(F.text == "ping")
        async def handler(event, bot, state):
            received.append(event.post_id)
            self.assertIs(bot, self.bot)
            await state.set_data({"processed": True})
            await event.answer("pong")

        @self.dp.message(F.text == "barrier")
        async def done(event):
            barrier.set()

        @self.dp.message()
        async def unexpected(event):
            self.fail("The bot's own answer entered application routing")

        await self.start()
        await self.emit()
        while not self.created:
            await asyncio.sleep(0)
        await self.emit(
            {
                "id": "barrier",
                "channel_id": "channel",
                "user_id": "human",
                "message": "barrier",
                "create_at": 0,
            }
        )
        await asyncio.wait_for(barrier.wait(), 2)
        await self.finish()
        self.assertEqual(received, ["incoming"])
        self.assertEqual(self.created[0]["message"], "pong")
        self.assertEqual(
            self.challenges,
            [{"seq": 1, "action": "authentication_challenge", "data": {"token": "fixture-secret"}}],
        )

    async def test_permission_error_reaches_error_observer(self):
        handled = asyncio.Event()
        self.mode = "forbidden"

        @self.dp.message()
        async def handler(event):
            await event.answer("denied")

        @self.dp.error(lambda event: isinstance(event.exception, PermissionDeniedError))
        async def error(event, exception):
            self.assertEqual(event.event.post_id, "incoming")
            self.assertEqual(exception.status, 403)
            handled.set()

        await self.start()
        await self.emit()
        await asyncio.wait_for(handled.wait(), 2)
        await self.finish()

    async def test_reconnect_after_socket_disconnect(self):
        socket = await self.start()
        await socket.close()
        await asyncio.wait_for(self.connected.get(), 2)
        self.assertGreaterEqual(self.dp.reconnects, 1)
        await self.finish()

    async def test_invalid_identity_and_ws_protocol_cleanup(self):
        for mode, error in (
            ("invalid-token", AuthenticationError),
            ("bad-auth", AuthenticationError),
            ("bad-json", ProtocolError),
            ("redirect", ProtocolError),
        ):
            with self.subTest(mode=mode):
                self.mode = mode
                bot = Bot(token="fixture-secret", base_url=self.base)
                dp = Dispatcher()
                with self.assertRaises(error):
                    await asyncio.wait_for(dp.start_websocket(bot), 2)
                self.assertTrue(bot.is_closed)
                self.assertFalse(dp._running)
                self.assertTrue(dp._closed)

    async def test_startup_failure_runs_reverse_cleanup_and_remaining_handlers(self):
        order = []
        child = self.dp.include_router(Router())

        @self.dp.startup()
        async def startup():
            order.append("root-start")

        @child.startup()
        async def broken():
            order.append("child-start")
            raise ValueError("startup failed")

        @child.shutdown()
        async def cleanup_failure():
            order.append("child-stop-1")
            raise RuntimeError("cleanup failed")

        @child.shutdown()
        async def cleanup_rest():
            order.append("child-stop-2")

        @self.dp.shutdown()
        async def shutdown():
            order.append("root-stop")

        with self.assertRaisesRegex(ValueError, "startup failed"):
            await self.dp.start_websocket(self.bot)
        self.assertEqual(order, ["root-start", "child-start", "child-stop-1", "child-stop-2", "root-stop"])
        self.assertTrue(self.bot.is_closed)

    async def test_stop_cancels_overdue_handler_and_counts_queued_events(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()
        self.dp.queue_capacity = 1
        self.dp.shutdown_timeout = 0.05

        @self.dp.message()
        async def hung(event):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        await self.start()
        await self.emit()
        await asyncio.wait_for(entered.wait(), 2)
        await self.emit()
        await self.emit()
        async with asyncio.timeout(2):
            while self.dp.pending_events < 1:
                await asyncio.sleep(0)
        await self.dp.stop()
        with self.assertRaises(BaseExceptionGroup) as caught:
            await asyncio.wait_for(self.task, 2)
        self.assertTrue(any(isinstance(error, ShutdownTimeoutError) for error in caught.exception.exceptions))
        self.assertTrue(cancelled.is_set())
        self.assertGreaterEqual(self.dp.dropped_events, 1)
        self.assertEqual(self.dp.pending_events, 0)
        self.assertTrue(self.bot.is_closed)
        self.assertEqual(self.dp.events_isolation._locks, {})

    async def test_external_cancellation_drains_current_handler_and_closes_resources(self):
        entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        @self.dp.message()
        async def handler(event):
            entered.set()
            await release.wait()
            finished.set()

        await self.start()
        await self.emit()
        await asyncio.wait_for(entered.wait(), 2)
        self.task.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await self.task
        self.assertTrue(finished.is_set())
        self.assertTrue(self.bot.is_closed)
        self.assertEqual(self.dp.events_isolation._locks, {})

    async def test_combined_http_ws_startup_ready_and_ordered_shutdown(self):
        started = []
        http = HttpAdapter(
            self.bot,
            self.dp,
            trusted_peers=["127.0.0.1/32"],
            slash_bindings=[SlashBinding("team", "request", SecretStr("slash-fixture"))],
        )

        @self.dp.startup()
        async def startup():
            started.append("started")

        @self.dp.slash_command()
        async def command(event, state):
            self.assertEqual(started, ["started"])
            await state.set_state("received")
            await event.answer("combined")

        self.task = asyncio.create_task(self.dp.start(self.bot, http=http, port=0))
        await asyncio.wait_for(self.connected.get(), 2)
        host, port = http.listening_addresses[0]
        async with ClientSession() as client:
            async with client.post(
                f"http://{host}:{port}/mattergram/slash",
                data={
                    "token": "slash-fixture",
                    "team_id": "team",
                    "channel_id": "channel",
                    "user_id": "human",
                    "command": "/request",
                    "text": "",
                },
            ) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(await response.json(), {"response_type": "ephemeral", "text": "combined"})
        await self.finish()
        self.assertEqual(http.listening_addresses, ())
        self.assertTrue(http._closed)

    async def test_http_bind_failure_closes_partial_combined_startup(self):
        stopped = []
        http = HttpAdapter(
            self.bot,
            self.dp,
            trusted_peers=["127.0.0.1/32"],
            slash_bindings=[SlashBinding("team", "request", SecretStr("slash-fixture"))],
        )

        @self.dp.shutdown()
        async def shutdown():
            stopped.append(True)

        occupied = self.runner.addresses[0][1]
        with self.assertRaises(OSError):
            await self.dp.start(self.bot, http=http, host="127.0.0.1", port=occupied)
        self.assertTrue(self.bot.is_closed)
        self.assertTrue(self.dp._closed)
        self.assertTrue(http._closed)
        self.assertEqual(stopped, [True])
