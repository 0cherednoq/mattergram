from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from mattergram.ui.forms import Option
from mattergram_dialog import DialogManager

from .service import GameAccountsAdminService


@dataclass(frozen=True, slots=True)
class GameAdminGetters:
    """Load render-only data without owning navigation or form side effects."""

    service: GameAccountsAdminService

    async def home(self, manager: DialogManager) -> Mapping[str, Any]:
        return {
            **await self.service.dashboard(manager.actor_id),
            "refreshed_at": manager.dialog_data.get("home_refreshed_at", "при открытии"),
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
        return {"accounts": await self.service.list_accounts(manager.actor_id)}

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
