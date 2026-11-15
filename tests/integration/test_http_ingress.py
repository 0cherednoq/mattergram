from __future__ import annotations

import asyncio
import json
import unittest
from urllib.parse import urlencode

import aiohttp
from aiohttp import web
from pydantic import SecretStr

from mattergram import Bot, Dispatcher, F
from mattergram.exceptions import APIError, RequestOutcomeUnknown, ResponseDeadlineError, ResponseStateError
from mattergram.filters import Command
from mattergram.http import ASGIAdapter, CallbackSigner, HttpAdapter, SlashBinding
from mattergram.http.delayed import DelayedResponder


class HTTPIngressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.provider_calls = []
        self.followups = []
        self.provider_mode = "normal"
        self.provider_entered = asyncio.Event()
        self.provider_release = asyncio.Event()
        provider = web.Application()
        provider.router.add_get("/sub/api/v4/users/me", self.identity)
        provider.router.add_post("/sub/hooks/commands/{token}", self.respond)
        self.provider_runner = web.AppRunner(provider, access_log=None, shutdown_timeout=0.1)
        await self.provider_runner.setup()
        await web.TCPSite(self.provider_runner, "127.0.0.1", 0).start()
        self.base = f"http://127.0.0.1:{self.provider_runner.addresses[0][1]}/sub"
        self.bot = Bot(token="bot-secret", base_url=self.base)
        self.dp = Dispatcher()
        self.signer = CallbackSigner(
            instance_id=self.bot.instance_id,
            bot_id="bot-native",
            keys={"main": b"s" * 32},
            active_key_id="main",
        )
        self.adapter = HttpAdapter(
            self.bot,
            self.dp,
            trusted_peers=["127.0.0.1/32"],
            slash_bindings=[
                SlashBinding("team", "request", SecretStr("slash-secret")),
                SlashBinding("team", "getrequest", SecretStr("get-secret"), method="GET"),
            ],
            callback_signer=self.signer,
            request_timeout=0.5,
            shutdown_timeout=0.3,
            max_deferred=1,
        )
        self.runner = web.AppRunner(self.adapter.create_app(), access_log=None, shutdown_timeout=0.3)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", 0).start()
        self.incoming = f"http://127.0.0.1:{self.runner.addresses[0][1]}/mattergram"
        self.client = aiohttp.ClientSession()
        self.provider_calls.clear()

    async def asyncTearDown(self):
        self.provider_release.set()
        await self.runner.cleanup()
        await self.dp.close()
        await self.bot.close()
        await self.client.close()
        await self.provider_runner.cleanup()

    async def identity(self, request):
        self.provider_calls.append("identity")
        return web.json_response({"id": "bot-native", "username": "native"})

    async def respond(self, request):
        self.provider_calls.append("followup")
        self.followups.append(
            (await request.json(), request.headers.get("Authorization"), request.headers.get("Cookie"))
        )
        if self.provider_mode == "redirect":
            return web.Response(status=302, headers={"Location": self.base + "/other"})
        if self.provider_mode == "forbidden":
            return web.json_response({"id": "denied"}, status=403)
        if self.provider_mode == "write-then-delay":
            self.provider_entered.set()
            await self.provider_release.wait()
        return web.json_response({})

    def slash(self, **changes):
        return {
            "token": "slash-secret",
            "team_id": "team",
            "channel_id": "channel",
            "user_id": "human",
            "command": "/request",
            "text": "arg & value",
            "trigger_id": "private-trigger",
            "response_url": self.base + "/hooks/commands/private-capability",
            **changes,
        }

    def callback(self):
        context = self.signer.seal(
            "action:approve",
            channel_id="channel",
            post_id="post",
            user_id="human",
            allowed_options=["yes", "no"],
        )
        context["selected_option"] = "yes"
        return {
            "user_id": "human",
            "post_id": "post",
            "channel_id": "channel",
            "team_id": "",
            "context": context,
            "trigger_id": "private-trigger",
        }

    async def post_slash(self, **changes):
        async with self.client.post(self.incoming + "/slash", data=self.slash(**changes)) as response:
            return response.status, await response.json()

    async def post_callback(self, payload=None):
        async with self.client.post(self.incoming + "/callback", json=payload or self.callback()) as response:
            return response.status, await response.json()

    async def test_real_http_slash_and_callback_response_families(self):
        @self.dp.slash_command(Command("request"))
        async def command(event, command):
            self.assertEqual(command.args, "arg & value")
            await event.answer("public", ephemeral=False)

        @self.dp.callback_query(F.data == "action:approve", F.selected_option == "yes")
        async def callback(event, state):
            self.assertEqual(event.message.post_id, "post")
            await state.set_state("done")
            await event.update_message("approved", props={"custom": True})

        self.assertEqual(await self.post_slash(), (200, {"response_type": "in_channel", "text": "public"}))
        self.assertEqual(
            await self.post_callback(), (200, {"update": {"message": "approved", "props": {"custom": True}}})
        )
        self.assertEqual(self.provider_calls, [])

    async def test_get_form_preserves_escaped_separators_and_empty_ack(self):
        @self.dp.slash_command(Command("getrequest"))
        async def command(event, command):
            self.assertEqual(command.args, "a&b +c")

        async with self.client.get(
            self.incoming + "/slash",
            params=self.slash(command="/getrequest", token="get-secret", text="a&b +c"),
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), {"response_type": "ephemeral", "text": ""})

    async def test_auth_validation_never_dispatches_or_uses_outbound_credentials(self):
        @self.dp.slash_command()
        @self.dp.callback_query()
        async def forbidden(event):
            self.fail("untrusted or invalid request reached handler")

        for changes in ({"token": "bad"}, {"team_id": "wrong"}, {"command": "/other"}):
            self.assertEqual((await self.post_slash(**changes))[0], 403)
        wire = self.callback()
        wire["context"]["mattergram"]["payload"]["data"] = "tampered"
        self.assertEqual((await self.post_callback(wire))[0], 403)
        async with self.client.post(
            self.incoming + "/callback",
            data=b'{"user_id":"one","user_id":"two"}',
            headers={"Content-Type": "application/json"},
        ) as response:
            self.assertEqual(response.status, 400)
        async with self.client.post(
            self.incoming + "/slash",
            data=b"token=one&token=two",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as response:
            self.assertEqual(response.status, 400)
        self.assertEqual(self.provider_calls, [])

    async def test_duplicate_completion_and_business_error_observer(self):
        @self.dp.callback_query()
        async def callback(event):
            await event.answer("first")
            await event.answer("private second")

        status, payload = await self.post_callback()
        self.assertEqual(status, 500)
        self.assertNotIn("private", json.dumps(payload))

        @self.dp.error(lambda event: isinstance(event.exception, ResponseStateError))
        async def handled(error):
            self.assertEqual(error.context.source, "http-callback")

        # The observer handles the SDK error; the first reserved response stays intact.
        self.assertEqual(await self.post_callback(), (200, {"ephemeral_text": "first"}))

    async def test_deadline_cancels_handler_releases_fsm_and_does_not_auto_ack(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()

        @self.dp.callback_query()
        async def hung(event):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        request = asyncio.create_task(self.post_callback())
        await asyncio.wait_for(entered.wait(), 2)
        self.assertEqual((await request)[0], 504)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.dp.events_isolation._locks, {})
        self.assertEqual(self.dp._active_feeds, 0)

    async def test_deferred_reply_capacity_no_bearer_and_owned_task_cleanup(self):
        entered, release = asyncio.Event(), asyncio.Event()

        @self.dp.slash_command()
        async def deferred(event):
            await event.defer("working")
            entered.set()
            await release.wait()
            await event.followup("finished")

        self.assertEqual(await self.post_slash(), (200, {"response_type": "ephemeral", "text": "working"}))
        await asyncio.wait_for(entered.wait(), 2)
        self.assertEqual(self.adapter.pending_deferred, 1)
        self.assertEqual((await self.post_slash(user_id="another-human"))[0], 503)
        release.set()
        async with asyncio.timeout(2):
            while self.adapter.pending_deferred:
                await asyncio.sleep(0.005)
        self.assertEqual(self.followups, [({"response_type": "ephemeral", "text": "finished"}, None, None)])
        self.assertEqual(self.adapter.background_errors, 0)

    async def test_shutdown_cancels_explicit_deferred_work_and_preserves_borrowed_resources(self):
        cancelled = asyncio.Event()

        @self.dp.slash_command()
        async def deferred(event):
            await event.defer()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.assertEqual((await self.post_slash())[0], 200)
        await self.adapter.shutdown()
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.adapter.pending_deferred, 0)
        self.assertEqual(self.dp._active_feeds, 0)
        self.assertFalse(self.bot.is_closed)
        self.assertFalse(self.dp._closed)

    async def test_delayed_url_origin_lifetime_and_five_attempt_budget(self):
        now = [1.0]
        sender = DelayedResponder(
            self.bot, SecretStr(self.base + "/hooks/commands/capability"), clock=lambda: now[0]
        )
        await asyncio.gather(
            *(sender({"text": str(index), "response_type": "ephemeral"}) for index in range(5))
        )
        with self.assertRaises(ResponseStateError):
            await sender({"text": "six"})
        self.assertEqual(len(self.followups), 5)
        self.assertTrue(all(auth is None and cookie is None for _, auth, cookie in self.followups))
        expired = DelayedResponder(
            self.bot, SecretStr(self.base + "/hooks/commands/expired"), clock=lambda: now[0]
        )
        now[0] += 1800
        with self.assertRaises(ResponseDeadlineError):
            await expired({"text": "too late"})
        for url in (
            "http://untrusted.test/hooks/commands/key",
            self.base + "/api/v4/users/me",
            self.base + "/hooks/commands/%2fother",
            self.base + "/hooks/commands/key?redirect=other",
        ):
            with self.assertRaises(ValueError):
                DelayedResponder(self.bot, SecretStr(url))

    async def test_delayed_http_error_and_redirect_are_not_retried(self):
        for mode, status in (("forbidden", 403), ("redirect", 302)):
            self.provider_mode = mode
            sender = DelayedResponder(self.bot, SecretStr(self.base + "/hooks/commands/capability"))
            with self.assertRaises(APIError) as caught:
                await sender({"text": "private"})
            self.assertEqual(caught.exception.status, status)
            self.assertEqual(sender.attempts, 1)
        self.assertEqual(len(self.followups), 2)

    async def test_unknown_followup_write_outcome_is_not_retried(self):
        self.provider_mode = "write-then-delay"
        bot = Bot(token="bot-secret", base_url=self.base, request_timeout=1)
        sender = DelayedResponder(bot, SecretStr(self.base + "/hooks/commands/capability"))
        task = asyncio.create_task(sender({"text": "already written"}))
        try:
            await asyncio.wait_for(self.provider_entered.wait(), 2)
            with self.assertRaises(RequestOutcomeUnknown):
                await task
            self.assertEqual(len(self.followups), 1)
            self.assertEqual(sender.attempts, 1)
        finally:
            self.provider_release.set()
            await bot.close()
            await asyncio.gather(task, return_exceptions=True)

    async def asgi_request(self, *, peer="127.0.0.1", disconnect=False):
        queue = asyncio.Queue()
        queue.put_nowait(
            {"type": "http.request", "body": urlencode(self.slash()).encode(), "more_body": False}
        )
        sent = []

        async def send(message):
            sent.append(message)

        app = ASGIAdapter(self.adapter)
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/mattergram/slash",
            "headers": [
                (b"content-type", b"application/x-www-form-urlencoded"),
                (b"x-forwarded-for", b"127.0.0.1"),
            ],
            "client": (peer, 1234),
            "query_string": b"",
        }
        task = asyncio.create_task(app(scope, queue.get, send))
        return task, queue, sent

    async def test_asgi_parity_and_forwarded_header_is_not_peer_authentication(self):
        @self.dp.slash_command()
        async def command(event):
            await event.answer("asgi")

        task, _, sent = await self.asgi_request()
        await task
        self.assertEqual(sent[0]["status"], 200)
        self.assertEqual(json.loads(sent[1]["body"]), {"response_type": "ephemeral", "text": "asgi"})
        task, _, rejected = await self.asgi_request(peer="192.0.2.4")
        await task
        self.assertEqual(rejected[0]["status"], 403)

    async def test_asgi_disconnect_cancels_handler_without_sending_success(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()

        @self.dp.slash_command()
        async def command(event):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        task, queue, sent = await self.asgi_request()
        await asyncio.wait_for(entered.wait(), 2)
        queue.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(task, 2)
        self.assertEqual(sent, [])
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.dp.events_isolation._locks, {})

    async def test_asgi_chunk_limit_and_duplicate_headers_reject_before_dispatch(self):
        calls = []

        @self.dp.slash_command()
        async def command(event):
            calls.append(event)

        app = ASGIAdapter(self.adapter)
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/mattergram/slash",
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
            "client": ("127.0.0.1", 1234),
        }
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate):
                queue, sent = asyncio.Queue(), []
                self.adapter.max_body_bytes = 8
                headers = scope["headers"] * (2 if duplicate else 1)
                queue.put_nowait({"type": "http.request", "body": b"12345", "more_body": True})
                queue.put_nowait({"type": "http.request", "body": b"67890", "more_body": False})

                async def send(message):
                    sent.append(message)

                await app({**scope, "headers": headers}, queue.get, send)
                self.assertEqual(sent[0]["status"], 400 if duplicate else 413)
        self.assertEqual(calls, [])
        self.assertEqual(self.adapter._handlers, set())

    async def test_asgi_lifespan_drains_adapter_and_borrows_bot_dispatcher(self):
        queue, sent = asyncio.Queue(), []
        queue.put_nowait({"type": "lifespan.startup"})
        queue.put_nowait({"type": "lifespan.shutdown"})

        async def send(message):
            sent.append(message)

        await ASGIAdapter(self.adapter)({"type": "lifespan"}, queue.get, send)
        self.assertEqual(
            [m["type"] for m in sent], ["lifespan.startup.complete", "lifespan.shutdown.complete"]
        )
        self.assertFalse(self.bot.is_closed)
        self.assertFalse(self.dp._closed)
        self.assertEqual((await self.post_slash())[0], 503)
