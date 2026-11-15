from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pydantic import SecretStr

from .checks import check_ids
from .errors import AdminInputError
from .imports import import_accounts as run_account_import
from .imports import parse_cookies
from .models import (
    PUSH_EVENTS,
    AccountCredentials,
    AdminRole,
    AdminUser,
    CheckRun,
    GameAccount,
    ImportRun,
    PushDestinationKind,
    PushEvent,
    PushRule,
    TeamUser,
    _id,
)
from .ports import AccountVerifier, ImportFileReader, PushDispatcher, TeamUserDirectory
from .repository import MemoryAdminRepository


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

    async def import_accounts(self, actor_id: str, *, game: str, file_id: str) -> ImportRun:
        self.require_admin(actor_id)
        return await run_account_import(
            self.repository,
            games=self.games,
            reader=self.import_reader,
            emit=self._emit,
            actor_id=actor_id,
            game=game,
            file_id=file_id,
        )

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
                    cookies = parse_cookies(json.loads(cookies_json), 1)
                except (json.JSONDecodeError, ValueError) as error:
                    raise AdminInputError({"cookies_json": str(error)}) from None
            secret = account.credentials.password if not password else SecretStr(password)
            account.credentials = AccountCredentials(login, secret, cookies)
            account.status = "unchecked"
            account.last_error = None
            return account

    async def _check_ids(self, actor_id: str, account_ids: Sequence[str], *, scope: str) -> CheckRun:
        self.require_admin(actor_id)
        return await check_ids(
            self.repository,
            verifier=self.verifier,
            emit=self._emit,
            actor_id=actor_id,
            account_ids=account_ids,
            scope=scope,
        )

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
