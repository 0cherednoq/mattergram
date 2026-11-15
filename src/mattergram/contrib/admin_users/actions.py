"""Form actions for Admin Users use cases."""

from __future__ import annotations

from mattergram.types import DialogSubmission
from mattergram_dialog import DialogManager, ShowMode

from .service import AdminUsersInputError, AdminUsersService


class AdminUsersActions:
    def __init__(self, service: AdminUsersService) -> None:
        self.service = service

    async def add(self, event: DialogSubmission, manager: DialogManager) -> None:
        try:
            await self.service.add_user(
                manager.actor_id,
                mattermost_user_id=event.values["mattermost_user_id"],
                role=event.values["role"],
                enabled=event.values["enabled"],
            )
        except AdminUsersInputError as error:
            await event.field_errors(error.field_errors)
            return
        await event.answer()
        await manager.back(show_mode=ShowMode.EDIT)

    async def edit(self, event: DialogSubmission, manager: DialogManager) -> None:
        selected = manager.dialog_data.get("admin_users_selected_id")
        if not isinstance(selected, str):
            await event.validation_error("Выбранный пользователь больше недоступен")
            return
        try:
            await self.service.update_user(
                manager.actor_id,
                selected,
                role=event.values["role"],
                enabled=event.values["enabled"],
            )
        except AdminUsersInputError as error:
            await event.field_errors(error.field_errors)
            return
        await event.answer()
        await manager.back(show_mode=ShowMode.EDIT)


__all__ = ["AdminUsersActions"]
