"""Post-commit notifications for application-user access changes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import AdminUser


@dataclass(frozen=True, slots=True)
class AdminUserChange:
    """One committed application-user change emitted without UI assumptions."""

    actor_id: str
    action: str
    previous: AdminUser | None
    current: AdminUser


class AdminUserChangeSink(Protocol):
    """Consume committed changes, for example to reconcile external access surfaces."""

    async def write(self, change: AdminUserChange) -> None: ...


class NullAdminUserChangeSink:
    async def write(self, change: AdminUserChange) -> None:
        del change


__all__ = ["AdminUserChange", "AdminUserChangeSink", "NullAdminUserChangeSink"]
