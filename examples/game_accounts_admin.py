"""Declarative administrator panel for users, pushes and game accounts.

The module contains a working in-memory backend for tests and local demos.
Production applications should implement durable encrypted persistence behind
the same service boundary. Passwords and cookie values never enter dialog
render data.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import SecretStr

from mattergram import ComposerButton, ComposerKeyboard, Dispatcher
from mattergram.dispatcher.router import Router
from mattergram.filters import Command
from mattergram.fsm.state import State, StatesGroup
from mattergram.types import DialogSubmission, Message, SlashCommand
from mattergram.ui.forms import Option
from mattergram_dialog import (
    ActionRow,
    Button,
    ButtonRow,
    Checkbox,
    Const,
    DataBlock,
    Dialog,
    DialogManager,
    DialogRuntime,
    FileInput,
    Filter,
    Format,
    FormWindow,
    MultiSelect,
    OpenForm,
    PagedTable,
    PageItemSelect,
    PagePager,
    Section,
    Select,
    ShowMode,
    StartMode,
    SwitchTo,
    TextArea,
    TextInput,
    Window,
)
from mattergram_dialog.widgets.table import MarkdownColumn

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

game_admin_router = Router(name="game-accounts-admin")


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


class AccountVerifier(Protocol):
    async def verify(self, game: str, credentials: AccountCredentials) -> VerificationResult: ...


class ImportFileReader(Protocol):
    async def read(self, file_id: str) -> bytes: ...


class PushDispatcher(Protocol):
    async def send(self, rule: PushRule, event: PushEvent, text: str) -> None: ...


class TeamUserDirectory(Protocol):
    async def list_users(self) -> Sequence[TeamUser]: ...


class AdminInputError(ValueError):
    def __init__(self, field_errors: Mapping[str, str]) -> None:
        super().__init__("Administrator input is invalid")
        self.field_errors = dict(field_errors)


class MemoryAdminRepository:
    """Explicit non-durable repository for tests and local development."""

    def __init__(self, *, users: Sequence[AdminUser] = ()) -> None:
        self.users = {user.id: user for user in users}
        self.push_rules: dict[str, PushRule] = {}
        self.accounts: dict[str, GameAccount] = {}
        self.imports: list[ImportRun] = []
        self.checks: list[CheckRun] = []
        self.lock = asyncio.Lock()


class GameAccountsAdminService:
    """Administrator-only application service with an explicit secret boundary."""

    def __init__(
        self,
        repository: MemoryAdminRepository,
        *,
        games: Sequence[str],
        verifier: AccountVerifier,
        import_reader: ImportFileReader,
        push_dispatcher: PushDispatcher,
        user_directory: TeamUserDirectory,
    ) -> None:
        if not games or len(set(games)) != len(games) or any(not game for game in games):
            raise ValueError("games must contain unique nonempty names")
        self.repository = repository
        self.games = tuple(games)
        self.verifier = verifier
        self.import_reader = import_reader
        self.push_dispatcher = push_dispatcher
        self.user_directory = user_directory

    def require_admin(self, actor_id: str) -> AdminUser:
        for user in self.repository.users.values():
            if user.mattermost_user_id == actor_id and user.enabled and user.role == "admin":
                return user
        raise PermissionError("This administrator panel requires an enabled admin user")

    async def dashboard(self, actor_id: str) -> dict[str, int]:
        self.require_admin(actor_id)
        async with self.repository.lock:
            return {
                "users": len(self.repository.users),
                "push_rules": len(self.repository.push_rules),
                "accounts": len(self.repository.accounts),
                "invalid_accounts": sum(
                    account.status in {"invalid", "error"} for account in self.repository.accounts.values()
                ),
            }

    async def list_users(self, actor_id: str) -> list[dict[str, Any]]:
        self.require_admin(actor_id)
        async with self.repository.lock:
            users = sorted(self.repository.users.values(), key=lambda item: item.username.casefold())
            return [
                {
                    "id": user.id,
                    "mattermost_user_id": user.mattermost_user_id,
                    "username": user.username,
                    "role": user.role,
                    "enabled": "да" if user.enabled else "нет",
                    "filter": "enabled" if user.enabled else "disabled",
                }
                for user in users
            ]

    async def list_available_team_users(self, actor_id: str) -> list[TeamUser]:
        """Read current active team members and exclude application users already assigned."""

        self.require_admin(actor_id)
        async with self.repository.lock:
            assigned = {user.mattermost_user_id for user in self.repository.users.values()}
        users = list(await self.user_directory.list_users())
        if len({user.id for user in users}) != len(users):
            raise RuntimeError("Mattermost team user directory returned duplicate IDs")
        return sorted(
            (user for user in users if not user.is_bot and user.id not in assigned),
            key=lambda user: (user.username.casefold(), user.id),
        )

    async def add_user(
        self,
        actor_id: str,
        *,
        mattermost_user_id: str,
        role: AdminRole,
        enabled: bool,
    ) -> AdminUser:
        self.require_admin(actor_id)
        mattermost_user_id = mattermost_user_id.strip()
        errors: dict[str, str] = {}
        if not mattermost_user_id:
            errors["mattermost_user_id"] = "Выберите пользователя команды Mattermost"
        if role not in {"admin", "operator", "viewer"}:
            errors["role"] = "Неизвестная роль"
        if errors:
            raise AdminInputError(errors)
        available = await self.list_available_team_users(actor_id)
        selected = next((user for user in available if user.id == mattermost_user_id), None)
        if selected is None:
            raise AdminInputError(
                {
                    "mattermost_user_id": (
                        "Пользователь не входит в выбранную команду, отключён или уже добавлен"
                    )
                }
            )
        async with self.repository.lock:
            if any(user.mattermost_user_id == mattermost_user_id for user in self.repository.users.values()):
                raise AdminInputError({"mattermost_user_id": "Этот Mattermost user ID уже добавлен"})
            user = AdminUser(_id("usr"), mattermost_user_id, selected.username, role, enabled)
            self.repository.users[user.id] = user
            return user

    async def update_user(
        self,
        actor_id: str,
        user_id: str,
        *,
        username: str,
        role: AdminRole,
        enabled: bool,
    ) -> AdminUser:
        self.require_admin(actor_id)
        username = username.strip()
        if not username:
            raise AdminInputError({"username": "Укажите имя пользователя"})
        async with self.repository.lock:
            current = self.repository.users.get(user_id)
            if current is None:
                raise AdminInputError({"username": "Пользователь больше не существует"})
            if current.role == "admin" and current.enabled and (role != "admin" or not enabled):
                other_admins = [
                    user
                    for user in self.repository.users.values()
                    if user.id != current.id and user.role == "admin" and user.enabled
                ]
                if not other_admins:
                    raise AdminInputError(
                        {"enabled": "Нельзя отключить или понизить последнего администратора"}
                    )
            updated = AdminUser(
                current.id,
                current.mattermost_user_id,
                username,
                role,
                enabled,
            )
            self.repository.users[user_id] = updated
            return updated

    async def list_push_rules(self, actor_id: str) -> list[dict[str, Any]]:
        self.require_admin(actor_id)
        async with self.repository.lock:
            values = sorted(
                self.repository.push_rules.values(),
                key=lambda item: (item.destination_kind, item.destination_id),
            )
            return [
                {
                    "id": rule.id,
                    "destination": f"{rule.destination_kind}:{rule.destination_id}",
                    "destination_kind": rule.destination_kind,
                    "destination_id": rule.destination_id,
                    "events": ", ".join(rule.events),
                    "event_values": rule.events,
                    "enabled": "да" if rule.enabled else "нет",
                    "enabled_bool": rule.enabled,
                    "filter": "enabled" if rule.enabled else "disabled",
                }
                for rule in values
            ]

    async def save_push_rule(
        self,
        actor_id: str,
        *,
        rule_id: str | None,
        destination_kind: PushDestinationKind,
        destination_id: str,
        events: Sequence[str],
        enabled: bool,
    ) -> PushRule:
        self.require_admin(actor_id)
        destination_id = destination_id.strip()
        errors: dict[str, str] = {}
        if destination_kind not in {"channel", "direct"}:
            errors["destination_kind"] = "Неизвестный тип назначения"
        if not destination_id:
            errors["destination_id"] = "Укажите точный channel ID или user ID"
        if not events or set(events) - set(PUSH_EVENTS):
            errors["events"] = "Выберите хотя бы одно поддерживаемое событие"
        if errors:
            raise AdminInputError(errors)
        async with self.repository.lock:
            if rule_id is not None and rule_id not in self.repository.push_rules:
                raise AdminInputError({"destination_id": "Настройка больше не существует"})
            rule = PushRule(
                rule_id or _id("push"),
                destination_kind,
                destination_id,
                tuple(event for event in PUSH_EVENTS if event in events),
                enabled,
            )
            self.repository.push_rules[rule.id] = rule
            return rule

    async def _emit(self, event: PushEvent, text: str) -> None:
        async with self.repository.lock:
            rules = [
                rule for rule in self.repository.push_rules.values() if rule.enabled and event in rule.events
            ]
        for rule in rules:
            await self.push_dispatcher.send(rule, event, text)

    @staticmethod
    def _parse_cookies(value: Any, line: int) -> tuple[StoredCookie, ...]:
        if value is None:
            return ()
        if not isinstance(value, list) or len(value) > 500:
            raise ValueError(f"строка {line}: cookies должны быть массивом до 500 элементов")
        result: list[StoredCookie] = []
        for cookie in value:
            if not isinstance(cookie, dict):
                raise ValueError(f"строка {line}: cookie должен быть объектом")
            domain = cookie.get("domain")
            name = cookie.get("name")
            secret = cookie.get("value")
            if (
                not isinstance(domain, str)
                or not domain
                or not isinstance(name, str)
                or not name
                or not isinstance(secret, str)
                or not secret
            ):
                raise ValueError(f"строка {line}: cookie требует domain, name и value")
            result.append(StoredCookie(domain, name, SecretStr(secret)))
        return tuple(result)

    async def import_accounts(self, actor_id: str, *, game: str, file_id: str) -> ImportRun:
        self.require_admin(actor_id)
        if game not in self.games:
            raise AdminInputError({"game": "Неизвестная игра"})
        payload = await self.import_reader.read(file_id)
        if len(payload) > 1_000_000:
            raise AdminInputError({"import_file": "Файл превышает 1 МБ"})
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            raise AdminInputError({"import_file": "Файл должен быть UTF-8 JSONL"}) from None
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines or len(lines) > 500:
            raise AdminInputError({"import_file": "Файл должен содержать от 1 до 500 строк"})
        imported: list[str] = []
        issues: list[str] = []
        async with self.repository.lock:
            existing = {
                (account.game, account.credentials.login.casefold())
                for account in self.repository.accounts.values()
            }
            for number, line in enumerate(lines, start=1):
                try:
                    raw = json.loads(line)
                    if not isinstance(raw, dict):
                        raise ValueError(f"строка {number}: ожидается JSON object")
                    login = raw.get("login")
                    password = raw.get("password")
                    if not isinstance(login, str) or not login.strip():
                        raise ValueError(f"строка {number}: login обязателен")
                    if not isinstance(password, str) or not password:
                        raise ValueError(f"строка {number}: password обязателен")
                    key = (game, login.strip().casefold())
                    if key in existing:
                        raise ValueError(f"строка {number}: такой login уже существует")
                    cookies = self._parse_cookies(raw.get("cookies"), number)
                    account = GameAccount(
                        _id("acc"),
                        game,
                        AccountCredentials(login.strip(), SecretStr(password), cookies),
                    )
                    self.repository.accounts[account.id] = account
                    imported.append(account.id)
                    existing.add(key)
                except (json.JSONDecodeError, ValueError) as error:
                    issues.append(str(error).splitlines()[0])
            status: RunStatus = "completed" if not issues else "partial" if imported else "failed"
            run = ImportRun(
                _id("imp"),
                _now(),
                actor_id,
                game,
                len(lines),
                len(imported),
                len(issues),
                status,
                tuple(imported),
                tuple(issues[:20]),
            )
            self.repository.imports.insert(0, run)
        await self._emit(
            "import.completed" if imported else "import.failed",
            f"Импорт {run.id}: добавлено {run.imported}, отклонено {run.rejected}",
        )
        return run

    @staticmethod
    def _account_view(account: GameAccount) -> dict[str, Any]:
        domains = sorted({cookie.domain for cookie in account.credentials.cookies})
        return {
            "id": account.id,
            "game": account.game,
            "login": account.credentials.login,
            "status": account.status,
            "filter": account.status,
            "password_state": "сохранён" if account.credentials.password.get_secret_value() else "не задан",
            "cookie_count": len(account.credentials.cookies),
            "cookie_domains": ", ".join(domains) if domains else "нет",
            "last_checked_at": account.last_checked_at or "не проверялся",
            "last_error": account.last_error or "нет",
        }

    async def list_accounts(self, actor_id: str) -> list[dict[str, Any]]:
        self.require_admin(actor_id)
        async with self.repository.lock:
            values = sorted(
                self.repository.accounts.values(),
                key=lambda item: (item.game.casefold(), item.credentials.login.casefold()),
            )
            return [self._account_view(account) for account in values]

    async def update_account(
        self,
        actor_id: str,
        account_id: str,
        *,
        login: str,
        password: str | None,
        cookies_json: str | None,
    ) -> GameAccount:
        self.require_admin(actor_id)
        login = login.strip()
        if not login:
            raise AdminInputError({"login": "Логин не может быть пустым"})
        async with self.repository.lock:
            account = self.repository.accounts.get(account_id)
            if account is None:
                raise AdminInputError({"login": "Аккаунт больше не существует"})
            if any(
                other.id != account.id
                and other.game == account.game
                and other.credentials.login.casefold() == login.casefold()
                for other in self.repository.accounts.values()
            ):
                raise AdminInputError({"login": "Такой login уже существует в этой игре"})
            cookies = account.credentials.cookies
            if cookies_json and cookies_json.strip():
                try:
                    cookies = self._parse_cookies(json.loads(cookies_json), 1)
                except (json.JSONDecodeError, ValueError) as error:
                    raise AdminInputError({"cookies_json": str(error)}) from None
            secret = account.credentials.password if not password else SecretStr(password)
            account.credentials = AccountCredentials(login, secret, cookies)
            account.status = "unchecked"
            account.last_error = None
            return account

    async def _check_ids(self, actor_id: str, account_ids: Sequence[str], *, scope: str) -> CheckRun:
        self.require_admin(actor_id)
        async with self.repository.lock:
            accounts = [
                (
                    self.repository.accounts[account_id].id,
                    self.repository.accounts[account_id].game,
                    self.repository.accounts[account_id].credentials,
                    self.repository.accounts[account_id].credentials.login,
                )
                for account_id in account_ids
                if account_id in self.repository.accounts
            ]
        valid = invalid = errors = 0
        for account_id, game, credentials, login in accounts:
            last_error: str | None
            try:
                result = await self.verifier.verify(game, credentials)
            except Exception as error:  # verifier boundary; secret values never enter the message
                account_status: AccountStatus = "error"
                last_error = type(error).__name__
                outcome = "error"
                await self._emit(
                    "account.check_failed",
                    f"Ошибка проверки {game}/{login}",
                )
            else:
                account_status = "valid" if result.valid else "invalid"
                last_error = None if result.valid else self._redact_summary(result.summary, credentials)[:150]
                outcome = "valid" if result.valid else "invalid"
                if not result.valid:
                    await self._emit(
                        "account.invalid",
                        f"Невалидный аккаунт {game}/{login}",
                    )
            async with self.repository.lock:
                account = self.repository.accounts.get(account_id)
                if account is None or account.credentials != credentials:
                    errors += 1
                    continue
                account.status = account_status
                account.last_error = last_error
                account.last_checked_at = _now()
            if outcome == "valid":
                valid += 1
            elif outcome == "invalid":
                invalid += 1
            else:
                errors += 1
        run_status: RunStatus = (
            "failed" if accounts and errors == len(accounts) else "partial" if errors else "completed"
        )
        run = CheckRun(
            _id("chk"),
            _now(),
            actor_id,
            scope,
            len(accounts),
            valid,
            invalid,
            errors,
            run_status,
        )
        async with self.repository.lock:
            self.repository.checks.insert(0, run)
        return run

    @staticmethod
    def _redact_summary(summary: str, credentials: AccountCredentials) -> str:
        redacted = summary
        secrets = (credentials.password.get_secret_value(),) + tuple(
            cookie.value.get_secret_value() for cookie in credentials.cookies
        )
        for secret in sorted((item for item in secrets if item), key=len, reverse=True):
            redacted = redacted.replace(secret, "[secret]")
        return redacted

    async def check_account(self, actor_id: str, account_id: str) -> CheckRun:
        return await self._check_ids(actor_id, (account_id,), scope=f"account:{account_id}")

    async def check_all(self, actor_id: str) -> CheckRun:
        async with self.repository.lock:
            account_ids = tuple(self.repository.accounts)
        return await self._check_ids(actor_id, account_ids, scope="all")

    async def check_import(self, actor_id: str, import_id: str) -> CheckRun:
        self.require_admin(actor_id)
        async with self.repository.lock:
            run = next((item for item in self.repository.imports if item.id == import_id), None)
        if run is None:
            raise AdminInputError({"import_file": "Импорт больше не существует"})
        return await self._check_ids(actor_id, run.account_ids, scope=f"import:{import_id}")

    async def list_imports(self, actor_id: str) -> list[dict[str, Any]]:
        self.require_admin(actor_id)
        async with self.repository.lock:
            return [
                {
                    "id": run.id,
                    "created_at": run.created_at,
                    "game": run.game,
                    "result": f"{run.imported}/{run.total}",
                    "status": run.status,
                    "filter": run.status,
                    "issues": "; ".join(run.issues) if run.issues else "нет",
                }
                for run in self.repository.imports
            ]

    async def list_checks(self, actor_id: str) -> list[dict[str, Any]]:
        self.require_admin(actor_id)
        async with self.repository.lock:
            return [
                {
                    "id": run.id,
                    "created_at": run.created_at,
                    "scope": run.scope,
                    "result": f"valid {run.valid} · invalid {run.invalid} · errors {run.errors}",
                    "status": run.status,
                    "filter": run.status,
                    "total": run.total,
                }
                for run in self.repository.checks
            ]


class AdminPanel(StatesGroup):
    home = State()
    import_menu = State()
    users = State()
    user_add = State()
    user_edit = State()
    pushes = State()
    push_add = State()
    push_edit = State()
    accounts = State()
    account_edit = State()
    import_accounts = State()
    import_result = State()
    import_history = State()
    check_history = State()


GAME_ADMIN_SLASH_ROUTES: dict[str, State] = {
    "overview": AdminPanel.home,
    "accounts": AdminPanel.accounts,
    "import": AdminPanel.import_menu,
    "users": AdminPanel.users,
    "pushes": AdminPanel.pushes,
    "imports": AdminPanel.import_history,
    "checks": AdminPanel.check_history,
}

GAME_ADMIN_KEYBOARD_ROUTES: dict[str, State] = {
    "Обзор": AdminPanel.home,
    "Аккаунты": AdminPanel.accounts,
    "Импорт": AdminPanel.import_menu,
    "Push-настройки": AdminPanel.pushes,
    "История импортов": AdminPanel.import_history,
    "История проверок": AdminPanel.check_history,
}


def build_admin_keyboard() -> ComposerKeyboard:
    """Declare the persistent global navigation for the administrator panel."""

    def button(identifier: str, text: str, *, primary: bool = False) -> ComposerButton:
        return ComposerButton(
            id="gameAdmin" + identifier.title(),
            text=text,
            message=text,
            style="primary" if primary else "default",
        )

    return ComposerKeyboard(
        title="Администрирование игровых аккаунтов",
        rows=(
            (
                button("overview", "Обзор", primary=True),
                button("accounts", "Аккаунты"),
                button("import", "Импорт"),
            ),
            (button("users", "Пользователи"), button("pushes", "Push-настройки")),
            (button("imports", "История импортов"), button("checks", "История проверок")),
        ),
    )


def _selected_visible(target: str, key: str) -> Callable[[Mapping[str, Any], DialogManager], bool]:
    def condition(data: Mapping[str, Any], manager: DialogManager) -> bool:
        selected = manager.dialog_data.get(key)
        table = manager.find(target)
        items, _, _, _ = table.page_items(data, manager)
        return isinstance(selected, str) and any(item.get("id") == selected for item in items)

    return condition


def build_admin_dialog(service: GameAccountsAdminService) -> Dialog:
    """Build the complete administrator panel against one application service."""

    async def home_data(manager: DialogManager) -> Mapping[str, Any]:
        return {
            **await service.dashboard(manager.actor_id),
            "refreshed_at": manager.dialog_data.get("home_refreshed_at", "при открытии"),
        }

    async def users_data(manager: DialogManager) -> Mapping[str, Any]:
        available = await service.list_available_team_users(manager.actor_id)
        return {
            "users": await service.list_users(manager.actor_id),
            "has_available_users": bool(available),
        }

    async def available_user_options(data: Mapping[str, Any], manager: DialogManager) -> tuple[Option, ...]:
        del data
        users = await service.list_available_team_users(manager.actor_id)
        return tuple(Option(text=user.option_label, value=user.id) for user in users)

    async def pushes_data(manager: DialogManager) -> Mapping[str, Any]:
        return {"pushes": await service.list_push_rules(manager.actor_id)}

    async def accounts_data(manager: DialogManager) -> Mapping[str, Any]:
        return {"accounts": await service.list_accounts(manager.actor_id)}

    async def imports_data(manager: DialogManager) -> Mapping[str, Any]:
        return {"imports": await service.list_imports(manager.actor_id)}

    async def checks_data(manager: DialogManager) -> Mapping[str, Any]:
        return {"checks": await service.list_checks(manager.actor_id)}

    async def selected_user_data(manager: DialogManager) -> Mapping[str, Any]:
        users = await service.list_users(manager.actor_id)
        selected = manager.dialog_data.get("selected_user_id")
        return next((user for user in users if user["id"] == selected), {})

    async def selected_push_data(manager: DialogManager) -> Mapping[str, Any]:
        values = await service.list_push_rules(manager.actor_id)
        selected = manager.dialog_data.get("selected_push_id")
        return next((rule for rule in values if rule["id"] == selected), {})

    async def selected_account_data(manager: DialogManager) -> Mapping[str, Any]:
        values = await service.list_accounts(manager.actor_id)
        selected = manager.dialog_data.get("selected_account_id")
        return next((account for account in values if account["id"] == selected), {})

    async def open_user_edit(event: Any, button: Button, manager: DialogManager) -> None:
        del event, button
        await manager.switch_to(AdminPanel.user_edit, show_mode=ShowMode.NO_UPDATE)

    async def open_push_edit(event: Any, button: Button, manager: DialogManager) -> None:
        del event, button
        await manager.switch_to(AdminPanel.push_edit, show_mode=ShowMode.NO_UPDATE)

    async def open_account_edit(event: Any, button: Button, manager: DialogManager) -> None:
        del event, button
        await manager.switch_to(AdminPanel.account_edit, show_mode=ShowMode.NO_UPDATE)

    async def refresh_home(event: Any, button: Button, manager: DialogManager) -> None:
        del event, button
        manager.dialog_data["home_refreshed_at"] = _now()

    async def check_selected(event: Any, button: Button, manager: DialogManager) -> None:
        del event, button
        selected = manager.dialog_data.get("selected_account_id")
        if not isinstance(selected, str):
            return
        run = await service.check_account(manager.actor_id, selected)
        manager.dialog_data["check_summary"] = (
            f"Проверка: valid {run.valid}, invalid {run.invalid}, errors {run.errors}"
        )

    async def check_all(event: Any, button: Button, manager: DialogManager) -> None:
        del event, button
        run = await service.check_all(manager.actor_id)
        manager.dialog_data["check_summary"] = (
            f"Проверено {run.total}: valid {run.valid}, invalid {run.invalid}, errors {run.errors}"
        )

    async def check_imported(event: Any, button: Button, manager: DialogManager) -> None:
        del event, button
        import_id = manager.dialog_data.get("last_import_id")
        if not isinstance(import_id, str):
            return
        run = await service.check_import(manager.actor_id, import_id)
        manager.dialog_data["check_summary"] = (
            f"Проверено из импорта {run.total}: valid {run.valid}, invalid {run.invalid}, errors {run.errors}"
        )

    async def add_user(event: DialogSubmission, manager: DialogManager) -> None:
        try:
            await service.add_user(
                manager.actor_id,
                mattermost_user_id=event.values["mattermost_user_id"],
                role=event.values["role"],
                enabled=event.values["enabled"],
            )
        except AdminInputError as error:
            await event.field_errors(error.field_errors)
            return
        await event.answer()
        await manager.back(show_mode=ShowMode.EDIT)

    async def edit_user(event: DialogSubmission, manager: DialogManager) -> None:
        selected = manager.dialog_data.get("selected_user_id")
        if not isinstance(selected, str):
            await event.validation_error("Выбранный пользователь больше недоступен")
            return
        try:
            await service.update_user(
                manager.actor_id,
                selected,
                username=event.values["username"],
                role=event.values["role"],
                enabled=event.values["enabled"],
            )
        except AdminInputError as error:
            await event.field_errors(error.field_errors)
            return
        await event.answer()
        await manager.back(show_mode=ShowMode.EDIT)

    async def save_push(event: DialogSubmission, manager: DialogManager, *, edit: bool) -> None:
        selected = manager.dialog_data.get("selected_push_id") if edit else None
        try:
            await service.save_push_rule(
                manager.actor_id,
                rule_id=selected if isinstance(selected, str) else None,
                destination_kind=event.values["destination_kind"],
                destination_id=event.values["destination_id"],
                events=event.values["events"],
                enabled=event.values["enabled"],
            )
        except AdminInputError as error:
            await event.field_errors(error.field_errors)
            return
        await event.answer()
        await manager.back(show_mode=ShowMode.EDIT)

    async def add_push(event: DialogSubmission, manager: DialogManager) -> None:
        await save_push(event, manager, edit=False)

    async def edit_push(event: DialogSubmission, manager: DialogManager) -> None:
        await save_push(event, manager, edit=True)

    async def edit_account(event: DialogSubmission, manager: DialogManager) -> None:
        selected = manager.dialog_data.get("selected_account_id")
        if not isinstance(selected, str):
            await event.validation_error("Выбранный аккаунт больше недоступен")
            return
        try:
            await service.update_account(
                manager.actor_id,
                selected,
                login=event.values["login"],
                password=event.values["password"],
                cookies_json=event.values["cookies_json"],
            )
        except AdminInputError as error:
            await event.field_errors(error.field_errors)
            return
        await event.answer()
        await manager.back(show_mode=ShowMode.EDIT)

    async def import_accounts(event: DialogSubmission, manager: DialogManager) -> None:
        if len(event.file_ids) != 1:
            await event.field_errors({"import_file": "Загрузите ровно один JSONL-файл"})
            return
        try:
            run = await service.import_accounts(
                manager.actor_id,
                game=event.values["game"],
                file_id=event.file_ids[0],
            )
        except AdminInputError as error:
            await event.field_errors(error.field_errors)
            return
        manager.dialog_data.update(
            last_import_id=run.id,
            import_total=run.total,
            import_imported=run.imported,
            import_rejected=run.rejected,
            import_status=run.status,
            import_issues="; ".join(run.issues) if run.issues else "нет",
            check_summary=None,
        )
        await event.answer()
        await manager.switch_to(AdminPanel.import_result, show_mode=ShowMode.EDIT)

    role_options = tuple(
        Option(text=label, value=value)
        for label, value in (("Администратор", "admin"), ("Оператор", "operator"), ("Наблюдатель", "viewer"))
    )
    destination_options = (
        Option(text="Канал", value="channel"),
        Option(text="Личное сообщение", value="direct"),
    )
    event_options = tuple(Option(text=value, value=value) for value in PUSH_EVENTS)
    game_options = tuple(Option(text=game, value=game) for game in service.games)

    return Dialog(
        Window(
            Section(
                "Администрирование игровых аккаунтов",
                Const("Пользователи, уведомления, импорт и проверки через reply-команды бота."),
                Format(
                    "**Аккаунты:** {accounts} · проблемных {invalid_accounts}  \n"
                    "**Администраторы:** {users} · push-настроек {push_rules}  \n"
                    "**Сводка обновлена:** {refreshed_at}"
                ),
                Const("Основные разделы доступны в keyboard-меню над полем ввода."),
                level=1,
            ),
            ButtonRow(
                Button(Const("Обновить сводку"), id="adminHomeRefresh", on_click=refresh_home),
                title="Сводка",
            ),
            state=AdminPanel.home,
            getter=home_data,
        ),
        Window(
            Section(
                "Импорт аккаунтов",
                Const(
                    "Mattermost открывает нативную форму только после интерактивного действия. "
                    "Подтвердите открытие формы кнопкой ниже."
                ),
                level=1,
            ),
            ButtonRow(
                OpenForm(
                    Const("Открыть форму импорта"),
                    id="adminImportOpen",
                    state=AdminPanel.import_accounts,
                ),
                SwitchTo(Const("Назад"), id="adminImportMenuBack", state=AdminPanel.home),
                title="Импорт",
            ),
            state=AdminPanel.import_menu,
        ),
        Window(
            Section(
                "Пользователи",
                Const("Доступ к панели выдаётся только активной роли admin приложения."),
                Const(
                    "Все активные пользователи команды уже добавлены в приложение.",
                    when=lambda data, manager: not data["has_available_users"],
                ),
                level=1,
            ),
            DataBlock(
                "Пользователи",
                PagedTable(
                    MarkdownColumn("Пользователь", "username", max_chars=24),
                    MarkdownColumn("Роль", "role", max_chars=12),
                    MarkdownColumn("Активен", "enabled", max_chars=8),
                    id="adminUsersTable",
                    items="users",
                    page_size=8,
                    filter_by="filter",
                    empty=Const("Пользователей нет"),
                    page_label="Страница {page}/{pages} · всего {total}",
                ),
                Filter(
                    id="adminUsersFilter",
                    target="adminUsersTable",
                    options={"Все": None, "Активные": "enabled", "Отключённые": "disabled"},
                ),
                ActionRow(
                    PagePager(id="adminUsersPager", target="adminUsersTable"),
                    PageItemSelect(
                        id="adminUserSelect",
                        target="adminUsersTable",
                        item_id="id",
                        item_label="username",
                        selection_key="selected_user_id",
                        placeholder="Выбрать пользователя",
                        detail_title=Format("Пользователь {item[username]}"),
                        detail=Format(
                            "**Mattermost ID:** {item[mattermost_user_id]}  \n"
                            "**Роль:** {item[role]} · **активен:** {item[enabled]}"
                        ),
                    ),
                ),
                Button(
                    Const("Изменить выбранного"),
                    id="adminUserEdit",
                    on_click=open_user_edit,
                    when=_selected_visible("adminUsersTable", "selected_user_id"),
                ),
            ),
            ButtonRow(
                OpenForm(
                    Const("Добавить пользователя"),
                    id="adminUserAdd",
                    state=AdminPanel.user_add,
                    when="has_available_users",
                ),
                SwitchTo(Const("Назад"), id="adminUsersBack", state=AdminPanel.home),
                title="Навигация",
            ),
            state=AdminPanel.users,
            getter=users_data,
        ),
        FormWindow(
            Select(
                id="mattermost_user_id",
                label="Пользователь",
                options=available_user_options,
                placeholder="Выберите участника команды",
                help_text="Список содержит активных людей выбранной Mattermost-команды.",
            ),
            Select(id="role", label="Роль", options=role_options, default="operator"),
            Checkbox(id="enabled", label="Активен", default=True),
            title=Const("Добавить пользователя"),
            submit_text=Const("Добавить"),
            on_submit=add_user,
            callback_id="game_admin_user_add",
            state=AdminPanel.user_add,
        ),
        FormWindow(
            TextInput(id="username", label="Имя", max_length=64, default=Format("{username}")),
            Select(id="role", label="Роль", options=role_options, default=lambda data, manager: data["role"]),
            Checkbox(id="enabled", label="Активен", default=lambda data, manager: data["enabled"] == "да"),
            title=Format("Изменить {username}"),
            introduction=Format("Mattermost ID: {mattermost_user_id}"),
            submit_text=Const("Сохранить"),
            on_submit=edit_user,
            getter=selected_user_data,
            callback_id="game_admin_user_edit",
            state=AdminPanel.user_edit,
        ),
        Window(
            Section("Push-настройки", Const("Куда отправлять уведомления и на какие события."), level=1),
            DataBlock(
                "Маршруты уведомлений",
                PagedTable(
                    MarkdownColumn("Назначение", "destination", max_chars=30),
                    MarkdownColumn("События", "events", max_chars=46),
                    MarkdownColumn("Включено", "enabled", max_chars=8),
                    id="adminPushTable",
                    items="pushes",
                    page_size=6,
                    filter_by="filter",
                    empty=Const("Push-маршрутов нет"),
                    page_label="Страница {page}/{pages} · всего {total}",
                ),
                Filter(
                    id="adminPushFilter",
                    target="adminPushTable",
                    options={"Все": None, "Включённые": "enabled", "Отключённые": "disabled"},
                ),
                ActionRow(
                    PagePager(id="adminPushPager", target="adminPushTable"),
                    PageItemSelect(
                        id="adminPushSelect",
                        target="adminPushTable",
                        item_id="id",
                        item_label="destination",
                        selection_key="selected_push_id",
                        placeholder="Выбрать маршрут",
                        detail_title=Format("Push {item[destination]}"),
                        detail=Format("**События:** {item[events]}  \n**Включено:** {item[enabled]}"),
                    ),
                ),
                Button(
                    Const("Изменить маршрут"),
                    id="adminPushEdit",
                    on_click=open_push_edit,
                    when=_selected_visible("adminPushTable", "selected_push_id"),
                ),
            ),
            ButtonRow(
                OpenForm(Const("Добавить маршрут"), id="adminPushAdd", state=AdminPanel.push_add),
                SwitchTo(Const("Назад"), id="adminPushBack", state=AdminPanel.home),
                title="Навигация",
            ),
            state=AdminPanel.pushes,
            getter=pushes_data,
        ),
        FormWindow(
            Select(id="destination_kind", label="Куда", options=destination_options, default="channel"),
            TextInput(id="destination_id", label="Channel ID / user ID", max_length=64),
            MultiSelect(id="events", label="События", options=event_options, default_values=PUSH_EVENTS),
            Checkbox(id="enabled", label="Включено", default=True),
            title=Const("Добавить push-маршрут"),
            submit_text=Const("Добавить"),
            on_submit=add_push,
            callback_id="game_admin_push_add",
            state=AdminPanel.push_add,
        ),
        FormWindow(
            Select(
                id="destination_kind",
                label="Куда",
                options=destination_options,
                default=lambda data, manager: data["destination_kind"],
            ),
            TextInput(
                id="destination_id",
                label="Channel ID / user ID",
                max_length=64,
                default=Format("{destination_id}"),
            ),
            MultiSelect(
                id="events",
                label="События",
                options=event_options,
                default_values=lambda data, manager: data["event_values"],
            ),
            Checkbox(
                id="enabled",
                label="Включено",
                default=lambda data, manager: data["enabled_bool"],
            ),
            title=Const("Изменить push-маршрут"),
            submit_text=Const("Сохранить"),
            on_submit=edit_push,
            getter=selected_push_data,
            callback_id="game_admin_push_edit",
            state=AdminPanel.push_edit,
        ),
        Window(
            Section(
                "Игровые аккаунты",
                Const("Пароли и значения cookies не показываются ни в таблице, ни в карточке."),
                Format("{check_summary}", when="check_summary"),
                level=1,
            ),
            DataBlock(
                "Аккаунты",
                PagedTable(
                    MarkdownColumn("Игра", "game", max_chars=18),
                    MarkdownColumn("Логин", "login", max_chars=28),
                    MarkdownColumn("Статус", "status", max_chars=10),
                    MarkdownColumn("Cookies", "cookie_count", max_chars=8, align="right"),
                    id="adminAccountsTable",
                    items="accounts",
                    page_size=6,
                    filter_by="filter",
                    empty=Const("Аккаунтов нет"),
                    page_label="Страница {page}/{pages} · всего {total}",
                ),
                Filter(
                    id="adminAccountsFilter",
                    target="adminAccountsTable",
                    options={
                        "Все": None,
                        "Не проверены": "unchecked",
                        "Валидные": "valid",
                        "Невалидные": "invalid",
                        "Ошибки": "error",
                    },
                ),
                ActionRow(
                    PagePager(id="adminAccountsPager", target="adminAccountsTable"),
                    PageItemSelect(
                        id="adminAccountSelect",
                        target="adminAccountsTable",
                        item_id="id",
                        item_label="login",
                        selection_key="selected_account_id",
                        placeholder="Карточка аккаунта",
                        detail_title=Format("{item[game]} · {item[login]}"),
                        detail=Format(
                            "**Статус:** {item[status]}  \n"
                            "**Пароль:** {item[password_state]} (значение скрыто)  \n"
                            "**Cookies:** {item[cookie_count]} · домены: {item[cookie_domains]}  \n"
                            "**Последняя проверка:** {item[last_checked_at]}  \n"
                            "**Последняя ошибка:** {item[last_error]}"
                        ),
                    ),
                ),
                ActionRow(
                    Button(
                        Const("Проверить выбранный"),
                        id="adminAccountCheck",
                        on_click=check_selected,
                        when=_selected_visible("adminAccountsTable", "selected_account_id"),
                    ),
                    Button(
                        Const("Изменить учётные данные"),
                        id="adminAccountEdit",
                        on_click=open_account_edit,
                        when=_selected_visible("adminAccountsTable", "selected_account_id"),
                    ),
                ),
            ),
            ButtonRow(
                Button(Const("Проверить все"), id="adminCheckAll", on_click=check_all),
                OpenForm(Const("Импортировать"), id="adminAccountsImport", state=AdminPanel.import_accounts),
                SwitchTo(Const("Назад"), id="adminAccountsBack", state=AdminPanel.home),
                title="Навигация",
            ),
            state=AdminPanel.accounts,
            getter=accounts_data,
        ),
        FormWindow(
            TextInput(id="login", label="Логин", max_length=150, default=Format("{login}")),
            TextInput(
                id="password",
                label="Новый пароль",
                subtype="password",
                max_length=150,
                optional=True,
                help_text="Оставьте пустым, чтобы сохранить текущий пароль",
            ),
            TextArea(
                id="cookies_json",
                label="Новые cookies JSON",
                max_length=3000,
                optional=True,
                help_text="Массив объектов domain/name/value; пусто — сохранить текущие cookies",
            ),
            title=Format("Учётные данные {login}"),
            introduction=Const("Текущий пароль и значения cookies намеренно не подставляются в форму."),
            submit_text=Const("Сохранить"),
            on_submit=edit_account,
            getter=selected_account_data,
            callback_id="game_admin_account_edit",
            state=AdminPanel.account_edit,
        ),
        FormWindow(
            Select(id="game", label="Игра", options=game_options),
            FileInput(
                id="import_file",
                label="JSONL-файл",
                help_text=(
                    'Одна строка: {"login":"...","password":"...",'
                    '"cookies":[{"domain":"...","name":"...","value":"..."}]}'
                ),
            ),
            title=Const("Импорт аккаунтов"),
            introduction=Const(
                "До 500 строк и 1 МБ. Сырые пароли/cookies не сохраняются в истории и не выводятся."
            ),
            submit_text=Const("Импортировать"),
            on_submit=import_accounts,
            callback_id="game_admin_import",
            state=AdminPanel.import_accounts,
        ),
        Window(
            Section(
                "Импорт завершён",
                Format(
                    "**Статус:** {import_status}  \n**Всего:** {import_total} · "
                    "**добавлено:** {import_imported} · **отклонено:** {import_rejected}"
                ),
                Format("**Ошибки:** {import_issues}"),
                Format("{check_summary}", when="check_summary"),
                level=1,
            ),
            ButtonRow(
                Button(
                    Const("Проверить импортированные"),
                    id="adminCheckImported",
                    on_click=check_imported,
                    when=lambda data, manager: data.get("import_imported", 0) > 0,
                ),
                SwitchTo(Const("Открыть аккаунты"), id="adminResultAccounts", state=AdminPanel.accounts),
                SwitchTo(
                    Const("История импортов"),
                    id="adminResultHistory",
                    state=AdminPanel.import_history,
                ),
                SwitchTo(Const("Главная"), id="adminResultHome", state=AdminPanel.home),
                title="Дальше",
            ),
            state=AdminPanel.import_result,
        ),
        Window(
            Section("История импортов", Const("Без паролей, cookie values и сырого файла."), level=1),
            DataBlock(
                "Импорты",
                PagedTable(
                    MarkdownColumn("Дата", "created_at", max_chars=25),
                    MarkdownColumn("Игра", "game", max_chars=18),
                    MarkdownColumn("Результат", "result", max_chars=12),
                    MarkdownColumn("Статус", "status", max_chars=10),
                    id="adminImportsTable",
                    items="imports",
                    page_size=8,
                    filter_by="filter",
                    empty=Const("Импортов ещё не было"),
                    page_label="Страница {page}/{pages} · всего {total}",
                ),
                Filter(
                    id="adminImportsFilter",
                    target="adminImportsTable",
                    options={
                        "Все": None,
                        "Успешные": "completed",
                        "Частичные": "partial",
                        "Ошибки": "failed",
                    },
                ),
                ActionRow(
                    PagePager(id="adminImportsPager", target="adminImportsTable"),
                    PageItemSelect(
                        id="adminImportSelect",
                        target="adminImportsTable",
                        item_id="id",
                        item_label="created_at",
                        selection_key="selected_import_id",
                        placeholder="Детали импорта",
                        detail_title=Format("Импорт {item[id]}"),
                        detail=Format(
                            "**Игра:** {item[game]} · **результат:** {item[result]}  \n"
                            "**Статус:** {item[status]}  \n**Ошибки:** {item[issues]}"
                        ),
                    ),
                ),
            ),
            ButtonRow(
                SwitchTo(Const("Назад"), id="adminImportsBack", state=AdminPanel.home),
                title="Навигация",
            ),
            state=AdminPanel.import_history,
            getter=imports_data,
        ),
        Window(
            Section("История проверок", Const("Отдельные и пакетные проверки аккаунтов."), level=1),
            DataBlock(
                "Проверки",
                PagedTable(
                    MarkdownColumn("Дата", "created_at", max_chars=25),
                    MarkdownColumn("Область", "scope", max_chars=28),
                    MarkdownColumn("Результат", "result", max_chars=42),
                    MarkdownColumn("Статус", "status", max_chars=10),
                    id="adminChecksTable",
                    items="checks",
                    page_size=8,
                    filter_by="filter",
                    empty=Const("Проверок ещё не было"),
                    page_label="Страница {page}/{pages} · всего {total}",
                ),
                Filter(
                    id="adminChecksFilter",
                    target="adminChecksTable",
                    options={
                        "Все": None,
                        "Успешные": "completed",
                        "Частичные": "partial",
                        "Ошибки": "failed",
                    },
                ),
                ActionRow(
                    PagePager(id="adminChecksPager", target="adminChecksTable"),
                    PageItemSelect(
                        id="adminCheckSelect",
                        target="adminChecksTable",
                        item_id="id",
                        item_label="created_at",
                        selection_key="selected_check_id",
                        placeholder="Детали проверки",
                        detail_title=Format("Проверка {item[id]}"),
                        detail=Format(
                            "**Область:** {item[scope]} · **аккаунтов:** {item[total]}  \n"
                            "**Результат:** {item[result]} · **статус:** {item[status]}"
                        ),
                    ),
                ),
            ),
            ButtonRow(
                SwitchTo(Const("Назад"), id="adminChecksBack", state=AdminPanel.home),
                title="Навигация",
            ),
            state=AdminPanel.check_history,
            getter=checks_data,
        ),
    )


@dataclass(frozen=True, slots=True)
class GameAdminHandlerContext:
    """Dependencies injected into module-level game-admin handlers."""

    runtime: DialogRuntime
    service: GameAccountsAdminService


def _requested_slash_state(text: str) -> State:
    route = text.strip().split(maxsplit=1)
    return GAME_ADMIN_SLASH_ROUTES.get(route[0] if route else "", AdminPanel.home)


def _is_keyboard_command(event: Message) -> bool:
    return event.text.strip() in GAME_ADMIN_KEYBOARD_ROUTES


@game_admin_router.slash_command(Command("game-admin"))
async def open_game_admin(event: SlashCommand, game_admin: GameAdminHandlerContext) -> None:
    """Open the panel from the globally registered native slash command."""

    game_admin.service.require_admin(event.user_id)
    await game_admin.runtime.start(
        event,
        _requested_slash_state(event.text),
        mode=StartMode.RESET_STACK,
    )
    await event.answer()


@game_admin_router.message(_is_keyboard_command)
async def respond_to_game_admin_keyboard(
    event: Message,
    game_admin: GameAdminHandlerContext,
) -> None:
    """Create a fresh panel post for one visible reply-keyboard message."""

    game_admin.service.require_admin(event.user_id)
    await game_admin.runtime.respond(event, GAME_ADMIN_KEYBOARD_ROUTES[event.text.strip()])


def register_admin_panel(
    dispatcher: Dispatcher,
    runtime: DialogRuntime,
    service: GameAccountsAdminService,
    *,
    command: str = "game-admin",
) -> None:
    """Bind dependencies and include the globally declared game-admin router."""

    if command != "game-admin":
        raise ValueError("The global game-admin router owns only the game-admin slash command")
    context = GameAdminHandlerContext(runtime, service)
    current = dispatcher.workflow_data.get("game_admin")
    if current is not None and current != context:
        raise RuntimeError("Another game-admin handler context is already bound")
    dispatcher.workflow_data["game_admin"] = context
    if game_admin_router.parent_router is None:
        dispatcher.include_router(game_admin_router)
    elif game_admin_router.parent_router is not dispatcher:
        raise RuntimeError("The global game-admin router is already attached to another dispatcher")


__all__ = [
    "AccountCredentials",
    "AccountVerifier",
    "AdminInputError",
    "AdminPanel",
    "AdminUser",
    "CheckRun",
    "GameAccount",
    "GameAdminHandlerContext",
    "GameAccountsAdminService",
    "ImportFileReader",
    "ImportRun",
    "MemoryAdminRepository",
    "PUSH_EVENTS",
    "PushDispatcher",
    "PushRule",
    "StoredCookie",
    "TeamUser",
    "TeamUserDirectory",
    "VerificationResult",
    "build_admin_keyboard",
    "build_admin_dialog",
    "register_admin_panel",
    "game_admin_router",
]
