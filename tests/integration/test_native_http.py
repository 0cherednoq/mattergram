from __future__ import annotations

import asyncio
import json
import unittest

from aiohttp import web

from mattergram import Bot, ComposerButton, ComposerKeyboard
from mattergram.exceptions import (
    APIError,
    AuthenticationError,
    BotBindingError,
    ClientClosedError,
    PermissionDeniedError,
    ProtocolError,
    RateLimitError,
    RequestOutcomeUnknown,
    TransportError,
    UnsupportedCapabilityError,
)
from mattergram.http import CallbackSigner
from mattergram.normalizers import normalize_posted
from mattergram.types import Message
from mattergram.ui import UIConversionError, UIProfile, from_adaptive_cards, from_slack_blocks
from mattergram.ui.actions import ActionContext, Actions, Attachment, External
from mattergram.ui.actions import Button as LegacyButton
from mattergram.ui.blocks import Text


class NativeHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_route_id_rejected_before_write_and_preserved_on_newer_server(self):
        bot = self.bot()
        group = Actions(
            items=(
                LegacyButton(
                    id="native-ID_1", name="Run", action=External(url="https://integration.test/callback")
                ),
            )
        )
        for shape in ({"actions": group}, {"attachments": (Attachment(actions=group),)}):
            for target in (None, UIProfile("11.7.0")):
                with self.subTest(shape=tuple(shape), target=target):
                    with self.assertRaises(UnsupportedCapabilityError):
                        await bot.send_message("channel-real", "Menu", ui_profile=target, **shape)
        self.assertEqual(self.calls, [])
        await bot.send_message("channel-real", "Menu", actions=group, ui_profile=UIProfile("11.11.0"))
        wire = self.calls[-1][3]
        self.assertEqual(wire["props"]["attachments"][0]["actions"][0]["id"], "native-ID_1")

    async def test_imported_ui_uses_native_wire_and_signed_registry_with_notification_fallback(self):
        bot = self.bot()
        signer = CallbackSigner(
            instance_id=bot.instance_id, bot_id="bot-real", keys={"one": b"k" * 32}, active_key_id="one"
        )
        action = External(url="https://integration.test/callback", data="task:run")
        imported = [
            from_slack_blocks(
                [
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "action_id": "run",
                                "text": {"type": "plain_text", "text": "Run"},
                                "value": "task:run",
                            }
                        ],
                    }
                ],
                actions={"run": action},
            ),
            from_adaptive_cards(
                [
                    {
                        "type": "AdaptiveCard",
                        "version": "1.5",
                        "actions": [
                            {"type": "Action.Submit", "id": "run", "title": "Run", "data": "task:run"}
                        ],
                    }
                ],
                actions={"run": action},
            ),
        ]
        target = UIProfile(
            "12.0.2", client="test", client_features=frozenset({"blocks"}), mm_blocks_enabled=True
        )
        for blocks in imported:
            with self.subTest(source=blocks):
                await bot.send_message(
                    "channel-real",
                    "Notification fallback",
                    blocks=blocks,
                    ui_profile=target,
                    action_context=ActionContext(signer, "channel-real"),
                )
                wire = self.calls[-1][3]
                self.assertEqual(wire["message"], "Notification fallback")
                self.assertEqual(set(wire["props"]), {"mm_blocks", "mm_blocks_actions"})
                self.assertEqual(wire["props"]["mm_blocks"][0]["content"][0]["action_id"], "run")
                verified = signer.verify(
                    {
                        "channel_id": "channel-real",
                        "context": wire["props"]["mm_blocks_actions"]["run"]["context"],
                    }
                )
                self.assertEqual(verified["data"], "task:run")

    async def test_input_conversion_errors_and_missing_capabilities_make_no_http_write(self):
        bot = self.bot()
        with self.assertRaises(UIConversionError):
            await bot.send_message(
                "channel",
                "Fallback",
                blocks=from_adaptive_cards(
                    [
                        {
                            "type": "AdaptiveCard",
                            "version": "1.5",
                            "body": [{"type": "Input.Text", "id": "name"}],
                        }
                    ]
                ),
            )
        with self.assertRaises(UnsupportedCapabilityError):
            await bot.send_message(
                "channel",
                "Fallback",
                blocks=from_slack_blocks([{"type": "divider"}]),
                ui_profile=UIProfile("12.0.2"),
            )
        self.assertEqual(self.calls, [])

    async def test_ui_is_compiled_before_write_with_signed_native_action_context(self):
        bot = self.bot()
        signer = CallbackSigner(
            instance_id=bot.instance_id, bot_id="bot-real", keys={"one": b"k" * 32}, active_key_id="one"
        )
        await bot.send_message(
            "channel-real",
            "Menu",
            actions=Actions(
                items=[
                    LegacyButton(
                        name="Run", action=External(url="https://integration.test/callback", data="task:run")
                    )
                ]
            ),
            action_context=ActionContext(signer, "channel-real"),
        )
        wire = self.calls[-1][3]
        integration = wire["props"]["attachments"][0]["actions"][0]["integration"]
        self.assertEqual(integration["url"], "https://integration.test/callback")
        verified = signer.verify({"channel_id": "channel-real", "context": integration["context"]})
        self.assertEqual(verified["data"], "task:run")
        self.assertEqual(wire["message"], "Menu")

    async def test_unsupported_or_misbound_ui_makes_no_http_write(self):
        bot = self.bot()
        for target in (None, UIProfile("12.0.2")):
            with self.subTest(profile=target):
                with self.assertRaises((ValueError, UnsupportedCapabilityError)):
                    await bot.send_message(
                        "channel", "Text", blocks=[Text(text="Advanced UI")], ui_profile=target
                    )
        signer = CallbackSigner(
            instance_id=bot.instance_id, bot_id="bot-real", keys={"one": b"k" * 32}, active_key_id="one"
        )
        with self.assertRaises(ValueError):
            await bot.send_message("channel", "Text", action_context=ActionContext(signer, "another-channel"))
        self.assertEqual(self.calls, [])

    async def asyncSetUp(self) -> None:
        self.calls = []
        self.created = []
        self.mode = "normal"
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        app = web.Application()
        app.router.add_route(
            "*",
            "/mattermost/plugins/com.mattergram.menu/api/v1/channels/{channel_id}/keyboard",
            self.handle_plugin,
        )
        app.router.add_get(
            "/mattermost/plugins/com.mattergram.menu/api/v1/keyboards",
            self.handle_plugin_list,
        )
        app.router.add_route("*", "/mattermost/api/v4/{tail:.*}", self.handle)
        self.runner = web.AppRunner(app, shutdown_timeout=0.1)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.base = f"http://127.0.0.1:{self.runner.addresses[0][1]}/mattermost"
        self.bots = []
        self.keyboard_state = None

    async def asyncTearDown(self) -> None:
        self.release.set()
        for bot in self.bots:
            await bot.close()
        await self.runner.cleanup()

    def bot(self, **kwargs):
        instance = Bot(token=kwargs.pop("token", "fixture-secret"), base_url=self.base, **kwargs)
        self.bots.append(instance)
        return instance

    async def handle(self, request):
        body = await request.json() if request.method == "POST" else None
        self.calls.append((request.method, request.path, request.headers.get("Authorization"), body))
        if request.headers.get("Authorization") != "Bearer fixture-secret":
            return web.json_response(
                {"id": "api.context.invalid_token", "request_id": "request-401"}, status=401
            )
        if self.mode == "forbidden" and request.method == "POST":
            return web.json_response(
                {"id": "api.post.create_post.permissions", "message": "fixture-secret must never be logged"},
                status=403,
                headers={"X-Request-ID": "request-403"},
            )
        if self.mode == "rate-limit":
            return web.json_response({"id": "api.rate_limit"}, status=429, headers={"Retry-After": "2"})
        if self.mode == "echo-secret":
            return web.json_response(
                {"id": "echo.fixture-secret"}, status=403, headers={"X-Request-ID": "request.fixture-secret"}
            )
        if self.mode == "error-html":
            return web.Response(
                text="<h1>proxy error fixture-secret</h1>", content_type="text/html", status=502
            )
        if self.mode == "redirect":
            return web.Response(status=302, headers={"Location": self.base + "/api/v4/redirect-target"})
        if self.mode == "bad-identity" and request.method == "GET":
            return web.json_response({"id": 123, "username": "fixture-secret"})
        if self.mode == "delay-identity" and request.method == "GET":
            self.entered.set()
            await self.release.wait()
        if request.method == "GET" and request.match_info["tail"] == "users/me":
            return web.json_response({"id": "bot-real", "username": "bot", "future_user_field": True})
        if request.method == "POST" and request.match_info["tail"] == "posts":
            if self.mode == "disconnect":
                request.transport.close()
                return web.Response()
            if self.mode == "malformed":
                return web.Response(
                    text="not-json fixture-secret", content_type="application/json", status=201
                )
            if self.mode == "wrong-type":
                return web.json_response(["not-an-object"], status=201)
            if self.mode == "invalid-post":
                return web.json_response({"id": 123}, status=201)
            created = {
                "id": f"post-{len(self.created) + 1}",
                "channel_id": body["channel_id"],
                "user_id": "bot-real",
                "message": body["message"],
                "root_id": body.get("root_id", ""),
                "create_at": 1700000000123,
                "props": {"server": "kept"},
            }
            self.created.append(created)
            if self.mode == "write-then-delay":
                self.entered.set()
                await self.release.wait()
            return web.json_response(created, status=201)
        return web.json_response({"id": "fixture.unsupported_endpoint"}, status=501)

    async def handle_plugin(self, request):
        body = await request.json() if request.method == "PUT" else None
        self.calls.append((request.method, request.path, request.headers.get("Authorization"), body))
        if request.headers.get("Authorization") != "Bearer fixture-secret":
            return web.json_response({"id": "api.context.invalid_token"}, status=401)
        channel_id = request.match_info["channel_id"]
        if request.method == "PUT":
            revision = 1 if self.keyboard_state is None else self.keyboard_state["revision"] + 1
            self.keyboard_state = {
                "channel_id": channel_id,
                "revision": revision,
                "owner_id": "b" * 26,
                "keyboard": body,
            }
            return web.json_response(self.keyboard_state)
        if request.method == "GET":
            return web.json_response(self.keyboard_state or {"keyboard": None})
        if request.method == "DELETE":
            self.keyboard_state = None
            return web.json_response({"status": "OK"})
        return web.json_response({"id": "method_not_allowed"}, status=405)

    async def handle_plugin_list(self, request):
        self.calls.append((request.method, request.path, request.headers.get("Authorization"), None))
        if request.headers.get("Authorization") != "Bearer fixture-secret":
            return web.json_response({"id": "api.context.invalid_token"}, status=401)
        return web.json_response({"keyboards": [] if self.keyboard_state is None else [self.keyboard_state]})

    async def test_plugin_backed_composer_keyboard_lifecycle(self) -> None:
        bot = self.bot()
        keyboard = ComposerKeyboard(
            title="Admin",
            rows=[
                [
                    ComposerButton(id="accounts", text="Accounts", message="/accounts", style="primary"),
                    ComposerButton(id="imports", text="Imports", message="/imports"),
                ]
            ],
        )
        self.assertIsNone(await bot.get_composer_keyboard("channel1"))
        created = await bot.set_composer_keyboard("channel1", keyboard)
        self.assertEqual(created.revision, 1)
        self.assertEqual(created.owner_id, "b" * 26)
        self.assertEqual(created.keyboard, keyboard)
        self.assertEqual((await bot.get_composer_keyboard("channel1")).keyboard.title, "Admin")
        self.assertEqual(await bot.list_composer_keyboards(), (created,))
        await bot.delete_composer_keyboard("channel1")
        self.assertIsNone(await bot.get_composer_keyboard("channel1"))
        self.assertEqual(
            [call[0] for call in self.calls],
            ["GET", "PUT", "GET", "GET", "DELETE", "GET"],
        )
        self.assertTrue(all("/plugins/com.mattergram.menu/" in call[1] for call in self.calls))

    async def test_composer_keyboard_rejects_wrong_model_before_http(self) -> None:
        with self.assertRaises(TypeError):
            await self.bot().set_composer_keyboard("channel1", {"rows": []})
        self.assertEqual(self.calls, [])

    async def test_real_http_get_me_create_post_and_subpath(self) -> None:
        bot = self.bot()
        async with bot:
            message = await bot.send_message("channel-real", "Привет, Mattermost 👋")
            self.assertEqual(bot.user_id, "bot-real")
            self.assertEqual(message.post_id, "post-1")
            self.assertEqual(message.raw["props"], {"server": "kept"})
        self.assertTrue(bot.is_closed)
        self.assertTrue(bot._session.closed)
        self.assertEqual(
            [call[:2] for call in self.calls],
            [("GET", "/mattermost/api/v4/users/me"), ("POST", "/mattermost/api/v4/posts")],
        )
        self.assertEqual(self.calls[1][3], {"channel_id": "channel-real", "message": "Привет, Mattermost 👋"})

    async def test_posted_answer_reply_thread_and_self_suppression(self) -> None:
        bot = self.bot()
        incoming = {
            "id": "original",
            "channel_id": "channel-real",
            "user_id": "human",
            "message": "ping",
            "create_at": 1700000000123,
            "root_id": "thread-root",
        }
        message = normalize_posted(
            {"event": "posted", "data": {"post": json.dumps(incoming)}}, bot=bot, self_user_id="bot-real"
        )
        answer = await message.answer("pong")
        reply = await message.reply("threaded pong")
        self.assertIsNone(answer.root_id)
        self.assertEqual(reply.root_id, "thread-root")
        self.assertNotIn("root_id", self.calls[0][3])
        self.assertEqual(self.calls[1][3]["root_id"], "thread-root")
        top_level = Message.from_wire({**incoming, "root_id": ""}, bot=bot)
        self.assertEqual((await top_level.reply("new thread")).root_id, "original")
        self.assertIsNone(
            normalize_posted(
                {"event": "posted", "data": {"post": json.dumps(self.created[0])}},
                bot=bot,
                self_user_id="bot-real",
            )
        )
        self.assertEqual(len(self.created), 3)

    async def test_wrong_token_closes_failed_context_and_redacts_error(self) -> None:
        bot = self.bot(token="invalid-secret")
        with self.assertRaises(AuthenticationError) as caught:
            async with bot:
                self.fail("invalid token entered context")
        self.assertEqual(caught.exception.status, 401)
        self.assertEqual(caught.exception.error_id, "api.context.invalid_token")
        self.assertEqual(caught.exception.request_id, "request-401")
        self.assertNotIn("invalid-secret", repr(caught.exception))
        self.assertTrue(bot.is_closed)
        self.assertTrue(bot._session.closed)

    async def test_forbidden_keeps_server_code_and_request_id_without_payload_logs(self) -> None:
        self.mode = "forbidden"
        with self.assertRaises(PermissionDeniedError) as caught:
            await self.bot().send_message("channel-real", "not allowed")
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(caught.exception.error_id, "api.post.create_post.permissions")
        self.assertEqual(caught.exception.request_id, "request-403")
        self.assertNotIn("fixture-secret", repr(caught.exception))
        self.assertEqual(len(self.calls), 1)

    async def test_rate_limit_preserves_header_without_automatic_retry(self) -> None:
        self.mode = "rate-limit"
        with self.assertRaises(RateLimitError) as caught:
            await self.bot().send_message("channel-real", "limited")
        self.assertEqual(caught.exception.retry_after, "2")
        self.assertEqual(len(self.calls), 1)

    async def test_non_json_error_preserves_known_status(self) -> None:
        self.mode = "error-html"
        with self.assertRaises(APIError) as caught:
            await self.bot().get_me()
        self.assertEqual(caught.exception.status, 502)
        self.assertNotIn("fixture-secret", repr(caught.exception))

    async def test_successful_but_malformed_post_writes_have_unknown_outcome(self) -> None:
        for mode in ("malformed", "wrong-type", "invalid-post"):
            self.mode = mode
            with self.subTest(mode=mode), self.assertRaises(RequestOutcomeUnknown) as caught:
                await self.bot().send_message("channel-real", "hello")
            self.assertNotIn("fixture-secret", repr(caught.exception))
        self.assertEqual(len(self.calls), 3)

    async def test_redirect_is_not_followed_with_credential(self) -> None:
        self.mode = "redirect"
        with self.assertRaises(APIError) as caught:
            await self.bot().get_me()
        self.assertEqual(caught.exception.status, 302)
        self.assertEqual(len(self.calls), 1)

    async def test_invalid_identity_closes_startup(self) -> None:
        self.mode = "bad-identity"
        bot = self.bot()
        with self.assertRaises(ProtocolError):
            async with bot:
                self.fail("invalid identity was accepted")
        self.assertTrue(bot._session.closed)

    async def test_timeout_after_write_is_unknown_and_never_retried(self) -> None:
        self.mode = "write-then-delay"
        bot = self.bot(request_timeout=1)
        # Establish the local connection first. A 50ms cold-start deadline could
        # expire before the server had written anything on a busy Windows host;
        # that does not establish the write-before-timeout scenario we need.
        await bot.get_me()
        self.calls.clear()
        task = asyncio.create_task(bot.send_message("channel-real", "may already exist"))
        try:
            await asyncio.wait_for(self.entered.wait(), 5)
            with self.assertRaises(RequestOutcomeUnknown):
                await task
            self.assertEqual(len(self.created), 1)
            self.assertEqual(len(self.calls), 1)
        finally:
            self.release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_disconnect_during_write_is_unknown_and_never_retried(self) -> None:
        self.mode = "disconnect"
        with self.assertRaises(RequestOutcomeUnknown):
            await self.bot().send_message("channel-real", "uncertain")
        self.assertEqual(len(self.calls), 1)

    async def test_cancellation_propagates_and_context_closes_session(self) -> None:
        self.mode = "write-then-delay"
        bot = self.bot()

        async def run():
            async with bot:
                await bot.send_message("channel-real", "cancel after write")

        task = asyncio.create_task(run())
        await self.entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(self.created), 1)
        self.assertTrue(bot.is_closed)
        self.assertTrue(bot._session.closed)

    async def test_cancelled_context_startup_also_closes_session(self) -> None:
        self.mode = "delay-identity"
        bot = self.bot()
        task = asyncio.create_task(bot.__aenter__())
        await self.entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(bot._session.closed)

    async def test_concurrency_reuses_pool_and_close_is_terminal(self) -> None:
        bot = self.bot()
        posts = await asyncio.gather(*(bot.send_message("channel-real", f"post {i}") for i in range(8)))
        session = bot._session
        self.assertEqual(len({message.post_id for message in posts}), 8)
        self.assertIs(bot._session, session)
        await asyncio.gather(bot.close(), bot.close())
        with self.assertRaises(ClientClosedError):
            await bot.send_message("channel-real", "closed")
        self.assertTrue(session.closed)
        self.assertEqual(len(self.calls), 8)

    async def test_unbound_shortcut_and_invalid_arguments_do_not_send_requests(self) -> None:
        message = Message.from_wire(
            {"id": "p", "channel_id": "c", "user_id": "u", "message": "", "create_at": 0}
        )
        with self.assertRaises(BotBindingError):
            await message.answer("no bot")
        bot = self.bot()
        for args in ((123, "text"), ("", "text"), ("c", 123)):
            with self.subTest(args=args), self.assertRaises((TypeError, ValueError)):
                await bot.send_message(*args)
        with self.assertRaises(TypeError):
            await bot.send_message("c", "text", unsupported=True)
        self.assertEqual(self.calls, [])

    async def test_concurrent_context_entry_does_not_share_resource_ownership(self) -> None:
        self.mode = "delay-identity"
        bot = self.bot()
        first = asyncio.create_task(bot.__aenter__())
        await self.entered.wait()
        with self.assertRaisesRegex(RuntimeError, "already active"):
            await bot.__aenter__()
        self.assertFalse(bot.is_closed)
        self.release.set()
        self.assertIs(await first, bot)
        await bot.__aexit__(None, None, None)
        self.assertTrue(bot.is_closed)
        self.assertEqual(len(self.calls), 1)

    async def test_close_during_post_keeps_unknown_outcome_without_retry(self) -> None:
        self.mode = "write-then-delay"
        bot = self.bot()
        task = asyncio.create_task(bot.send_message("c", "already written"))
        await self.entered.wait()
        await bot.close()
        with self.assertRaises(RequestOutcomeUnknown):
            await task
        self.assertEqual(len(self.created), 1)
        self.assertEqual(len(self.calls), 1)

    async def test_server_metadata_cannot_echo_the_bot_credential(self) -> None:
        self.mode = "echo-secret"
        with self.assertRaises(PermissionDeniedError) as caught:
            await self.bot().send_message("c", "hello")
        self.assertNotIn("fixture-secret", caught.exception.error_id)
        self.assertNotIn("fixture-secret", caught.exception.request_id)

    async def test_constructor_rejects_bad_configuration_without_creating_pool(self) -> None:
        for url in (
            "relative/path",
            "ftp://host",
            "http://user:password@host",
            "http://host?query=1",
            "http://host/#fragment",
            "http://host:invalid",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                Bot(token="fixture-secret", base_url=url)
        for timeout in (True, 0, -1, float("inf"), float("nan")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.bot(request_timeout=timeout)
        for token in ("", "secret with space", "secret\r\n", "secret\x00"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                self.bot(token=token)
        self.assertEqual(self.calls, [])

    async def test_connection_failure_is_not_a_successful_or_ambiguous_write(self) -> None:
        await self.runner.cleanup()
        with self.assertRaises(TransportError) as caught:
            await self.bot(request_timeout=0.2).send_message("c", "not connected")
        self.assertNotIsInstance(caught.exception, RequestOutcomeUnknown)


if __name__ == "__main__":
    unittest.main()
