from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from mattergram.ui.forms import Option
from mattergram_dialog import DialogManager

from .mattermost_ui import AccountList
from .models import AccountCheckData, AccountCheckState
from .presentation import format_datetime
from .service import GameAccountsAdminService


@dataclass(frozen=True, slots=True)
class GameAdminGetters:
    """Load render-only data without owning navigation or form side effects."""

    service: GameAccountsAdminService

    @staticmethod
    def _account_check(manager: DialogManager, account_id: str) -> AccountCheckData | None:
        values = manager.dialog_data
        if values.get("account_check_account_id") != account_id:
            return None
        job_id = values.get("account_check_job_id")
        state = values.get("account_check_state")
        stage = values.get("account_check_stage")
        if (
            not isinstance(job_id, str)
            or state not in {"pending", "running", "success", "failed", "cancelled"}
            or not isinstance(stage, str)
        ):
            return None

        def counter(name: str) -> int:
            value = values.get(name, 0)
            return value if type(value) is int and value >= 0 else 0

        total = values.get("account_check_total")
        if type(total) is not int or total < 0:
            total = None
        started_at = values.get("account_check_started_at")
        finished_at = values.get("account_check_finished_at")
        error = values.get("account_check_error")
        return AccountCheckData(
            account_id=account_id,
            job_id=job_id,
            state=cast(AccountCheckState, state),
            stage=stage,
            current=counter("account_check_current"),
            total=total,
            started_at=started_at if isinstance(started_at, str) else None,
            finished_at=finished_at if isinstance(finished_at, str) else None,
            valid=counter("account_check_valid"),
            invalid=counter("account_check_invalid"),
            errors=counter("account_check_errors"),
            error=error if isinstance(error, str) else None,
        )

    async def home(self, manager: DialogManager) -> Mapping[str, Any]:
        return {
            **await self.service.dashboard(manager.actor_id),
            "refreshed_at": format_datetime(
                manager.dialog_data.get("home_refreshed_at"),
                empty="при открытии",
            ),
        }

    async def users(self, manager: DialogManager) -> Mapping[str, Any]:
        available = await self.service.list_available_team_users(manager.actor_id)
        return {
            "users": await self.service.list_users(manager.actor_id),
            "has_available_users": bool(available),
        }

    async def available_user_options(
        self,
        data: Mapping[str, Any],
        manager: DialogManager,
    ) -> tuple[Option, ...]:
        del data
        users = await self.service.list_available_team_users(manager.actor_id)
        return tuple(Option(text=user.option_label, value=user.id) for user in users)

    async def pushes(self, manager: DialogManager) -> Mapping[str, Any]:
        return {"pushes": await self.service.list_push_rules(manager.actor_id)}

    async def accounts(self, manager: DialogManager) -> Mapping[str, Any]:
        account_list = manager.find("adminAccountList")
        if not isinstance(account_list, AccountList):
            raise TypeError("adminAccountList must be an AccountList component")
        page = await self.service.query_accounts(
            manager.actor_id,
            account_list.query(manager),
        )
        selected_card = None
        selected = manager.dialog_data.get("selected_account_id")
        visible_ids = {item.id for item in page.items}
        if isinstance(selected, str) and selected not in visible_ids:
            manager.dialog_data.pop("selected_account_id", None)
            selected = None
        if isinstance(selected, str):
            selected_card = await self.service.account_card(manager.actor_id, selected)
        return {
            "account_page": page,
            "selected_account_card": selected_card,
            "selected_account_check": (
                self._account_check(manager, selected) if isinstance(selected, str) else None
            ),
        }

    async def imports(self, manager: DialogManager) -> Mapping[str, Any]:
        return {"imports": await self.service.list_imports(manager.actor_id)}

    async def checks(self, manager: DialogManager) -> Mapping[str, Any]:
        return {"checks": await self.service.list_checks(manager.actor_id)}

    async def selected_user(self, manager: DialogManager) -> Mapping[str, Any]:
        users = await self.service.list_users(manager.actor_id)
        selected = manager.dialog_data.get("selected_user_id")
        return next((user for user in users if user["id"] == selected), {})

    async def selected_push(self, manager: DialogManager) -> Mapping[str, Any]:
        values = await self.service.list_push_rules(manager.actor_id)
        selected = manager.dialog_data.get("selected_push_id")
        return next((rule for rule in values if rule["id"] == selected), {})

    async def selected_account(self, manager: DialogManager) -> Mapping[str, Any]:
        values = await self.service.list_accounts(manager.actor_id)
        selected = manager.dialog_data.get("selected_account_id")
        return next((account for account in values if account["id"] == selected), {})
