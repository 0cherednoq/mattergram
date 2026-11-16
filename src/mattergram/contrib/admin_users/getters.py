"""Render-data adapters for Admin Users windows and forms."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mattergram.ui.forms import Option
from mattergram_dialog import DialogManager

from .models import ASSIGN_ROLES, MANAGE_USERS
from .service import AdminUsersInputError, AdminUsersService


class AdminUsersGetters:
    def __init__(self, service: AdminUsersService) -> None:
        self.service = service

    async def users(self, manager: DialogManager) -> Mapping[str, Any]:
        permissions = await self.service.permissions_for(manager.actor_id)
        values = await self.service.list_users(manager.actor_id)
        can_manage = MANAGE_USERS in permissions
        can_assign = ASSIGN_ROLES in permissions
        available = await self.service.list_available_team_users(manager.actor_id) if can_manage else ()
        return {
            "users": tuple(
                {
                    "id": user.id,
                    "mattermost_user_id": user.mattermost_user_id,
                    "username": user.username,
                    "role": user.role,
                    "role_title": self.service.role(user.role).title,
                    "enabled": "да" if user.enabled else "нет",
                    "enabled_bool": user.enabled,
                    "filter": "enabled" if user.enabled else "disabled",
                }
                for user in values
            ),
            "can_manage": can_manage,
            "can_assign": can_assign,
            "can_add": can_manage and bool(available),
            "all_team_users_assigned": can_manage and not available,
        }

    async def available_user_options(
        self,
        data: Mapping[str, Any],
        manager: DialogManager,
    ) -> tuple[Option, ...]:
        del data
        users = await self.service.list_available_team_users(manager.actor_id)
        return tuple(Option(text=user.option_label, value=user.id) for user in users)

    async def form_permissions(self, manager: DialogManager) -> Mapping[str, Any]:
        permissions = await self.service.permissions_for(manager.actor_id)
        return {"can_assign": ASSIGN_ROLES in permissions}

    async def selected_user(self, manager: DialogManager) -> Mapping[str, Any]:
        selected = manager.dialog_data.get("admin_users_selected_id")
        if not isinstance(selected, str):
            raise AdminUsersInputError({"user": "Выберите пользователя на текущей странице"})
        user = await self.service.get_user(manager.actor_id, selected)
        permissions = await self.service.permissions_for(manager.actor_id)
        return {
            "id": user.id,
            "mattermost_user_id": user.mattermost_user_id,
            "username": user.username,
            "role": user.role,
            "enabled": user.enabled,
            "can_assign": ASSIGN_ROLES in permissions,
        }


__all__ = ["AdminUsersGetters"]
