"""Native slash observer with explicit provisioning metadata."""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from typing import Any

from mattergram.dispatcher.flags import extract_flags_from_object
from mattergram.types.command_metadata import CommandRegistration

from .handler import CallbackType
from .message import NativeEventObserver

REGISTRATION_FLAG = "_slash_registration"


class SlashEventObserver(NativeEventObserver):
    def register(
        self,
        callback: CallbackType,
        *filters: CallbackType,
        flags: dict[str, Any] | None = None,
        description: str = "",
        hint: str = "",
        triggers: Sequence[str] | None = None,
        provision: bool = True,
        **kwargs: Any,
    ) -> CallbackType:
        if triggers is not None and (isinstance(triggers, str) or not isinstance(triggers, Sequence)):
            raise TypeError("triggers must be a sequence of literal command names")
        metadata = CommandRegistration(
            description=description,
            hint=hint,
            triggers=tuple(triggers) if triggers is not None else None,
            provision=provision,
        )
        copied = dict(flags or {})
        if REGISTRATION_FLAG in copied or REGISTRATION_FLAG in extract_flags_from_object(
            inspect.unwrap(callback)
        ):
            raise ValueError("Slash registration metadata is set through explicit observer arguments")
        if isinstance(copied.get("commands"), list):
            copied["commands"] = list(copied["commands"])
        copied[REGISTRATION_FLAG] = metadata
        result = super().register(callback, *filters, flags=copied, **kwargs)
        if self.handlers[-1].flags.get(REGISTRATION_FLAG) is not metadata:
            self.handlers.pop()
            raise ValueError("Filters cannot replace reserved slash registration metadata")
        return result
