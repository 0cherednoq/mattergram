from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pydantic import SecretStr

from .checks import check_ids
from .credentials import DenyCredentialRevealPolicy
from .errors import AdminInputError
from .imports import import_accounts as run_account_import
from .imports import parse_cookies
from .models import (
    PUSH_EVENTS,
    AccountCardData,
    AccountCredentials,
    AccountListItem,
    AccountListPage,
    AccountListQuery,
    AdminRole,
    AdminUser,
    CheckRun,
    CookieMetadata,
    GameAccount,
    ImportRun,
    PushDestinationKind,
    PushEvent,
    PushRule,
    TeamUser,
    _id,
)
from .ports import (
    AccountVerifier,
    CredentialRevealPolicy,
    ImportFileReader,
    OperationProgress,
    PushDispatcher,
    TeamUserDirectory,
)
from .presentation import (
    ACCOUNT_STATUS_LABELS,
    PUSH_DESTINATION_LABELS,
    PUSH_EVENT_LABELS,
    ROLE_LABELS,
    RUN_STATUS_LABELS,
    format_check_scope,
    format_datetime,
)
from .repository import MemoryAdminRepository


class GameAccountsAdminService:
    """Role-aware game-account service with an explicit secret boundary."""

    def __init__(
        self,
        repository: MemoryAdminRepository,
        *,
        games: Sequence[str],
        verifier: AccountVerifier,
        import_reader: ImportFileReader,
        push_dispatcher: PushDispatcher,
        user_directory: TeamUserDirectory,
        credential_reveal_policy: CredentialRevealPolicy | None = None,
    ) -> None:
        if not games or len(set(games)) != len(games) or any(not game for game in games):
            raise ValueError("games must contain unique nonempty names")
        self.repository = repository
        self.games = tuple(games)
        self.verifier = verifier
        self.import_reader = import_reader
        self.push_dispatcher = push_dispatcher
        self.user_directory = user_directory
        self.credential_reveal_policy = credential_reveal_policy or DenyCredentialRevealPolicy()

    def require_admin(self, actor_id: str) -> AdminUser:
        for user in self.repository.users.values():
            if user.mattermost_user_id == actor_id and user.enabled and user.role == "admin":
                return user
        raise PermissionError("This administrator panel requires an enabled admin user")

    def require_operator(self, actor_id: str) -> AdminUser:
        for user in self.repository.users.values():
            if user.mattermost_user_id == actor_id and user.enabled and user.role in {"admin", "operator"}:
                return user
        raise PermissionError("Game-account operations require an enabled admin or operator")

    async def resolve_role(self, actor_id: str) -> str | None:
        """Resolve an enabled application role for core RBAC middleware."""

        for user in self.repository.users.values():
            if user.mattermost_user_id == actor_id and user.enabled:
                return user.role
        return None

    async def dashboard(self, actor_id: str) -> dict[str, int]:
        self.require_operator(actor_id)
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
                    "role_label": ROLE_LABELS[user.role],
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
        self.require_operator(actor_id)
        async with self.repository.lock:
            values = sorted(
                self.repository.push_rules.values(),
                key=lambda item: (item.destination_kind, item.destination_id),
            )
            return [
                {
                    "id": rule.id,
                    "destination": (
                        f"{PUSH_DESTINATION_LABELS[rule.destination_kind]}: {rule.destination_id}"
                    ),
                    "destination_kind": rule.destination_kind,
                    "destination_id": rule.destination_id,
                    "events": ", ".join(PUSH_EVENT_LABELS[event] for event in rule.events),
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
        self.require_operator(actor_id)
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

    async def import_accounts(
        self,
        actor_id: str,
        *,
        game: str,
        file_id: str,
        progress: OperationProgress | None = None,
    ) -> ImportRun:
        self.require_operator(actor_id)
        return await run_account_import(
            self.repository,
            games=self.games,
            reader=self.import_reader,
            emit=self._emit,
            actor_id=actor_id,
            game=game,
            file_id=file_id,
            progress=progress,
        )

    @staticmethod
    def _account_view(account: GameAccount) -> dict[str, Any]:
        domains = sorted({cookie.domain for cookie in account.credentials.cookies})
        return {
            "id": account.id,
            "game": account.game,
            "login": account.credentials.login,
            "status": account.status,
            "status_label": ACCOUNT_STATUS_LABELS[account.status],
            "filter": account.status,
            "password_state": "сохранён" if account.credentials.password.get_secret_value() else "не задан",
            "cookie_count": len(account.credentials.cookies),
            "cookie_domains": ", ".join(domains) if domains else "нет",
            "last_checked_at": format_datetime(account.last_checked_at, empty="не проверялся"),
            "last_error": account.last_error or "нет",
        }

    async def list_accounts(self, actor_id: str) -> list[dict[str, Any]]:
        self.require_operator(actor_id)
        async with self.repository.lock:
            values = sorted(
                self.repository.accounts.values(),
                key=lambda item: (item.game.casefold(), item.credentials.login.casefold()),
            )
            return [self._account_view(account) for account in values]

    async def query_accounts(
        self,
        actor_id: str,
        query: AccountListQuery,
    ) -> AccountListPage:
        """Filter, sort and slice accounts before rendering a user-specific page."""

        self.require_operator(actor_id)
        async with self.repository.lock:
            values = list(self.repository.accounts.values())
            if query.filter == "credentials":
                values = [
                    account for account in values if bool(account.credentials.password.get_secret_value())
                ]
            elif query.filter == "cookies":
                values = [account for account in values if account.credentials.cookies]

            if query.sort == "newest":
                values.sort(key=lambda account: (account.created_at, account.id), reverse=True)
            elif query.sort == "oldest":
                values.sort(key=lambda account: (account.created_at, account.id))
            elif query.sort == "cookies_desc":
                values.sort(
                    key=lambda account: (
                        len(account.credentials.cookies),
                        account.created_at,
                        account.id,
                    ),
                    reverse=True,
                )
            else:
                values.sort(
                    key=lambda account: (
                        len(account.credentials.cookies),
                        account.created_at,
                        account.id,
                    )
                )

            total = len(values)
            pages = max(1, (total + query.page_size - 1) // query.page_size)
            page = min(query.page, pages - 1)
            start = page * query.page_size
            items = tuple(
                AccountListItem(
                    id=account.id,
                    login=account.credentials.login,
                    has_credentials=bool(account.credentials.password.get_secret_value()),
                    cookie_count=len(account.credentials.cookies),
                    country=account.environment.country or "не указана",
                )
                for account in values[start : start + query.page_size]
            )
            normalized = AccountListQuery(
                page=page,
                page_size=query.page_size,
                filter=query.filter,
                sort=query.sort,
            )
            return AccountListPage(items, page, pages, total, normalized)

    async def can_reveal_credentials(self, actor_id: str, channel_id: str) -> bool:
        self.require_operator(actor_id)
        return await self.credential_reveal_policy.allows(actor_id, channel_id)

    async def account_card(
        self,
        actor_id: str,
        account_id: str,
    ) -> AccountCardData:
        self.require_operator(actor_id)
        async with self.repository.lock:
            account = self.repository.accounts.get(account_id)
            if account is None:
                raise AdminInputError({"account": "Аккаунт больше не существует"})
            return AccountCardData(
                account.id,
                account.credentials.login,
                bool(account.credentials.password.get_secret_value()),
                tuple(CookieMetadata(cookie.domain, cookie.name) for cookie in account.credentials.cookies),
                account.environment,
                account.status,
                account.last_checked_at,
                account.last_error,
            )

    async def update_account(
        self,
        actor_id: str,
        account_id: str,
        *,
        login: str,
        password: str | None,
        cookies_json: str | None,
    ) -> GameAccount:
        self.require_operator(actor_id)
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

    async def delete_account(self, actor_id: str, account_id: str) -> GameAccount:
        """Delete one game account after the caller performed explicit confirmation."""

        self.require_operator(actor_id)
        async with self.repository.lock:
            account = self.repository.accounts.pop(account_id, None)
            if account is None:
                raise AdminInputError({"account": "Аккаунт больше не существует"})
            return account

    async def _check_ids(
        self,
        actor_id: str,
        account_ids: Sequence[str],
        *,
        scope: str,
        progress: OperationProgress | None = None,
    ) -> CheckRun:
        self.require_operator(actor_id)
        return await check_ids(
            self.repository,
            verifier=self.verifier,
            emit=self._emit,
            actor_id=actor_id,
            account_ids=account_ids,
            scope=scope,
            progress=progress,
        )

    async def check_account(
        self,
        actor_id: str,
        account_id: str,
        *,
        progress: OperationProgress | None = None,
    ) -> CheckRun:
        return await self._check_ids(
            actor_id,
            (account_id,),
            scope=f"account:{account_id}",
            progress=progress,
        )

    async def check_all(
        self,
        actor_id: str,
        *,
        progress: OperationProgress | None = None,
    ) -> CheckRun:
        async with self.repository.lock:
            account_ids = tuple(self.repository.accounts)
        return await self._check_ids(actor_id, account_ids, scope="all", progress=progress)

    async def check_import(
        self,
        actor_id: str,
        import_id: str,
        *,
        progress: OperationProgress | None = None,
    ) -> CheckRun:
        self.require_operator(actor_id)
        async with self.repository.lock:
            run = next((item for item in self.repository.imports if item.id == import_id), None)
        if run is None:
            raise AdminInputError({"import_file": "Импорт больше не существует"})
        return await self._check_ids(
            actor_id,
            run.account_ids,
            scope=f"import:{import_id}",
            progress=progress,
        )

    async def list_imports(self, actor_id: str) -> list[dict[str, Any]]:
        self.require_operator(actor_id)
        async with self.repository.lock:
            return [
                {
                    "id": run.id,
                    "created_at": format_datetime(run.created_at, empty="—"),
                    "game": run.game,
                    "result": f"{run.imported}/{run.total}",
                    "status": RUN_STATUS_LABELS[run.status],
                    "filter": run.status,
                    "issues": "; ".join(run.issues) if run.issues else "нет",
                }
                for run in self.repository.imports
            ]

    async def list_checks(self, actor_id: str) -> list[dict[str, Any]]:
        self.require_operator(actor_id)
        async with self.repository.lock:
            return [
                {
                    "id": run.id,
                    "created_at": format_datetime(run.created_at, empty="—"),
                    "scope": format_check_scope(run.scope),
                    "result": (
                        f"действительных {run.valid} · недействительных {run.invalid} · ошибок {run.errors}"
                    ),
                    "status": RUN_STATUS_LABELS[run.status],
                    "filter": run.status,
                    "total": run.total,
                }
                for run in self.repository.checks
            ]
