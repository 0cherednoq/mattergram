from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import SecretStr

AdminRole = Literal["admin", "operator", "viewer"]


AccountStatus = Literal["unchecked", "valid", "invalid", "error"]


RunStatus = Literal["completed", "partial", "failed"]


PushDestinationKind = Literal["channel", "direct"]


PushEvent = Literal[
    "import.completed",
    "import.failed",
    "account.check_failed",
    "account.invalid",
]


PUSH_EVENTS: tuple[PushEvent, ...] = (
    "import.completed",
    "import.failed",
    "account.check_failed",
    "account.invalid",
)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _id(prefix: str) -> str:
    return prefix + "_" + uuid4().hex


@dataclass(frozen=True, slots=True)
class AdminUser:
    id: str
    mattermost_user_id: str
    username: str
    role: AdminRole
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class TeamUser:
    """One active human Mattermost account available to this application."""

    id: str
    username: str
    display_name: str = ""
    is_bot: bool = False

    def __post_init__(self) -> None:
        if not self.id or not self.username:
            raise ValueError("Team users require nonempty Mattermost IDs and usernames")

    @property
    def option_label(self) -> str:
        name = self.display_name.strip()
        return (
            f"@{self.username} — {name}"
            if name and name.casefold() != self.username.casefold()
            else f"@{self.username}"
        )


@dataclass(frozen=True, slots=True)
class PushRule:
    id: str
    destination_kind: PushDestinationKind
    destination_id: str
    events: tuple[PushEvent, ...]
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class StoredCookie:
    domain: str
    name: str
    value: SecretStr = field(repr=False)


@dataclass(frozen=True, slots=True)
class AccountCredentials:
    login: str
    password: SecretStr = field(repr=False)
    cookies: tuple[StoredCookie, ...] = field(default=(), repr=False)


@dataclass(slots=True)
class GameAccount:
    id: str
    game: str
    credentials: AccountCredentials = field(repr=False)
    status: AccountStatus = "unchecked"
    last_checked_at: str | None = None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class VerificationResult:
    valid: bool
    summary: str


@dataclass(frozen=True, slots=True)
class ImportRun:
    id: str
    created_at: str
    actor_id: str
    game: str
    total: int
    imported: int
    rejected: int
    status: RunStatus
    account_ids: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CheckRun:
    id: str
    created_at: str
    actor_id: str
    scope: str
    total: int
    valid: int
    invalid: int
    errors: int
    status: RunStatus
