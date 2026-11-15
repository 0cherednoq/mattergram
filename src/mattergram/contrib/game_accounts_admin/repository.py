from __future__ import annotations

import asyncio
from collections.abc import Sequence

from .models import AdminUser, CheckRun, GameAccount, ImportRun, PushRule


class MemoryAdminRepository:
    """Explicit non-durable repository for tests and local development."""

    def __init__(self, *, users: Sequence[AdminUser] = ()) -> None:
        self.users = {user.id: user for user in users}
        self.push_rules: dict[str, PushRule] = {}
        self.accounts: dict[str, GameAccount] = {}
        self.imports: list[ImportRun] = []
        self.checks: list[CheckRun] = []
        self.lock = asyncio.Lock()
