from __future__ import annotations

import unittest

from aiohttp import web

from mattergram import NativeAPI
from mattergram.exceptions import (
    APIError,
    AuthenticationError,
    PermissionDeniedError,
    ProtocolError,
    RateLimitError,
    RequestOutcomeUnknown,
    TransportError,
)


class NativeAPIHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.calls: list[tuple[str, str, str | None, object]] = []
        self.mode = "normal"
        app = web.Application()
        app.router.add_route("*", "/mattermost/api/v4/{tail:.*}", self.handle)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.base_url = f"http://127.0.0.1:{self.runner.addresses[0][1]}/mattermost"
        self.client = NativeAPI(token="fixture-secret", base_url=self.base_url)

    async def asyncTearDown(self) -> None:
        await self.client.close()
        await self.runner.cleanup()

    async def handle(self, request: web.Request) -> web.StreamResponse:
        body: object = await request.json() if request.can_read_body else None
        self.calls.append((request.method, request.raw_path, request.headers.get("Authorization"), body))
        if self.mode == "disconnect":
            request.transport.close()
            return web.Response()
        if self.mode == "unauthorized":
            return web.json_response(
                {"id": "api.invalid.fixture-secret", "request_id": "request-401"}, status=401
            )
        if self.mode == "forbidden":
            return web.json_response(
                {"id": "api.permission.fixture-secret"},
                status=403,
                headers={"X-Request-ID": "request-403"},
            )
        if self.mode == "rate-limit":
            return web.json_response({"id": "api.rate_limit"}, status=429, headers={"Retry-After": "3"})
        if self.mode == "server-error":
            return web.Response(text="proxy fixture-secret", status=503)
        if self.mode == "redirect":
            return web.Response(status=302, headers={"Location": "/credential-must-not-follow"})
        status = 201 if request.method == "POST" else 200
        if self.mode == "wrong-success":
            status = 202
        if self.mode == "invalid-json":
            return web.Response(text="not json", content_type="text/plain", status=status)
        return web.json_response(
            {"method": request.method, "path": request.path, "body": body}, status=status
        )

    async def test_generated_get_encodes_path_and_query_with_one_owned_pool(self) -> None:
        result = await self.client.call(
            "GetPostsForChannel",
            path={"channel_id": "channel/one"},
            query={"page": 2, "include_deleted": True, "tag": ("one", "two")},
        )
        self.assertEqual(result["method"], "GET")
        method, raw_path, authorization, body = self.calls[-1]
        self.assertEqual(method, "GET")
        self.assertEqual(authorization, "Bearer fixture-secret")
        self.assertIn("channels/channel%2Fone/posts?", raw_path)
        self.assertIn("page=2", raw_path)
        self.assertIn("include_deleted=true", raw_path)
        self.assertIn("tag=one&tag=two", raw_path)
        self.assertIsNone(body)

    async def test_generated_write_sends_json_and_invalid_success_is_indeterminate(self) -> None:
        payload = {"channel_id": "channel", "message": "hello"}
        result = await self.client.call("CreatePost", body=payload)
        self.assertEqual(result["body"], payload)
        self.mode = "invalid-json"
        with self.assertRaises(RequestOutcomeUnknown):
            await self.client.call("CreatePost", body=payload)
        with self.assertRaises(ProtocolError):
            await self.client.call("GetUser", path={"user_id": "me"})

    async def test_native_errors_keep_existing_typed_mapping(self) -> None:
        cases = (
            ("unauthorized", AuthenticationError, 401),
            ("forbidden", PermissionDeniedError, 403),
            ("rate-limit", RateLimitError, 429),
            ("server-error", APIError, 503),
            ("redirect", APIError, 302),
            ("wrong-success", APIError, 202),
        )
        for mode, error_type, status in cases:
            self.mode = mode
            before = len(self.calls)
            with self.subTest(mode=mode), self.assertRaises(error_type) as caught:
                await self.client.call("GetUser", path={"user_id": "me"})
            self.assertEqual(caught.exception.status, status)
            self.assertEqual(len(self.calls), before + 1)
            self.assertNotIn("fixture-secret", repr(caught.exception))
        self.mode = "rate-limit"
        with self.assertRaises(RateLimitError) as limited:
            await self.client.call("GetUser", path={"user_id": "me"})
        self.assertEqual(limited.exception.retry_after, "3")

    async def test_disconnect_distinguishes_safe_read_from_uncertain_write_without_retry(self) -> None:
        self.mode = "disconnect"
        with self.assertRaises(TransportError):
            await self.client.call("GetUser", path={"user_id": "me"})
        with self.assertRaises(RequestOutcomeUnknown):
            await self.client.call("CreatePost", body={"channel_id": "channel", "message": "one attempt"})
        self.assertEqual(len(self.calls), 2)


if __name__ == "__main__":
    unittest.main()
