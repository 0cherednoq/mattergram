from __future__ import annotations

import asyncio
import unittest

from aiohttp import web

from mattergram import Bot
from mattergram.exceptions import (
    APIError,
    AuthenticationError,
    ClientClosedError,
    PermissionDeniedError,
    ProtocolError,
    RateLimitError,
    RequestOutcomeUnknown,
    TransportError,
)
from mattergram.provisioning import CommandSpec, SetupClient


class SetupHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.commands = {}
        self.mode = "normal"
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        app = web.Application()
        app.router.add_route("*", "/deployment/api/v4/{tail:.*}", self.receive)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.base = "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1]) + "/deployment"
        self.clients = []

    async def asyncTearDown(self):
        self.release.set()
        for client in self.clients:
            await client.close()
        await self.runner.cleanup()

    def client(self, **options):
        client = SetupClient(token=options.pop("token", "setup-secret"), base_url=self.base, **options)
        self.clients.append(client)
        return client

    async def receive(self, request):
        body = await request.json() if request.can_read_body else None
        path = request.match_info["tail"]
        self.calls.append((request.method, path, dict(request.query), dict(request.headers), body))
        if request.headers.get("Authorization") not in {"Bearer setup-secret", "Bearer runtime-secret"}:
            return web.json_response({"id": "native.invalid_token"}, status=401)
        if self.mode in {"forbidden", "limited", "redirect", "html-error"}:
            if self.mode == "html-error":
                return web.Response(status=502, text="private setup-secret", content_type="text/html")
            status = {"forbidden": 403, "limited": 429, "redirect": 302}[self.mode]
            return web.json_response(
                {"id": "native.setup-secret"},
                status=status,
                headers={
                    "Retry-After": "3",
                    "Location": "/must-not-follow",
                    "Set-Cookie": "setup=private; Path=/",
                },
            )
        if request.method == "GET":
            if path == "users/me":
                return web.json_response(
                    {
                        "id": "admin" if request.headers["Authorization"].endswith("setup-secret") else "bot",
                        "username": "fixture",
                    }
                )
            if path.startswith("commands/"):
                return web.json_response(self.commands.get(path.split("/")[1], {}))
            if path == "commands":
                if self.mode == "bad-list":
                    return web.json_response({"message": "private setup-secret"})
                return web.json_response([{"trigger": "builtin"}, *self.commands.values()])
            if path.startswith("bots/"):
                return web.json_response({"user_id": "bot", "username": "ownedbot", "owner_id": "admin"})
            if path.startswith("users/"):
                return web.json_response({"id": "bot", "username": "ownedbot"})
        if path == "commands" and request.method == "POST":
            result = {**body, "id": "nativecommand", "creator_id": "admin", "token": "issued-command-secret"}
            self.commands[result["id"]] = result
            status = 201
        elif path.startswith("commands/") and request.method == "PUT":
            result = {**self.commands.get("nativecommand", {}), **body}
            self.commands["nativecommand"] = result
            status = 200
        elif path.startswith("commands/") and request.method == "DELETE":
            self.commands.pop(path.split("/")[1], None)
            result, status = {"status": "OK"}, 200
        elif path == "bots":
            result, status = {"user_id": "bot", "owner_id": "admin", **body}, 201
        elif path == "users/bot/tokens":
            result, status = (
                {
                    "id": "nativepat",
                    "user_id": "bot",
                    "token": "issued-runtime-secret",
                    "is_active": True,
                    **body,
                },
                200,
            )
        elif path == "users/tokens/revoke":
            result, status = {"status": "OK"}, 200
        else:
            return web.json_response({"id": "fixture.not_modeled"}, status=501)
        if self.mode == "bad-write":
            if path == "commands":
                return web.json_response(
                    {"trigger": body["trigger"], "team_id": body["team_id"]}, status=status
                )
            return web.json_response({"unexpected": "issued-runtime-secret"}, status=status)
        if self.mode == "wrong-token-user":
            return web.json_response({**result, "user_id": "other-user"}, status=status)
        if self.mode == "write-delay":
            self.entered.set()
            await self.release.wait()
        if self.mode == "disconnect":
            request.transport.abort()
            return web.Response()
        return web.json_response(result, status=status)

    async def test_command_crud_list_and_builtin_preserve_native_shape(self):
        client = self.client()
        async with client:
            self.assertEqual(client.user_id, "admin")
            created = await client.create_command(
                CommandSpec("help", hint="[topic]"), team_id="team", url="https://callback.test/slash"
            )
            self.assertEqual(created.token.get_secret_value(), "issued-command-secret")
            self.assertEqual((await client.get_command(created.id)).id, created.id)
            updated = await client.update_command(
                created.id,
                CommandSpec("help", hint="[changed]"),
                team_id="team",
                url="https://callback.test/new",
            )
            self.assertEqual(updated.auto_complete_hint, "[changed]")
            listed = await client.list_commands("team")
            self.assertEqual(
                [(c.id, c.trigger) for c in listed], [("", "builtin"), ("nativecommand", "help")]
            )
            self.assertEqual(self.calls[-1][2], {"team_id": "team", "custom_only": "false"})
            await client.delete_command(created.id)
            self.assertEqual(self.commands, {})
        self.assertTrue(client.is_closed)
        self.assertTrue(client._session.closed)
        self.assertTrue(all(call[3]["Authorization"] == "Bearer setup-secret" for call in self.calls))

    async def test_bot_and_native_200_pat_response_redact_secrets(self):
        client = self.client()
        bot = await client.create_bot(username="ownedbot")
        self.assertEqual((await client.get_bot(bot.user_id)).owner_id, "admin")
        self.assertEqual((await client.get_user_by_username("ownedbot")).id, bot.user_id)
        token = await client.issue_access_token(bot.user_id, description="Owned application")
        self.assertEqual(token.token.get_secret_value(), "issued-runtime-secret")
        self.assertNotIn("issued-runtime-secret", repr(token))
        self.assertNotIn("token", token.model_dump())
        await client.revoke_access_token(token.id)
        self.assertEqual(self.calls[-1][-1], {"token_id": "nativepat"})

    async def test_runtime_and_setup_credentials_pools_and_lifecycle_are_separate(self):
        setup = self.client()
        runtime = Bot(token="runtime-secret", base_url=self.base)
        self.clients.append(runtime)
        await setup.get_me()
        await runtime.get_me()
        self.assertNotIsInstance(setup, Bot)
        self.assertIsNot(setup._session, runtime._session)
        await setup.close()
        self.assertFalse(runtime.is_closed)
        self.assertEqual((await runtime.get_me()).id, "bot")
        self.assertFalse(hasattr(runtime, "create_bot"))
        self.assertFalse(hasattr(setup, "send_message"))

    async def test_error_codes_no_redirect_no_retry_and_no_cookie_reuse(self):
        client = self.client()
        for mode, expected in (
            ("forbidden", PermissionDeniedError),
            ("limited", RateLimitError),
            ("redirect", APIError),
            ("html-error", APIError),
        ):
            self.mode = mode
            before = len(self.calls)
            with self.subTest(mode=mode), self.assertRaises(expected) as caught:
                await client.get_bot("bot")
            self.assertEqual(len(self.calls), before + 1)
            self.assertNotIn("setup-secret", str(caught.exception))
            self.assertNotIn("setup-secret", caught.exception.error_id or "")
            if mode == "limited":
                self.assertEqual(caught.exception.retry_after, "3")
        self.mode = "normal"
        await client.get_me()
        self.assertNotIn("Cookie", self.calls[-1][3])

    async def test_bad_list_is_protocol_error_without_raw_details(self):
        self.mode = "bad-list"
        with self.assertRaises(ProtocolError) as caught:
            await self.client().list_commands("team")
        self.assertNotIn("setup-secret", str(caught.exception))

    async def test_malformed_success_write_is_uncertain_and_not_retried(self):
        self.mode = "bad-write"
        client = self.client()
        with self.assertRaises(RequestOutcomeUnknown) as caught:
            await client.issue_access_token("bot", description="Owned")
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("issued-runtime-secret", str(caught.exception))
        with self.assertRaises(RequestOutcomeUnknown):
            await client.create_command(
                CommandSpec("help"), team_id="team", url="https://callback.test/slash"
            )
        self.mode = "wrong-token-user"
        with self.assertRaises(RequestOutcomeUnknown):
            await client.issue_access_token("bot", description="Owned")
        self.assertEqual(len(self.calls), 3)

    async def test_put_and_delete_write_then_timeout_are_unknown_without_retry(self):
        client = self.client(request_timeout=1)
        await client.create_command(CommandSpec("help"), team_id="team", url="https://callback.test/slash")
        for method in ("PUT", "DELETE", "PATCH"):
            self.mode = "write-delay"
            self.entered.clear()
            self.release.clear()
            before = len(self.calls)
            if method == "PUT":
                operation = client.update_command(
                    "nativecommand",
                    CommandSpec("help", hint="changed"),
                    team_id="team",
                    url="https://callback.test/slash",
                )
            elif method == "DELETE":
                operation = client.delete_command("nativecommand")
            else:
                # Exercise the shared write transport, including future PATCH operations.
                operation = client._request("PATCH", "bots", 201, {"username": "ownedbot"})
            task = asyncio.create_task(operation)
            try:
                await asyncio.wait_for(self.entered.wait(), 5)
                with self.subTest(method=method), self.assertRaises(RequestOutcomeUnknown):
                    await task
                self.assertEqual(len(self.calls), before + 1)
                if method == "PUT":
                    self.assertEqual(self.commands["nativecommand"]["auto_complete_hint"], "changed")
                elif method == "DELETE":
                    self.assertEqual(self.commands, {})
            finally:
                self.release.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_disconnect_during_write_is_unknown(self):
        self.mode = "disconnect"
        with self.assertRaises(RequestOutcomeUnknown):
            await self.client().create_bot(username="ownedbot")
        self.assertEqual(len(self.calls), 1)

    async def test_cancellation_propagates_and_context_closes(self):
        client = self.client()
        self.mode = "write-delay"

        async def run():
            async with client:
                await client.create_bot(username="ownedbot")

        task = asyncio.create_task(run())
        await asyncio.wait_for(self.entered.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(client.is_closed)
        self.assertEqual(len(self.calls), 2)

    async def test_bad_credential_closes_failed_context_and_close_is_terminal(self):
        client = self.client(token="rejected-secret")
        with self.assertRaises(AuthenticationError):
            async with client:
                self.fail("Rejected setup identity accepted")
        self.assertTrue(client.is_closed)
        with self.assertRaises(ClientClosedError):
            await client.get_me()

    async def test_paths_and_local_declarations_reject_before_http(self):
        client = self.client()
        for value in ("", "..", "x/y", "x%2fy", "x?y", "x#y", "x\x00y"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await client.get_bot(value)
        with self.assertRaises(ValueError):
            await client.issue_access_token("bot", description="")
        self.assertEqual(self.calls, [])

    async def test_unreachable_is_transport_error_before_any_send(self):
        await self.runner.cleanup()
        with self.assertRaises(TransportError) as caught:
            await self.client().create_bot(username="ownedbot")
        self.assertNotIsInstance(caught.exception, RequestOutcomeUnknown)
        self.assertEqual(self.calls, [])
