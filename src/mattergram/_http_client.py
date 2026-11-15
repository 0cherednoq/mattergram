"""Shared owned HTTP transport; each client keeps its own credentials and pool."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from types import SimpleNamespace, TracebackType
from typing import Any, Self
from urllib.parse import urlsplit, urlunsplit

import aiohttp

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
from mattergram.types import User

_UNSET_PAYLOAD = object()


async def _headers_sent(
    session: aiohttp.ClientSession, context: SimpleNamespace, params: aiohttp.TraceRequestHeadersSentParams
) -> None:
    if context.trace_request_ctx is not None:
        context.trace_request_ctx["sent"] = True


async def _reject_redirect(
    session: aiohttp.ClientSession, context: SimpleNamespace, params: aiohttp.TraceRequestRedirectParams
) -> None:
    raise ProtocolError("Mattermost redirect refused")


class _HTTPClient:
    def __init__(self, *, token: str, base_url: str, request_timeout: float = 30.0) -> None:
        if (
            not isinstance(token, str)
            or not token
            or not token.isascii()
            or any(ord(character) <= 32 or ord(character) == 127 for character in token)
        ):
            raise ValueError("token must be a nonempty credential without whitespace")
        if not isinstance(base_url, str) or base_url.strip() != base_url:
            raise ValueError("base_url must be an absolute HTTP(S) server URL")
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("base_url must be an HTTP(S) URL without credentials, query or fragment")
        try:
            parsed.port
        except ValueError:
            raise ValueError("base_url has an invalid port") from None
        if (
            isinstance(request_timeout, bool)
            or not isinstance(request_timeout, (float, int))
            or not math.isfinite(request_timeout)
            or request_timeout <= 0
        ):
            raise ValueError("request_timeout must be a positive finite number of seconds")
        self._api_url = urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/api/v4/", "", "")
        )
        self._token = token
        self._timeout = float(request_timeout)
        self._session: aiohttp.ClientSession | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False
        self._entered = False
        self._me: User | None = None

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def user_id(self) -> str | None:
        """Authenticated identity, if get_me or async context entry has completed."""
        return self._me.id if self._me is not None else None

    @property
    def instance_id(self) -> str:
        """Canonical configured server/deployment identity used to namespace state."""
        return self._api_url.removesuffix("api/v4/")

    def _get_session(self) -> aiohttp.ClientSession:
        if self._closed:
            raise ClientClosedError("Client is closed")
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise RuntimeError("Bot cannot be shared across event loops")
        if self._session is None:
            self._loop = loop
            trace = aiohttp.TraceConfig()
            trace.on_request_headers_sent.append(_headers_sent)
            trace.on_request_redirect.append(_reject_redirect)
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout),
                cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False,
                trace_configs=[trace],
            )
            # aiohttp retries idempotent methods once after some connection
            # failures. The SDK contract requires one observable attempt so
            # callers, tests and reconciliation do not depend on HTTP verbs.
            setattr(self._session, "_retry_connection", False)
        return self._session

    def _metadata(self, value: Any, secrets: Sequence[str] = ()) -> str | None:
        if not isinstance(value, str):
            return None
        text_value: str = value
        for secret in (self._token, *secrets):
            if secret:
                text_value = text_value.replace(secret, "[REDACTED]")
                if len(secret) > 64:
                    text_value = text_value.replace(secret[:32], "[REDACTED]").replace(
                        secret[-32:], "[REDACTED]"
                    )
        return text_value[:256]

    async def _request(
        self,
        method: str,
        path: str,
        expected_status: int | Sequence[int],
        payload: Any = None,
        *,
        secrets: Sequence[str] = (),
    ) -> dict[str, Any]:
        data = await self._request_json(method, path, expected_status, payload, secrets=secrets)
        if not isinstance(data, dict) or not all(isinstance(key, str) for key in data):
            raise ProtocolError("Mattermost response must be a JSON object")
        return data

    async def _request_json(
        self,
        method: str,
        path: str,
        expected_status: int | Sequence[int],
        payload: Any = None,
        *,
        secrets: Sequence[str] = (),
    ) -> Any:
        return await self._request_payload(
            method,
            path,
            expected_status,
            json_payload=payload,
            secrets=secrets,
        )

    async def _request_plugin_json(
        self,
        plugin_id: str,
        path: str,
        method: str,
        expected_status: int | Sequence[int],
        payload: Any = None,
    ) -> Any:
        """Call one authenticated, typed plugin route without exposing an arbitrary URL API."""
        if not plugin_id or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for character in plugin_id
        ):
            raise ValueError("Invalid Mattermost plugin ID")
        if not path or path.startswith("/") or ".." in path.split("/"):
            raise ValueError("Invalid Mattermost plugin path")
        return await self._request_payload(
            method,
            path,
            expected_status,
            json_payload=payload,
            base_url=self.instance_id + "plugins/" + plugin_id + "/",
        )

    async def _request_multipart(
        self,
        method: str,
        path: str,
        expected_status: int | Sequence[int],
        form: aiohttp.FormData,
        *,
        secrets: Sequence[str] = (),
    ) -> Any:
        return await self._request_payload(
            method,
            path,
            expected_status,
            data_payload=form,
            secrets=secrets,
        )

    async def _request_payload(
        self,
        method: str,
        path: str,
        expected_status: int | Sequence[int],
        *,
        json_payload: Any = _UNSET_PAYLOAD,
        data_payload: Any = _UNSET_PAYLOAD,
        secrets: Sequence[str] = (),
        base_url: str | None = None,
    ) -> Any:
        if (json_payload is _UNSET_PAYLOAD) == (data_payload is _UNSET_PAYLOAD):
            raise ValueError("Declare exactly one native request payload mode")
        session = self._get_session()
        transmission = {"sent": False}
        request_payload = (
            {"json": json_payload} if json_payload is not _UNSET_PAYLOAD else {"data": data_payload}
        )
        try:
            async with session.request(
                method,
                (self._api_url if base_url is None else base_url) + path,
                headers={"Authorization": "Bearer " + self._token},
                allow_redirects=False,
                trace_request_ctx=transmission,
                **request_payload,
            ) as response:
                expected_statuses = (
                    (expected_status,) if isinstance(expected_status, int) else tuple(expected_status)
                )
                if not expected_statuses or response.status not in expected_statuses:
                    error: Any = None
                    try:
                        error = await response.json(content_type=None)
                    except (ValueError, aiohttp.ClientError, TimeoutError):
                        pass
                    error_id = self._metadata(error.get("id"), secrets) if isinstance(error, dict) else None
                    request_id = self._metadata(response.headers.get("X-Request-ID"), secrets)
                    if request_id is None and isinstance(error, dict):
                        request_id = self._metadata(error.get("request_id"), secrets)
                    kind = {401: AuthenticationError, 403: PermissionDeniedError, 429: RateLimitError}.get(
                        response.status, APIError
                    )
                    raise kind(
                        response.status,
                        error_id=error_id,
                        request_id=request_id,
                        retry_after=self._metadata(response.headers.get("Retry-After"), secrets),
                    )
                try:
                    data = await response.json()
                except (ValueError, aiohttp.ContentTypeError):
                    raise ProtocolError(
                        "Mattermost returned invalid JSON for a successful operation"
                    ) from None
                return data
        except aiohttp.ClientConnectorError:
            raise TransportError("Could not connect to Mattermost") from None
        except (aiohttp.ClientError, TimeoutError):
            if method not in {"GET", "HEAD", "OPTIONS"} and transmission["sent"]:
                raise RequestOutcomeUnknown(
                    "Mattermost write was not confirmed; reconcile before retrying"
                ) from None
            raise TransportError("Mattermost request failed") from None

    async def get_me(self) -> User:
        user = User.from_wire(await self._request("GET", "users/me", 200))
        self._me = user
        return user

    async def close(self) -> None:
        if (
            self._loop is not None
            and self._loop is not asyncio.get_running_loop()
            and self._session is not None
            and not self._session.closed
        ):
            raise RuntimeError("Bot must be closed in its owning event loop")
        self._closed = True
        if self._session is not None:
            await self._session.close()

    async def __aenter__(self) -> Self:
        if self._entered:
            raise RuntimeError("Bot async context is already active")
        self._entered = True
        try:
            await self.get_me()
        except BaseException:
            try:
                await self.close()
            finally:
                self._entered = False
            raise
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        try:
            await self.close()
        finally:
            self._entered = False
