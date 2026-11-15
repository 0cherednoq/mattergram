from __future__ import annotations

from dataclasses import dataclass

from mattergram.types import DialogSubmission
from mattergram_dialog import DialogManager, ShowMode

from .errors import AdminInputError
from .models import _now
from .service import GameAccountsAdminService
from .states import AdminPanel


@dataclass(frozen=True, slots=True)
class GameAdminActions:
    """Apply business actions and form submissions for the dialog declaration."""

    service: GameAccountsAdminService

    async def refresh_home(self, manager: DialogManager) -> None:
        manager.dialog_data["home_refreshed_at"] = _now()

    async def check_selected(self, manager: DialogManager) -> None:
        selected = manager.dialog_data.get("selected_account_id")
        if not isinstance(selected, str):
            return
        run = await self.service.check_account(manager.actor_id, selected)
        manager.dialog_data["check_summary"] = (
            f"Проверка: valid {run.valid}, invalid {run.invalid}, errors {run.errors}"
        )

    async def check_all(self, manager: DialogManager) -> None:
        run = await self.service.check_all(manager.actor_id)
        manager.dialog_data["check_summary"] = (
            f"Проверено {run.total}: valid {run.valid}, invalid {run.invalid}, errors {run.errors}"
        )

    async def check_imported(self, manager: DialogManager) -> None:
        import_id = manager.dialog_data.get("last_import_id")
        if not isinstance(import_id, str):
            return
        run = await self.service.check_import(manager.actor_id, import_id)
        manager.dialog_data["check_summary"] = (
            f"Проверено из импорта {run.total}: valid {run.valid}, invalid {run.invalid}, errors {run.errors}"
        )

    async def add_user(self, event: DialogSubmission, manager: DialogManager) -> None:
        try:
            await self.service.add_user(
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

    async def edit_user(self, event: DialogSubmission, manager: DialogManager) -> None:
        selected = manager.dialog_data.get("selected_user_id")
        if not isinstance(selected, str):
            await event.validation_error("Выбранный пользователь больше недоступен")
            return
        try:
            await self.service.update_user(
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

    async def save_push(
        self,
        event: DialogSubmission,
        manager: DialogManager,
        *,
        edit: bool,
    ) -> None:
        selected = manager.dialog_data.get("selected_push_id") if edit else None
        try:
            await self.service.save_push_rule(
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

    async def add_push(self, event: DialogSubmission, manager: DialogManager) -> None:
        await self.save_push(event, manager, edit=False)

    async def edit_push(self, event: DialogSubmission, manager: DialogManager) -> None:
        await self.save_push(event, manager, edit=True)

    async def edit_account(self, event: DialogSubmission, manager: DialogManager) -> None:
        selected = manager.dialog_data.get("selected_account_id")
        if not isinstance(selected, str):
            await event.validation_error("Выбранный аккаунт больше недоступен")
            return
        try:
            await self.service.update_account(
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

    async def import_accounts(self, event: DialogSubmission, manager: DialogManager) -> None:
        if len(event.file_ids) != 1:
            await event.field_errors({"import_file": "Загрузите ровно один JSONL-файл"})
            return
        try:
            run = await self.service.import_accounts(
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
