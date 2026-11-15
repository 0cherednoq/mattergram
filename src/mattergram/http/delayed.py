from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from pydantic import SecretStr

from mattergram.bot import Bot
from mattergram.exceptions import (
    APIError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
    RequestOutcomeUnknown,
    ResponseDeadlineError,
    ResponseStateError,
    TransportError,
)


class DelayedResponder:
    """One received response-url handle: five attempts, 30 minutes, no Bearer/retry/redirect.

    The provider remains authoritative across processes or reconstructed handles.
    Unknown write outcomes consume an attempt rather than invite a duplicate retry.
    """

    def __init__(
        self,
        bot: Bot,
        url: SecretStr,
        *,
        origins: Sequence[str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._bot = bot
        if not isinstance(url, SecretStr):
            raise TypeError("Response URL must be a SecretStr capability")
        parsed = urlsplit(url.get_secret_value())
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Invalid response URL capability")
        allowed = False
        for origin in origins or (bot.instance_id,):
            base = urlsplit(origin)
            prefix = base.path.rstrip("/") + "/hooks/commands/"
            if (parsed.scheme, parsed.hostname, parsed.port) != (base.scheme, base.hostname, base.port):
                continue
            if parsed.path.startswith(prefix):
                tail = parsed.path[len(prefix) :]
                if (
                    tail
                    and not any(char in tail for char in "/\\%")
                    and tail not in (".", "..")
                    and not any(ord(char) <= 32 for char in tail)
                ):
                    allowed = True
        if not allowed:
            raise ValueError("Response URL is outside the configured Mattermost origin/deployment")
        self._url = url
        self._secret_tail = parsed.path.rsplit("/", 1)[-1]
        self._clock = clock or asyncio.get_running_loop().time
        self._expires = self._clock() + 1800
        self._attempts = 0
        self._lock = asyncio.Lock()

    @property
    def attempts(self) -> int:
        return self._attempts

    def _metadata(self, value: Any) -> str | None:
        sanitized = self._bot._metadata(value)
        if sanitized is None:
            return None
        return sanitized.replace(self._url.get_secret_value(), "[REDACTED]").replace(
            self._secret_tail, "[REDACTED]"
        )

    async def __call__(self, payload: Mapping[str, Any]) -> None:
        session = self._bot._get_session()
        async with self._lock:
            if self._clock() >= self._expires:
                raise ResponseDeadlineError("Slash response URL lifetime expired")
            if self._attempts >= 5:
                raise ResponseStateError("Five response URL attempts have already been reserved")
            self._attempts += 1
        transmission = {"sent": False}
        try:
            async with session.post(
                self._url.get_secret_value(),
                json=dict(payload),
                allow_redirects=False,
                trace_request_ctx=transmission,
            ) as response:
                if response.status != 200:
                    data: Any = None
                    try:
                        data = await response.json(content_type=None)
                    except (ValueError, aiohttp.ClientError, TimeoutError):
                        pass
                    kind = {401: AuthenticationError, 403: PermissionDeniedError, 429: RateLimitError}.get(
                        response.status, APIError
                    )
                    raise kind(
                        response.status,
                        error_id=self._metadata(data.get("id")) if isinstance(data, dict) else None,
                        request_id=self._metadata(response.headers.get("X-Request-ID")),
                        retry_after=self._metadata(response.headers.get("Retry-After")),
                    )
                await response.content.read(4096)
        except aiohttp.ClientConnectorError:
            raise TransportError("Could not connect to the slash response endpoint") from None
        except (aiohttp.ClientError, TimeoutError):
            if transmission["sent"]:
                raise RequestOutcomeUnknown(
                    "Slash response was not confirmed; reconcile before retrying"
                ) from None
            raise TransportError("Slash response delivery failed") from None
