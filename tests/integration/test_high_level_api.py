from __future__ import annotations

import asyncio
import unittest
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

from aiohttp import BodyPartReader, web

from mattergram import Bot
from mattergram.contrib.game_accounts_admin import MattermostPushDispatcher, PushRule
from mattergram.exceptions import APIError, RequestOutcomeUnknown
from mattergram.types import BufferedInputFile, InputFile, Message, MessagePriority


def post(
    *,
    message: str = "before",
    channel_id: str = "channel-real",
    file_ids: list[str] | None = None,
    delete_at: int = 0,
) -> dict[str, Any]:
    return {
        "id": "post-real",
        "channel_id": channel_id,
        "user_id": "bot-real",
        "message": message,
        "create_at": 1,
        "root_id": "",
        "file_ids": file_ids or [],
        "delete_at": delete_at,
    }


class HighLevelAPIHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.calls: list[tuple[str, str, object]] = []
        self.queries: list[dict[str, str]] = []
        self.mode = "normal"
        self.upload: dict[str, Any] | None = None
        app = web.Application()
        app.router.add_route("*", "/mattermost/api/v4/{tail:.*}", self.handle)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.base_url = f"http://127.0.0.1:{self.runner.addresses[0][1]}/mattermost"
        self.bot = Bot(token="fixture-secret", base_url=self.base_url)

    async def asyncTearDown(self) -> None:
        await self.bot.close()
        await self.runner.cleanup()

    async def handle(self, request: web.Request) -> web.StreamResponse:
        path = request.path.removeprefix("/mattermost/api/v4/")
        if request.method == "POST" and path == "files":
            reader = await request.multipart()
            fields: dict[str, Any] = {}
            files: list[dict[str, Any]] = []
            while part := await reader.next():
                if not isinstance(part, BodyPartReader):
                    raise AssertionError("Nested multipart is not expected")
                if part.name == "files":
                    files.append(
                        {
                            "filename": part.filename,
                            "content_type": part.headers.get("Content-Type"),
                            "data": await part.read(),
                        }
                    )
                else:
                    fields[part.name or ""] = await part.text()
            fields["files"] = files
            self.upload = fields
            self.calls.append((request.method, path, fields))
            if self.mode == "bad-upload":
                return web.json_response({"file_infos": []}, status=201)
            if self.mode == "partial-upload":
                files = files[:-1]
            ids = ["file-real"] if len(files) == 1 else [f"file-{index + 1}" for index in range(len(files))]
            if self.mode == "duplicate-upload":
                ids = ["file-duplicate"] * len(files)
            return web.json_response(
                {"file_infos": [{"id": file_id} for file_id in ids], "client_ids": []},
                status=201,
            )
        body: object = await request.json() if request.can_read_body else None
        self.calls.append((request.method, path, body))
        self.queries.append(dict(request.query))
        if request.method == "GET" and path in {"users/me", "users/user-real"}:
            return web.json_response(
                {"id": "bot-real" if path.endswith("me") else "user-real", "username": "user"}
            )
        if request.method == "GET" and path == "users":
            return web.json_response(
                [
                    {
                        "id": "team-user",
                        "username": "alice",
                        "first_name": "Alice",
                        "last_name": "Example",
                        "is_bot": False,
                        "delete_at": 0,
                    }
                ]
            )
        if request.method == "GET" and path == "channels/channel-real":
            return web.json_response({"id": "channel-real", "team_id": "team-real", "type": "O"})
        if request.method == "POST" and path == "channels":
            assert isinstance(body, dict)
            result = {"id": "channel-created", **body}
            if self.mode == "other-channel":
                result["name"] = "another-channel"
            return web.json_response(result, status=201)
        if request.method == "POST" and path == "channels/direct":
            if self.mode == "bad-direct":
                return web.json_response({"id": "channel-direct", "type": "O"}, status=201)
            return web.json_response({"id": "channel-direct", "type": "D"}, status=201)
        if request.method == "GET" and path == "posts/post-real":
            return web.json_response(post(delete_at=1 if self.mode == "deleted" else 0))
        if request.method == "PUT" and path == "posts/post-real/patch":
            if self.mode == "bad-patch":
                return web.json_response({"status": "OK"})
            assert isinstance(body, dict)
            return web.json_response(post(message=str(body["message"])))
        if request.method == "DELETE" and path == "posts/post-real":
            return web.json_response({"status": "MISSING" if self.mode == "bad-delete" else "OK"})
        if request.method == "POST" and path == "posts":
            assert isinstance(body, dict)
            if self.mode == "post-error":
                return web.json_response({"id": "app.post.create.app_error"}, status=500)
            if self.mode == "bad-post":
                return web.json_response({"status": "OK"}, status=201)
            if self.mode == "other-post-channel":
                result = post(message=str(body["message"]), file_ids=list(body.get("file_ids", [])))
                result["channel_id"] = "other-channel"
                return web.json_response(result, status=201)
            return web.json_response(
                post(
                    message=str(body["message"]),
                    channel_id=str(body["channel_id"]),
                    file_ids=list(body.get("file_ids", [])),
                ),
                status=201,
            )
        raise AssertionError(f"Unexpected request: {request.method} {path}")

    async def test_typed_reads_and_direct_channel_use_runtime_identity(self) -> None:
        user = await self.bot.get_user("user-real")
        team_users = await self.bot.get_users_in_team("team-real", page=2, per_page=50)
        channel = await self.bot.get_channel("channel-real")
        direct = await self.bot.create_direct_channel("user-real")
        self.assertEqual(user.id, "user-real")
        self.assertEqual([member.id for member in team_users], ["team-user"])
        self.assertIn(
            {
                "in_team": "team-real",
                "page": "2",
                "per_page": "50",
                "active": "true",
            },
            self.queries,
        )
        self.assertEqual(channel.id, "channel-real")
        self.assertEqual(direct.id, "channel-direct")
        self.assertEqual(self.calls[-1], ("POST", "channels/direct", ["bot-real", "user-real"]))
        self.mode = "bad-direct"
        with self.assertRaises(RequestOutcomeUnknown):
            await self.bot.create_direct_channel("user-real")

    async def test_bot_creates_typed_channel_and_rejects_unconfirmed_identity(self) -> None:
        channel = await self.bot.create_channel(
            team_id="team-real",
            name="game-alerts",
            display_name="Game alerts",
            purpose="Automated account alerts",
        )

        self.assertEqual(channel.id, "channel-created")
        self.assertEqual(channel.team_id, "team-real")
        self.assertEqual(channel.type, "O")
        self.assertIn(
            (
                "POST",
                "channels",
                {
                    "team_id": "team-real",
                    "name": "game-alerts",
                    "display_name": "Game alerts",
                    "type": "O",
                    "purpose": "Automated account alerts",
                    "header": "",
                },
            ),
            self.calls,
        )

        self.mode = "other-channel"
        with self.assertRaises(RequestOutcomeUnknown):
            await self.bot.create_channel(
                team_id="team-real",
                name="game-alerts-2",
                display_name="Other alerts",
            )

    async def test_game_admin_push_mentions_channel_recipient_and_is_not_silent(self) -> None:
        dispatcher = MattermostPushDispatcher(
            self.bot,
            mentions=("alice",),
            priority=MessagePriority("important"),
        )
        await dispatcher.send(
            PushRule(
                id="push-1",
                destination_kind="channel",
                destination_id="channel-real",
                events=("account.invalid",),
            ),
            "account.invalid",
            "Account alice requires attention",
        )

        method, path, body = self.calls[-1]
        self.assertEqual((method, path), ("POST", "posts"))
        self.assertEqual(self.queries[-1], {})
        self.assertEqual(
            body,
            {
                "channel_id": "channel-real",
                "message": "@alice\n[account.invalid] Account alice requires attention",
                "metadata": {
                    "priority": {
                        "priority": "important",
                        "requested_ack": False,
                        "persistent_notifications": False,
                    }
                },
            },
        )

    async def test_edit_delete_and_message_shortcuts_preserve_channel_binding(self) -> None:
        message = Message.from_wire(post(), bot=self.bot)
        edited = await message.edit_text("after")
        self.assertEqual(edited.text, "after")
        await message.delete()
        writes = [(method, path) for method, path, _ in self.calls if method != "GET"]
        self.assertEqual(writes, [("PUT", "posts/post-real/patch"), ("DELETE", "posts/post-real")])
        with self.assertRaisesRegex(ValueError, "another channel"):
            await self.bot.edit_message_text("other-channel", "post-real", "no write")
        self.mode = "bad-patch"
        with self.assertRaises(RequestOutcomeUnknown):
            await self.bot.edit_message_text("channel-real", "post-real", "uncertain")
        self.mode = "bad-delete"
        with self.assertRaises(RequestOutcomeUnknown):
            await self.bot.delete_message("channel-real", "post-real")

    async def test_edit_can_replace_props_without_changing_the_post_identity(self) -> None:
        edited = await self.bot.edit_message_text(
            "channel-real",
            "post-real",
            "after",
            props={"attachments": []},
        )
        self.assertEqual(edited.post_id, "post-real")
        method, path, body = self.calls[-1]
        self.assertEqual((method, path), ("PUT", "posts/post-real/patch"))
        self.assertEqual(body, {"id": "post-real", "message": "after", "props": {"attachments": []}})

    async def test_soft_deleted_message_is_not_editable(self) -> None:
        self.mode = "deleted"

        with self.assertRaises(APIError) as caught:
            await self.bot.edit_message_text("channel-real", "post-real", "after")

        self.assertEqual(caught.exception.status, 404)
        self.assertFalse(any(method == "PUT" for method, _, _ in self.calls))

    async def test_send_document_streams_upload_then_attaches_exact_file(self) -> None:
        message = await self.bot.send_document(
            "channel-real",
            BufferedInputFile(b"abcdef", "report.txt", chunk_size=2),
            text="Report",
            root_id="root-real",
        )
        self.assertEqual(message.text, "Report")
        self.assertEqual(message.raw["file_ids"], ["file-real"])
        self.assertEqual(
            self.upload,
            {
                "channel_id": "channel-real",
                "files": [
                    {
                        "filename": "report.txt",
                        "content_type": "text/plain",
                        "data": b"abcdef",
                    }
                ],
            },
        )
        post_call = self.calls[-1]
        self.assertEqual(post_call[0:2], ("POST", "posts"))
        self.assertEqual(
            post_call[2],
            {
                "channel_id": "channel-real",
                "message": "Report",
                "file_ids": ["file-real"],
                "root_id": "root-real",
            },
        )
        self.mode = "bad-upload"
        with self.assertRaises(RequestOutcomeUnknown):
            await self.bot.send_document("channel-real", BufferedInputFile(b"again", "report.txt"))

    async def test_send_message_maps_priority_silent_and_existing_file_ids_exactly(self) -> None:
        important = MessagePriority(level="important", requested_ack=True)
        await self.bot.send_message(
            "channel-real",
            "Acknowledge",
            priority=important,
            file_ids=("file-a", "file-b", "file-c", "file-d", "file-e"),
        )
        self.assertEqual(self.queries[-1], {})
        self.assertEqual(
            self.calls[-1],
            (
                "POST",
                "posts",
                {
                    "channel_id": "channel-real",
                    "message": "Acknowledge",
                    "metadata": {
                        "priority": {
                            "priority": "important",
                            "requested_ack": True,
                            "persistent_notifications": False,
                        }
                    },
                    "file_ids": ["file-a", "file-b", "file-c", "file-d", "file-e"],
                },
            ),
        )

        urgent = MessagePriority(
            level="urgent",
            requested_ack=True,
            persistent_notifications=True,
        )
        await self.bot.send_message("channel-real", "@alice urgent", priority=urgent)
        self.assertEqual(self.queries[-1], {})
        self.assertEqual(
            self.calls[-1][2],
            {
                "channel_id": "channel-real",
                "message": "@alice urgent",
                "metadata": {
                    "priority": {
                        "priority": "urgent",
                        "requested_ack": True,
                        "persistent_notifications": True,
                    }
                },
            },
        )

        await self.bot.send_message("channel-real", "quiet", silent=True, file_ids=())
        self.assertEqual(self.queries[-1], {"silent": "true"})
        self.assertEqual(
            self.calls[-1][2],
            {"channel_id": "channel-real", "message": "quiet"},
        )

    async def test_send_message_rejects_invalid_delivery_options_before_network(self) -> None:
        urgent = MessagePriority(level="urgent", persistent_notifications=True)
        invalid_calls: list[Callable[[], Awaitable[Message]]] = [
            lambda: self.bot.send_message("channel-real", "reply", root_id="root", priority=urgent),
            lambda: self.bot.send_message("channel-real", "quiet", priority=urgent, silent=True),
            lambda: self.bot.send_message("channel-real", "bad", silent=1),  # type: ignore[arg-type]
            lambda: self.bot.send_message("channel-real", "bad", file_ids="file-a"),
            lambda: self.bot.send_message("channel-real", "bad", file_ids=("",)),
            lambda: self.bot.send_message(
                "channel-real",
                "bad",
                file_ids=("one", "two", "three", "four", "five", "six"),
            ),
        ]
        for call in invalid_calls:
            before = len(self.calls)
            with self.subTest(call=call), self.assertRaises((TypeError, ValueError)):
                await call()
            self.assertEqual(len(self.calls), before)

    async def test_send_message_treats_malformed_or_mismatched_success_as_unknown(self) -> None:
        for mode in ("bad-post", "other-post-channel"):
            self.mode = mode
            with self.subTest(mode=mode), self.assertRaises(RequestOutcomeUnknown):
                await self.bot.send_message("channel-real", "write once")

    async def test_send_documents_uploads_five_files_once_and_preserves_order(self) -> None:
        documents = [BufferedInputFile(str(index).encode(), f"report-{index}.txt") for index in range(1, 6)]
        message = await self.bot.send_documents(
            "channel-real",
            documents,
            text="Batch",
            priority=MessagePriority(level="important", requested_ack=True),
            silent=True,
        )
        self.assertEqual(message.raw["file_ids"], ["file-1", "file-2", "file-3", "file-4", "file-5"])
        self.assertIsNotNone(self.upload)
        assert self.upload is not None
        self.assertEqual(
            [item["filename"] for item in self.upload["files"]],
            [f"report-{index}.txt" for index in range(1, 6)],
        )
        self.assertEqual([item["data"] for item in self.upload["files"]], [b"1", b"2", b"3", b"4", b"5"])
        self.assertEqual(len([call for call in self.calls if call[1] == "files"]), 1)
        self.assertEqual(self.queries[-1], {"silent": "true"})
        self.assertEqual(
            self.calls[-1][2],
            {
                "channel_id": "channel-real",
                "message": "Batch",
                "file_ids": ["file-1", "file-2", "file-3", "file-4", "file-5"],
                "metadata": {
                    "priority": {
                        "priority": "important",
                        "requested_ack": True,
                        "persistent_notifications": False,
                    }
                },
            },
        )

    async def test_send_documents_rejects_invalid_batches_before_network(self) -> None:
        document = BufferedInputFile(b"one", "one.txt")
        batches: list[object] = [[], [document] * 6, [document, object()], "not-documents"]
        for batch in batches:
            before = len(self.calls)
            with self.subTest(batch=batch), self.assertRaises((TypeError, ValueError)):
                await self.bot.send_documents("channel-real", batch)  # type: ignore[arg-type]
            self.assertEqual(len(self.calls), before)

    async def test_send_documents_reports_partial_upload_and_all_unattached_ids(self) -> None:
        documents = [BufferedInputFile(b"x", f"{index}.txt") for index in range(5)]
        self.mode = "partial-upload"
        with self.assertRaises(RequestOutcomeUnknown):
            await self.bot.send_documents("channel-real", documents)

        self.mode = "duplicate-upload"
        with self.assertRaises(RequestOutcomeUnknown):
            await self.bot.send_documents("channel-real", documents)

        self.mode = "post-error"
        post_writes = len([call for call in self.calls if call[1] == "posts"])
        with self.assertRaises(APIError) as caught:
            await self.bot.send_documents("channel-real", documents)
        self.assertEqual(len([call for call in self.calls if call[1] == "posts"]), post_writes + 1)
        self.assertEqual(
            caught.exception.__notes__,
            [f"Uploaded native file 'file-{index}' may remain unattached" for index in range(1, 6)],
        )

        self.mode = "bad-post"
        post_writes = len([call for call in self.calls if call[1] == "posts"])
        with self.assertRaises(RequestOutcomeUnknown) as unknown:
            await self.bot.send_documents("channel-real", documents)
        self.assertEqual(len([call for call in self.calls if call[1] == "posts"]), post_writes + 1)
        self.assertEqual(
            unknown.exception.__notes__,
            [f"Uploaded native file 'file-{index}' may remain unattached" for index in range(1, 6)],
        )

    async def test_send_documents_cancellation_closes_the_active_upload_stream(self) -> None:
        started = asyncio.Event()
        closed = asyncio.Event()

        class BlockingInputFile(InputFile):
            async def read(self) -> AsyncGenerator[bytes, None]:
                try:
                    started.set()
                    yield b"first"
                    await asyncio.Event().wait()
                finally:
                    closed.set()

        task = asyncio.create_task(
            self.bot.send_documents("channel-real", [BlockingInputFile("blocked.bin")])
        )
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(closed.wait(), 1)


if __name__ == "__main__":
    unittest.main()
