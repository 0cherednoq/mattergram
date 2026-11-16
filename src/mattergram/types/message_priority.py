"""Typed Mattermost post-priority metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class MessagePriority:
    """Priority metadata for one root post.

    Mattermost accepts acknowledgement requests for important and urgent posts.
    Persistent notifications are limited to urgent posts and remain subject to
    the server license, configuration, permissions, and explicit mentions.
    """

    level: Literal["important", "urgent"]
    requested_ack: bool = False
    persistent_notifications: bool = False

    def __post_init__(self) -> None:
        if self.level not in {"important", "urgent"}:
            raise ValueError("level must be 'important' or 'urgent'")
        if type(self.requested_ack) is not bool:
            raise TypeError("requested_ack must be a bool")
        if type(self.persistent_notifications) is not bool:
            raise TypeError("persistent_notifications must be a bool")
        if self.persistent_notifications and self.level != "urgent":
            raise ValueError("persistent_notifications requires urgent priority")

    def to_wire(self) -> dict[str, str | bool]:
        """Return the exact object nested under ``metadata.priority``."""
        return {
            "priority": self.level,
            "requested_ack": self.requested_ack,
            "persistent_notifications": self.persistent_notifications,
        }


__all__ = ["MessagePriority"]
