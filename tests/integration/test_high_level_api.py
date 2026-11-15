from __future__ import annotations

import unittest
from typing import Any

from aiohttp import web

from mattergram import Bot
from mattergram.exceptions import APIError, RequestOutcomeUnknown
from mattergram.types import BufferedInputFile, Message


def post(
    *,
    message: str = "before",
    file_ids: list[str] | None = None,
    delete_at: int = 0,
) -> dict[str, Any]:
    return {
        "id": "post-real",
        "channel_id": "channel-real",
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
            while part := await reader.next():
                if part.name == "files":
                    fields["filename"] = part.filename
                    fields["content_type"] = part.headers.get("Content-Type")
                    fields["data"] = await part.read()
                else:
                    fields[part.name or ""] = await part.text()
            self.upload = fields
            self.calls.append((request.method, path, fields))
            if self.mode == "bad-upload":
                return web.json_response({"file_infos": []}, status=201)
            return web.json_response({"file_infos": [{"id": "file-real"}], "client_ids": []}, status=201)
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
            return web.json_response(
                post(message=str(body["message"]), file_ids=list(body.get("file_ids", []))), status=201
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
                "filename": "report.txt",
                "content_type": "text/plain",
                "data": b"abcdef",
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


if __name__ == "__main__":
    unittest.main()
