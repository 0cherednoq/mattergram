from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .models import AccountCredentials, PushEvent, PushRule, TeamUser, VerificationResult


class AccountVerifier(Protocol):
    async def verify(self, game: str, credentials: AccountCredentials) -> VerificationResult: ...


class ImportFileReader(Protocol):
    async def read(self, file_id: str) -> bytes: ...


class PushDispatcher(Protocol):
    async def send(self, rule: PushRule, event: PushEvent, text: str) -> None: ...


class TeamUserDirectory(Protocol):
    async def list_users(self) -> Sequence[TeamUser]: ...
