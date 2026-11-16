from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aiohttp import web

from mattergram.bot import Bot
from mattergram.dispatcher.dispatcher import Dispatcher
from mattergram.dispatcher.event.bases import UNHANDLED
from mattergram.exceptions import (
    IngressAuthenticationError,
    IngressCapacityError,
    ProtocolError,
    ResponseDeadlineError,
    ResponseStateError,
    ShutdownTimeoutError,
)
from mattergram.types import CallbackQuery, HTTPEvent, SlashCommand

if TYPE_CHECKING:
    from mattergram.ui.forms import Form

    from .dialogs import DialogHandle, DialogManager

from .auth import CallbackSigner, SlashBinding, TrustedPeers, decode_form, decode_json
from .delayed import DelayedResponder
from .normalizers import normalize_callback, normalize_slash
from .responses import HTTPCapabilities, ResponseController

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HTTPResult:
    status: int
    payload: dict[str, Any] = field(repr=False)


def _positive(value: float, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be positive and finite")
    return float(value)


class HttpAdapter:
    """HTTP-only ingress borrowing Bot/Dispatcher. Explicit defer is bounded and non-durable.

    A trusted transport path and per-command/context authentication are both required.
    Framework adapters never trust Forwarded/X-Forwarded-For as actor evidence.
    """

    def __init__(
        self,
        bot: Bot,
        dispatcher: Dispatcher,
        *,
        trusted_peers: Sequence[str],
        slash_bindings: Sequence[SlashBinding] = (),
        callback_signer: CallbackSigner | None = None,
        path_prefix: str = "/mattergram",
        request_timeout: float = 2.5,
        deferred_timeout: float = 60,
        shutdown_timeout: float = 10,
        max_body_bytes: int = 65536,
        max_inflight: int = 64,
        max_deferred: int = 16,
        response_origins: Sequence[str] | None = None,
        capabilities: HTTPCapabilities | None = None,
        dialogs: DialogManager | None = None,
        own_dialogs: bool = False,
    ) -> None:
        if not slash_bindings and callback_signer is None and dialogs is None:
            raise ValueError("Configure at least one slash binding or callback signer")
        if not path_prefix.startswith("/") or any(
            value in path_prefix for value in ("?", "#", "\\", "..", "//", "%")
        ):
            raise ValueError("HTTP path prefix must be a canonical absolute path")
        for value in (max_body_bytes, max_inflight, max_deferred):
            if type(value) is not int or value < 1:
                raise ValueError("HTTP capacity/body limits must be positive integers")
        if isinstance(trusted_peers, str) or isinstance(response_origins, str):
            raise TypeError("Peer networks and response origins must be explicit sequences")
        self.bot = bot
        self.dispatcher = dispatcher
        self.peers = TrustedPeers(trusted_peers)
        self.signer = callback_signer
        self.bindings = tuple(slash_bindings)
        self.path_prefix = path_prefix.rstrip("/")
        self.request_timeout = _positive(request_timeout, "request_timeout")
        self.deferred_timeout = _positive(deferred_timeout, "deferred_timeout")
        self.shutdown_timeout = _positive(shutdown_timeout, "shutdown_timeout")
        self.max_body_bytes = max_body_bytes
        self.max_inflight = max_inflight
        self.max_deferred = max_deferred
        self.response_origins = tuple(response_origins) if response_origins is not None else None
        self.capabilities = capabilities or HTTPCapabilities()
        if dialogs is not None and dialogs.bot is not bot:
            raise ValueError("Dialog manager must share the exact runtime Bot")
        self.dialogs = dialogs
        self._own_dialogs = own_dialogs
        self.rejected_requests = 0
        self.request_timeouts = 0
        self.handler_errors = 0
        self.background_errors = 0
        self._active = 0
        self._deferred = 0
        self._ready = False
        self._closed = False
        self._handlers: set[asyncio.Task[Any]] = set()
        self._requests: set[asyncio.Task[Any]] = set()
        self._monitors: set[asyncio.Task[None]] = set()
        self._listening_addresses: tuple[tuple[str, int], ...] = ()

    @property
    def listening_addresses(self) -> tuple[tuple[str, int], ...]:
        return self._listening_addresses

    def add_slash_binding(self, binding: SlashBinding) -> None:
        """Bind a token issued by the server/provisioner; never creates a server command."""
        if not isinstance(binding, SlashBinding) or self._closed:
            raise ValueError("A live adapter and actual SlashBinding are required")
        if binding in self.bindings:
            raise ValueError("Slash binding is already registered")
        self.bindings = (*self.bindings, binding)

    async def startup(self) -> None:
        if self._closed or self.dispatcher._closed:
            raise RuntimeError("HTTP adapter or dispatcher is closed")
        identity = await self.dispatcher._identity(self.bot)
        if self.signer is not None and (
            self.signer.instance_id != self.bot.instance_id or self.signer.bot_id != identity
        ):
            raise ValueError("Callback signer belongs to another instance or bot")
        if self.dialogs is not None:
            await self.dialogs.startup()
        self._ready = True

    @property
    def pending_deferred(self) -> int:
        return self._deferred

    def _reserve_deferred(self) -> None:
        if not self._ready or self._deferred >= self.max_deferred:
            raise IngressCapacityError("Deferred capacity is unavailable")
        self._deferred += 1

    @staticmethod
    def _observe(task: asyncio.Task[Any]) -> None:
        if not task.cancelled():
            task.exception()

    async def _cancel(self, task: asyncio.Task[Any]) -> None:
        if not task.done():
            task.cancel()
        done, _ = await asyncio.wait({task}, timeout=self.shutdown_timeout)
        if not done:
            raise ShutdownTimeoutError("HTTP handler did not cooperate with cancellation")
        self._observe(task)

    async def _monitor(self, task: asyncio.Task[Any], response: ResponseController) -> None:
        try:
            done, _ = await asyncio.wait({task}, timeout=self.deferred_timeout)
            if not done:
                response.expire()
                await self._cancel(task)
                self.background_errors += 1
            else:
                task.result()
        except asyncio.CancelledError:
            response.expire()
            await self._cancel(task)
            raise
        except Exception:
            self.background_errors += 1
        finally:
            self._deferred -= 1

    async def shutdown(self) -> None:
        self._ready = False
        self._closed = True
        tasks = (set(self._handlers) | set(self._monitors) | set(self._requests)) - {asyncio.current_task()}
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=self.shutdown_timeout)
            for task in pending:
                task.cancel()
            if pending:
                done, remaining = await asyncio.wait(pending, timeout=self.shutdown_timeout)
                for task in done:
                    self._observe(task)
                if remaining:
                    raise ShutdownTimeoutError("HTTP work remains after shutdown cancellation")
        if self.dialogs is not None and self._own_dialogs:
            await self.dialogs.close()

    def _binding(self, payload: Mapping[str, Any], method: str, authorization: str | None) -> SlashBinding:
        for binding in self.bindings:
            try:
                binding.verify(payload, method=method, authorization=authorization)
                return binding
            except IngressAuthenticationError:
                continue
        raise IngressAuthenticationError("Unknown slash credentials or scope")

    async def handle(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
        peer: str | None,
        query: bytes = b"",
        received_at: float | None = None,
    ) -> HTTPResult:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("HTTP processing needs an asyncio task")
        self._requests.add(task)
        try:
            return await self._handle(
                method=method,
                path=path,
                headers=headers,
                body=body,
                peer=peer,
                query=query,
                received_at=received_at,
            )
        finally:
            self._requests.discard(task)

    async def _handle(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
        peer: str | None,
        query: bytes = b"",
        received_at: float | None = None,
    ) -> HTTPResult:
        if not self._ready:
            return HTTPResult(503, {"error": "not_ready"})
        if self._active >= self.max_inflight:
            return HTTPResult(503, {"error": "capacity"})
        self._active += 1
        started = asyncio.get_running_loop().time() if received_at is None else received_at
        response: ResponseController | None = None
        event: HTTPEvent
        try:
            headers = {key.casefold(): value for key, value in headers.items()}
            self.peers.verify(peer)
            if len(body) > self.max_body_bytes or len(query) > self.max_body_bytes:
                return HTTPResult(413, {"error": "body_too_large"})
            remaining = started + self.request_timeout - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise ResponseDeadlineError("HTTP request deadline expired")
            content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if path == self.path_prefix + "/slash":
                if method not in ("GET", "POST"):
                    return HTTPResult(405, {"error": "method"})
                if method == "POST" and content_type != "application/x-www-form-urlencoded":
                    return HTTPResult(415, {"error": "content_type"})
                if method == "POST" and query:
                    raise ProtocolError("POST slash parameters belong in the form body")
                payload = decode_form(query if method == "GET" else body)
                binding = self._binding(payload, method, headers.get("authorization"))
                event = normalize_slash(
                    payload,
                    binding=binding,
                    peers=self.peers,
                    peer=peer,
                    method=method,
                    authorization=headers.get("authorization"),
                )
                followup = (
                    DelayedResponder(self.bot, event.response_url, origins=self.response_origins)
                    if event.response_url is not None
                    else None
                )
                response = ResponseController(
                    "slash",
                    timeout=remaining,
                    capabilities=self.capabilities,
                    reserve_deferred=self._reserve_deferred,
                    followup=followup,
                )
            elif path == self.path_prefix + "/dialog":
                if method != "POST":
                    return HTTPResult(405, {"error": "method"})
                if content_type != "application/json":
                    return HTTPResult(415, {"error": "content_type"})
                if query or self.dialogs is None:
                    raise IngressAuthenticationError("Managed form endpoint is not configured")
                return await self.dialogs.handle(
                    decode_json(body), deadline=started + self.request_timeout, serve=self._serve
                )
            elif path == self.path_prefix + "/callback":
                if method != "POST":
                    return HTTPResult(405, {"error": "method"})
                if content_type != "application/json":
                    return HTTPResult(415, {"error": "content_type"})
                if query or self.signer is None:
                    raise IngressAuthenticationError("Callback endpoint is not configured")
                callback = normalize_callback(
                    decode_json(body), signer=self.signer, peers=self.peers, peer=peer
                )
                event = callback
                response = ResponseController("callback", timeout=remaining, capabilities=self.capabilities)
            else:
                return HTTPResult(404, {"error": "route"})
            event._response = response
            if self.dialogs is not None and isinstance(event, (SlashCommand, CallbackQuery)):
                manager = self.dialogs
                bound_event = event

                async def open_dialog(form: Form) -> DialogHandle:
                    return await manager.open(bound_event, form)

                response._open_dialog = open_dialog
        except IngressAuthenticationError:
            self.rejected_requests += 1
            return HTTPResult(403, {"error": "unauthorized"})
        except ResponseDeadlineError:
            self.request_timeouts += 1
            return HTTPResult(504, {"error": "deadline"})
        except (ProtocolError, ValueError, TypeError):
            self.rejected_requests += 1
            return HTTPResult(400, {"error": "invalid_payload"})
        finally:
            # Actual handler admission is counted across _serve as well.
            if response is None:
                self._active -= 1
        try:
            return await self._serve(event, response)
        finally:
            self._active -= 1

    async def _serve(self, event: HTTPEvent, response: ResponseController) -> HTTPResult:
        handler = asyncio.create_task(self.dispatcher.feed_event(self.bot, event))
        self._handlers.add(handler)
        handler.add_done_callback(self._handlers.discard)
        handler.add_done_callback(self._observe)
        detached = False
        try:
            remaining = max(0.0, response.deadline - asyncio.get_running_loop().time())
            done, _ = await asyncio.wait(
                {handler, response.future}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            if response.future in done and response.deferred and not handler.done():
                monitor = asyncio.create_task(self._monitor(handler, response))
                self._monitors.add(monitor)
                monitor.add_done_callback(self._monitors.discard)
                monitor.add_done_callback(self._observe)
                detached = True
                return self._result(response)
            if not handler.done():
                remaining = max(0.0, response.deadline - asyncio.get_running_loop().time())
                await asyncio.wait({handler}, timeout=remaining)
            if not handler.done():
                response.expire()
                self.request_timeouts += 1
                return HTTPResult(504, {"error": "deadline"})
            result = handler.result()
            if result is UNHANDLED and not response.completed:
                if response.kind == "dialog_cancel":
                    response.ack()
                    return self._result(response)
                response.expire()
                return HTTPResult(404, {"error": "unhandled"})
            if result is not None and result is not UNHANDLED:
                if not isinstance(result, Mapping):
                    raise ResponseStateError("HTTP handler must return native response mapping or None")
                response.complete(result)
            if not response.completed:
                if response.requires_explicit:
                    raise ResponseStateError("A native form response must be completed explicitly")
                response.ack()
            return self._result(response)
        except ResponseDeadlineError:
            response.expire()
            self.request_timeouts += 1
            return HTTPResult(504, {"error": "deadline"})
        except IngressCapacityError:
            response.expire()
            return HTTPResult(503, {"error": "deferred_capacity"})
        except asyncio.CancelledError:
            response.expire()
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            return HTTPResult(503, {"error": "shutdown"})
        except Exception:
            logger.exception("Mattergram HTTP handler failed")
            response.expire()
            self.handler_errors += 1
            return HTTPResult(500, {"error": "handler_failed"})
        finally:
            if not detached:
                if response.deferred:
                    self._deferred -= 1
                if not handler.done():
                    await self._cancel(handler)

    def _result(self, response: ResponseController) -> HTTPResult:
        payload = response.payload
        if payload is None:
            raise ResponseStateError("HTTP response was not completed")
        encoded = json.dumps(payload, allow_nan=False).encode()
        if len(encoded) > self.max_body_bytes:
            raise ResponseStateError("HTTP response exceeds configured size")
        return HTTPResult(200, payload)

    def create_app(self) -> web.Application:
        app = web.Application(client_max_size=self.max_body_bytes)

        async def startup(app: web.Application) -> None:
            await self.startup()

        async def shutdown(app: web.Application) -> None:
            await self.shutdown()

        async def receive(request: web.Request) -> web.Response:
            started = asyncio.get_running_loop().time()
            try:
                self.peers.verify(request.remote)
                headers: dict[str, str] = {}
                for name in request.headers:
                    if (
                        name.casefold() in ("authorization", "content-type")
                        and len(request.headers.getall(name)) != 1
                    ):
                        raise ProtocolError("Ambiguous integration headers")
                    headers[name] = request.headers[name]
                async with asyncio.timeout(self.request_timeout):
                    body = await request.read()
                result = await self.handle(
                    method=request.method,
                    path=request.path,
                    headers=headers,
                    body=body,
                    peer=request.remote,
                    query=request.rel_url.raw_query_string.encode("ascii"),
                    received_at=started,
                )
            except IngressAuthenticationError:
                result = HTTPResult(403, {"error": "unauthorized"})
            except web.HTTPRequestEntityTooLarge:
                result = HTTPResult(413, {"error": "body_too_large"})
            except TimeoutError:
                result = HTTPResult(504, {"error": "deadline"})
            except ProtocolError:
                result = HTTPResult(400, {"error": "invalid_payload"})
            return web.json_response(result.payload, status=result.status)

        app.on_startup.append(startup)
        app.on_shutdown.append(shutdown)
        app.router.add_route("*", self.path_prefix + "/slash", receive)
        app.router.add_route("*", self.path_prefix + "/callback", receive)
        app.router.add_route("*", self.path_prefix + "/dialog", receive)
        return app
