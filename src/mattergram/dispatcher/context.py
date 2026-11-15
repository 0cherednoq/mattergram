"""Explicit named-DI boundary shared by routing and native dispatcher."""

from collections.abc import Mapping
from typing import Any

from mattergram.exceptions import ContextCollisionError

SYSTEM_KEYS = frozenset(
    {
        "bot",
        "event",
        "event_update",
        "event_context",
        "event_raw",
        "event_router",
        "handler",
        "dispatcher",
        "state",
        "raw_state",
        "exception",
        "router",
    }
)


def validate_application_data(data: Mapping[str, Any]) -> None:
    if SYSTEM_KEYS.intersection(data):
        raise ContextCollisionError("Application/filter data cannot replace reserved system context")
