"""Native, credential-free client errors. No provider response prose is logged."""

from __future__ import annotations


class MattergramError(Exception):
    """Base for errors crossing the SDK boundary."""


class APIError(MattergramError):
    """A definite HTTP response; writes are never retried automatically."""

    def __init__(
        self,
        status: int,
        *,
        error_id: str | None = None,
        request_id: str | None = None,
        retry_after: str | None = None,
    ) -> None:
        self.status = status
        self.error_id = error_id
        self.request_id = request_id
        self.retry_after = retry_after
        super().__init__(f"Mattermost returned HTTP {status}")


class AuthenticationError(APIError):
    """The bot credential was rejected (401)."""


class PermissionDeniedError(APIError):
    """The server refused the operation (403)."""


class RateLimitError(APIError):
    """The server returned 429; retry_after preserves its header without retrying."""


class ProtocolError(MattergramError):
    """Malformed wire data or a response that contradicts the operation contract."""


class TransportError(MattergramError):
    """A read failed or the connection could not be established."""


class RequestOutcomeUnknown(TransportError):
    """A write may have reached the server. Reconcile before deciding to retry."""


class ClientClosedError(MattergramError):
    """A terminally closed bot cannot create another client session."""


class BotBindingError(MattergramError):
    """A shortcut needs a bot explicitly bound by from_wire/normalization."""


class UnsupportedKeywordArgument(MattergramError, TypeError):
    """An observer received unsupported registration arguments."""


class DataNotDictLikeError(MattergramError, TypeError):
    """FSM data must be a mapping."""


class StorageDataError(MattergramError, ValueError):
    """Stored FSM data has an invalid shape or encoding; raw data is not included."""


class ContextCollisionError(MattergramError, ValueError):
    """Application/filter data attempted to replace reserved system context."""


class ShutdownTimeoutError(MattergramError, TimeoutError):
    """An in-flight handler did not finish within the configured shutdown bound."""


class ResponseStateError(MattergramError, RuntimeError):
    """An HTTP response is unbound, already completed or no longer usable."""


class ResponseDeadlineError(ResponseStateError, TimeoutError):
    """The initial integration response deadline has expired."""


class UnsupportedCapabilityError(MattergramError):
    """The selected server profile does not implement this operation."""


class IngressAuthenticationError(MattergramError):
    """A request failed source, token, signature, scope or expiry validation."""


class IngressCapacityError(MattergramError):
    """No bounded processing/deferred capacity is available."""
