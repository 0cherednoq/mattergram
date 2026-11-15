from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from mattergram.exceptions import ResponseDeadlineError, ResponseStateError, UnsupportedCapabilityError

if TYPE_CHECKING:
    from mattergram.ui.forms import Form, Option

    from .dialogs import DialogHandle

ResponseKind = Literal[
    "slash", "callback", "dialog_submit", "dialog_refresh", "dialog_lookup", "dialog_cancel", "dialog_action"
]


@dataclass(frozen=True)
class HTTPCapabilities:
    """Baseline 11.1 action response has neither navigation nor inline error fields."""

    post_action_navigation: bool = False
    post_action_error: bool = False


class ResponseController:
    def __init__(
        self,
        kind: ResponseKind,
        *,
        timeout: float = 2.5,
        capabilities: HTTPCapabilities | None = None,
        reserve_deferred: Callable[[], None] | None = None,
        followup: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        open_dialog: Callable[[Form], Awaitable[DialogHandle]] | None = None,
        replace_form: Callable[[Form], Awaitable[dict[str, Any]]] | None = None,
        field_names: frozenset[str] = frozenset(),
    ) -> None:
        if kind not in (
            "slash",
            "callback",
            "dialog_submit",
            "dialog_refresh",
            "dialog_lookup",
            "dialog_cancel",
            "dialog_action",
        ):
            raise ValueError("Unknown HTTP response kind")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (float, int))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("Response timeout must be positive and finite")
        self.kind = kind
        self._loop = asyncio.get_running_loop()
        self.deadline = self._loop.time() + timeout
        self.capabilities = capabilities or HTTPCapabilities()
        self.future: asyncio.Future[dict[str, Any]] = self._loop.create_future()
        self._payload: dict[str, Any] | None = None
        self._expired = False
        self.deferred = False
        self._reserve_deferred = reserve_deferred
        self._followup = followup
        self._open_dialog = open_dialog
        self._replace_form = replace_form
        self._field_names = field_names
        self._opening = False
        self._open_attempted = False

    @property
    def requires_explicit(self) -> bool:
        return self.kind.startswith("dialog_")

    @property
    def completed(self) -> bool:
        return self._payload is not None

    @property
    def payload(self) -> dict[str, Any] | None:
        return deepcopy(self._payload)

    def expire(self) -> None:
        self._expired = True
        if not self.future.done():
            self.future.cancel()

    def _pending(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise ResponseStateError("HTTP responses belong to their request event loop")
        if self.completed:
            raise ResponseStateError("HTTP response was already completed")
        if self._opening:
            raise ResponseStateError("An HTTP response operation is still pending")
        if self._expired or self._loop.time() >= self.deadline:
            raise ResponseDeadlineError("HTTP response deadline expired")

    def complete(self, payload: Mapping[str, Any]) -> None:
        self._pending()
        if self.kind == "slash":
            supported = {
                "response_type",
                "text",
                "username",
                "channel_id",
                "icon_url",
                "type",
                "props",
                "goto_location",
                "skip_slack_parsing",
                "attachments",
                "extra_responses",
            }
        elif self.kind == "callback":
            supported = {"update", "ephemeral_text", "skip_slack_parsing"}
            if self.capabilities.post_action_navigation:
                supported.add("goto_location")
            if self.capabilities.post_action_error:
                supported.add("error")
        elif self.kind == "dialog_lookup":
            supported = {"items"}
            items = payload.get("items")
            if not isinstance(items, list) or any(
                not isinstance(item, dict)
                or set(item) != {"text", "value"}
                or any(not isinstance(v, str) or not v for v in item.values())
                for item in items
            ):
                raise ResponseStateError("Lookup responses require native text/value items")
        elif self.kind in ("dialog_cancel", "dialog_action"):
            supported = set()
        else:
            supported = {"error", "errors", "type", "form"}
            errors = payload.get("errors")
            error = payload.get("error")
            if errors is not None and (
                not isinstance(errors, Mapping)
                or not errors
                or any(
                    not isinstance(k, str) or k not in self._field_names or not isinstance(v, str) or not v
                    for k, v in errors.items()
                )
            ):
                raise ResponseStateError("Field errors require known field names and nonempty messages")
            if error is not None and (not isinstance(error, str) or not error):
                raise ResponseStateError("Validation error must be nonempty text")
            response_type = payload.get("type", "")
            if response_type not in ("", "ok", "form", "navigate"):
                raise ResponseStateError("Unknown native dialog response type")
            if response_type == "form":
                if not isinstance(payload.get("form"), Mapping) or errors or error:
                    raise ResponseStateError("A form response requires one form and no validation error")
            elif "form" in payload or ((errors or error) and response_type):
                raise ResponseStateError("Conflicting native dialog response results")
        if set(payload) - supported:
            raise UnsupportedCapabilityError("Response fields are not supported by the selected profile")
        self._payload = deepcopy(dict(payload))
        self.future.set_result(deepcopy(self._payload))

    def ack(self) -> None:
        self.complete({"response_type": "ephemeral", "text": ""} if self.kind == "slash" else {})

    def answer(self, text: str | None, *, ephemeral: bool = True) -> None:
        if self.kind not in ("slash", "callback"):
            raise UnsupportedCapabilityError("Use the response family of this native form event")
        if text is not None and not isinstance(text, str):
            raise TypeError("Response text must be a string or None")
        if type(ephemeral) is not bool:
            raise TypeError("ephemeral must be a bool")
        if self.kind == "slash":
            self.complete({"response_type": "ephemeral" if ephemeral else "in_channel", "text": text or ""})
        else:
            if not ephemeral:
                raise UnsupportedCapabilityError(
                    "A post-action answer is ephemeral; use update_message for a shared post"
                )
            self.complete({"ephemeral_text": text} if text is not None else {})

    def update(self, text: str, props: Mapping[str, Any] | None = None) -> None:
        if self.kind != "callback":
            raise UnsupportedCapabilityError("Slash commands do not update a fabricated source post")
        if not isinstance(text, str):
            raise TypeError("Updated message text must be a string")
        update: dict[str, Any] = {"message": text}
        if props is not None:
            update["props"] = deepcopy(dict(props))
        self.complete({"update": update})

    def error(self, text: str) -> None:
        if self.kind != "callback" or not self.capabilities.post_action_error:
            raise UnsupportedCapabilityError(
                "The selected profile does not support post-action inline errors"
            )
        if not isinstance(text, str):
            raise TypeError("Error text must be a string")
        self.complete({"error": {"message": text}})

    def navigate(self, url: str) -> None:
        if self.kind not in ("slash", "callback"):
            raise UnsupportedCapabilityError("Form navigation has a different native response protocol")
        if self.kind == "callback" and not self.capabilities.post_action_navigation:
            raise UnsupportedCapabilityError("The selected profile does not support post-action navigation")
        if not isinstance(url, str) or not url or any(ord(char) < 32 for char in url):
            raise ValueError("Navigation requires a nonempty URL without control characters")
        self.complete(
            {
                "goto_location": url,
                **({"response_type": "ephemeral", "text": ""} if self.kind == "slash" else {}),
            }
        )

    def defer(self, text: str | None = None, *, ephemeral: bool = True) -> None:
        self._pending()
        if self.kind != "slash" or self._followup is None or self._reserve_deferred is None:
            raise UnsupportedCapabilityError(
                "Deferred slash work needs a bound response URL and explicit managed capacity"
            )
        if text is not None and not isinstance(text, str):
            raise TypeError("Response text must be a string or None")
        if type(ephemeral) is not bool:
            raise TypeError("ephemeral must be a bool")
        self._reserve_deferred()
        self.deferred = True
        self.answer(text, ephemeral=ephemeral)

    async def followup(self, text: str, *, ephemeral: bool = True) -> None:
        if self._expired or not self.completed or self._followup is None or self.kind != "slash":
            raise ResponseStateError("A followup needs an initial response and a bound slash response URL")
        if not isinstance(text, str) or type(ephemeral) is not bool:
            raise TypeError("Followup requires text and an explicit boolean visibility")
        await self._followup({"response_type": "ephemeral" if ephemeral else "in_channel", "text": text})

    def field_errors(self, errors: Mapping[str, str]) -> None:
        if self.kind not in ("dialog_submit", "dialog_refresh"):
            raise UnsupportedCapabilityError("This event cannot return form field errors")
        self.complete({"errors": dict(errors)})

    def validation_error(self, message: str) -> None:
        if self.kind not in ("dialog_submit", "dialog_refresh"):
            raise UnsupportedCapabilityError("This event cannot return a form validation error")
        self.complete({"error": message})

    async def replace_form(self, form: Form) -> None:
        self._pending()
        if self.kind not in ("dialog_submit", "dialog_refresh") or self._replace_form is None:
            raise UnsupportedCapabilityError("This event has no managed continuation binding")
        wire = await self._replace_form(form)
        self.complete({"type": "form", "form": wire})

    def lookup_answer(self, options: Sequence[Option]) -> None:
        if self.kind != "dialog_lookup":
            raise UnsupportedCapabilityError("Only lookup events accept an option list")
        self.complete({"items": [option.model_dump(mode="json") for option in options]})

    async def open_dialog(self, form: Form) -> DialogHandle:
        self._pending()
        if self.kind not in ("slash", "callback", "dialog_action") or self._open_dialog is None:
            raise UnsupportedCapabilityError("A form needs a bound native user trigger")
        if self._open_attempted:
            raise ResponseStateError("This request already attempted to use its dialog trigger")
        self._open_attempted = self._opening = True
        try:
            async with asyncio.timeout(max(0.0, self.deadline - self._loop.time())):
                result = await self._open_dialog(form)
        finally:
            self._opening = False
        self.ack()
        return result
