from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import SecretStr

AdminRole = Literal["admin", "operator", "viewer"]


AccountStatus = Literal["unchecked", "valid", "invalid", "error"]


AccountCheckState = Literal["pending", "running", "success", "failed", "cancelled"]


AccountListFilter = Literal["all", "credentials", "cookies"]


AccountListSort = Literal["newest", "oldest", "cookies_desc", "cookies_asc"]


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


@dataclass(frozen=True, slots=True)
class AccountEnvironment:
    ip: str = ""
    country: str = ""
    region: str = ""
    city: str = ""
    user_agent: str = ""


@dataclass(frozen=True, slots=True)
class CookieMetadata:
    domain: str
    name: str


@dataclass(frozen=True, slots=True)
class AccountCardData:
    id: str
    login: str
    has_password: bool
    cookies: tuple[CookieMetadata, ...] = ()
    environment: AccountEnvironment = field(default_factory=AccountEnvironment)
    status: AccountStatus = "unchecked"
    last_checked_at: str | None = None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class AccountCheckData:
    """Secret-free snapshot rendered by the reusable account-check component."""

    account_id: str
    job_id: str
    state: AccountCheckState
    stage: str
    current: int = 0
    total: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    valid: int = 0
    invalid: int = 0
    errors: int = 0
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.account_id or not self.job_id or not self.stage:
            raise ValueError("Account checks require account, job, and stage values")
        if type(self.current) is not int or self.current < 0:
            raise ValueError("Account check current progress must be nonnegative")
        if self.total is not None and (
            type(self.total) is not int or self.total < 0 or self.current > self.total
        ):
            raise ValueError("Account check total must be nonnegative and no smaller than current")
        if any(type(value) is not int or value < 0 for value in (self.valid, self.invalid, self.errors)):
            raise ValueError("Account check result counters must be nonnegative integers")

    @property
    def active(self) -> bool:
        return self.state in {"pending", "running"}


@dataclass(slots=True)
class GameAccount:
    id: str
    game: str
    credentials: AccountCredentials = field(repr=False)
    status: AccountStatus = "unchecked"
    last_checked_at: str | None = None
    last_error: str | None = None
    environment: AccountEnvironment = field(default_factory=AccountEnvironment)
    created_at: str = field(default_factory=_now)


@dataclass(frozen=True, slots=True)
class AccountListQuery:
    page: int = 0
    page_size: int = 5
    filter: AccountListFilter = "all"
    sort: AccountListSort = "newest"

    def __post_init__(self) -> None:
        if type(self.page) is not int or self.page < 0:
            raise ValueError("Account list page must be a nonnegative integer")
        if type(self.page_size) is not int or self.page_size < 1 or self.page_size > 25:
            raise ValueError("Account list page size must be between 1 and 25")
        if self.filter not in {"all", "credentials", "cookies"}:
            raise ValueError("Account list filter is invalid")
        if self.sort not in {"newest", "oldest", "cookies_desc", "cookies_asc"}:
            raise ValueError("Account list sort is invalid")


@dataclass(frozen=True, slots=True)
class AccountListItem:
    id: str
    login: str
    has_credentials: bool
    cookie_count: int
    country: str


@dataclass(frozen=True, slots=True)
class AccountListPage:
    items: tuple[AccountListItem, ...]
    page: int
    pages: int
    total: int
    query: AccountListQuery


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
