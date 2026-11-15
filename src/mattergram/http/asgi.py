from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from mattergram.exceptions import IngressAuthenticationError, ProtocolError

from .adapter import HttpAdapter, HTTPResult

Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


class ASGIAdapter:
    """Mountable HTTP/lifespan adapter; Bot/Dispatcher remain explicitly caller-owned.

    The app owner must close its resources after this adapter drains. Starting WS
    independently is optional and must share that same lifecycle/loop.
    """

    def __init__(self, adapter: HttpAdapter) -> None:
        self.adapter = adapter

    async def __call__(self, scope: dict[str, Any], receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            raise ValueError("Unknown ASGI scope")
        started = asyncio.get_running_loop().time()
        try:
            client = scope.get("client")
            peer = client[0] if client else None
            self.adapter.peers.verify(peer)
            headers: dict[str, str] = {}
            for name, value in scope.get("headers", []):
                key = name.decode("ascii").casefold()
                if key in headers and key in ("authorization", "content-type"):
                    raise ProtocolError("Ambiguous integration headers")
                headers[key] = value.decode("latin-1")
            body = bytearray()
            async with asyncio.timeout(self.adapter.request_timeout):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    if message["type"] != "http.request" or not isinstance(message.get("body", b""), bytes):
                        raise ProtocolError("Invalid ASGI request event")
                    body.extend(message.get("body", b""))
                    if len(body) > self.adapter.max_body_bytes:
                        await self._send(HTTPResult(413, {"error": "body_too_large"}), send)
                        return
                    if not message.get("more_body", False):
                        break
            processing = asyncio.create_task(
                self.adapter.handle(
                    method=scope["method"],
                    path=scope["path"],
                    headers=headers,
                    body=bytes(body),
                    peer=peer,
                    query=scope.get("query_string", b""),
                    received_at=started,
                )
            )
            disconnected: asyncio.Future[dict[str, Any]] = asyncio.ensure_future(receive())
            try:
                signals: list[asyncio.Future[Any]] = [processing, disconnected]
                done, _ = await asyncio.wait(signals, return_when=asyncio.FIRST_COMPLETED)
                if disconnected in done and disconnected.result().get("type") == "http.disconnect":
                    processing.cancel()
                    await asyncio.gather(processing, return_exceptions=True)
                    return
                result = await processing
            finally:
                disconnected.cancel()
                if not processing.done():
                    processing.cancel()
                await asyncio.gather(disconnected, processing, return_exceptions=True)
        except TimeoutError:
            result = HTTPResult(504, {"error": "deadline"})
        except ProtocolError:
            result = HTTPResult(400, {"error": "invalid_payload"})
        except IngressAuthenticationError:
            result = HTTPResult(403, {"error": "unauthorized"})
        except Exception:
            result = HTTPResult(500, {"error": "handler_failed"})
        await self._send(result, send)

    @staticmethod
    async def _send(result: HTTPResult, send: Send) -> None:
        body = json.dumps(result.payload, ensure_ascii=False, allow_nan=False).encode()
        await send(
            {
                "type": "http.response.start",
                "status": result.status,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"cache-control", b"no-store"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    await self.adapter.startup()
                except Exception:
                    await send(
                        {"type": "lifespan.startup.failed", "message": "HTTP integration startup failed"}
                    )
                    return
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                try:
                    await self.adapter.shutdown()
                except Exception:
                    await send(
                        {"type": "lifespan.shutdown.failed", "message": "HTTP integration shutdown failed"}
                    )
                    return
                await send({"type": "lifespan.shutdown.complete"})
                return
