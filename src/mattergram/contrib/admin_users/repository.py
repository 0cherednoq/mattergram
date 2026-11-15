"""Persistence and audit protocols for the Admin Users plugin."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol

from .models import AdminUser, AuditRecord


class AdminUserRepository(Protocol):
    async def list_users(self) -> Sequence[AdminUser]: ...

    async def get_user(self, user_id: str) -> AdminUser | None: ...

    async def get_by_mattermost_user_id(self, mattermost_user_id: str) -> AdminUser | None: ...

    async def save_user(self, user: AdminUser) -> None: ...


class AuditSink(Protocol):
    async def write(self, record: AuditRecord) -> None: ...


class MemoryAdminUserRepository:
    """Process-local repository for tests and local demos, never durable storage."""

    def __init__(self, *, users: Sequence[AdminUser] = ()) -> None:
        if len({user.id for user in users}) != len(users):
            raise ValueError("Memory admin users must have unique application IDs")
        if len({user.mattermost_user_id for user in users}) != len(users):
            raise ValueError("Memory admin users must have unique Mattermost IDs")
        self.users = {user.id: user for user in users}
        self.lock = asyncio.Lock()

    async def list_users(self) -> tuple[AdminUser, ...]:
        async with self.lock:
            return tuple(self.users.values())

    async def get_user(self, user_id: str) -> AdminUser | None:
        async with self.lock:
            return self.users.get(user_id)

    async def get_by_mattermost_user_id(self, mattermost_user_id: str) -> AdminUser | None:
        async with self.lock:
            return next(
                (user for user in self.users.values() if user.mattermost_user_id == mattermost_user_id),
                None,
            )

    async def save_user(self, user: AdminUser) -> None:
        async with self.lock:
            duplicate = next(
                (
                    current
                    for current in self.users.values()
                    if current.mattermost_user_id == user.mattermost_user_id and current.id != user.id
                ),
                None,
            )
            if duplicate is not None:
                raise ValueError("Mattermost user ID is already assigned")
            self.users[user.id] = user


__all__ = ["AdminUserRepository", "AuditSink", "MemoryAdminUserRepository"]
