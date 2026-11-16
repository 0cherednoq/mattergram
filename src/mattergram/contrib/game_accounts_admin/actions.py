from __future__ import annotations

from dataclasses import dataclass

from mattergram.types import DialogSubmission
from mattergram_dialog import DialogManager, ShowMode

from .errors import AdminInputError
from .jobs import (
    ActiveJobConflict,
    GameAdminJob,
    GameAdminJobs,
    account_check_dialog_data,
    dialog_ref_from_manager,
)
from .models import _id, _now
from .service import GameAccountsAdminService
from .states import AdminPanel


@dataclass(frozen=True, slots=True)
class GameAdminActions:
    """Apply business actions and form submissions for the dialog declaration."""

    service: GameAccountsAdminService
    jobs: GameAdminJobs | None = None

    @staticmethod
    def _show_job(manager: DialogManager, job: GameAdminJob) -> None:
        manager.dialog_data.update(
            job_id=job.id,
            job_state=job.state,
            job_stage=job.stage,
            job_current=job.current,
            job_total=job.total if job.total else None,
            job_cancel_requested=job.cancel_requested,
            job_error_type=job.error_type,
        )

    @classmethod
    def _show_account_job(cls, manager: DialogManager, job: GameAdminJob) -> None:
        cls._show_job(manager, job)
        manager.dialog_data.update(account_check_dialog_data(job))

    @staticmethod
    async def _refresh_account_check(manager: DialogManager) -> None:
        show = getattr(manager, "show", None)
        if show is not None:
            await show(ShowMode.EDIT)

    async def refresh_home(self, manager: DialogManager) -> None:
        manager.dialog_data["home_refreshed_at"] = _now()

    async def check_selected(self, manager: DialogManager) -> None:
        selected = manager.dialog_data.get("selected_account_id")
        if not isinstance(selected, str):
            return
        if self.jobs is not None:
            try:
                job = await self.jobs.check_account(
                    manager.actor_id,
                    dialog_ref_from_manager(manager),
                    selected,
                )
            except ActiveJobConflict as error:
                job = error.job
            self._show_account_job(manager, job)
            return

        operation_id = _id("local_check")
        manager.dialog_data.update(
            account_check_account_id=selected,
            account_check_job_id=operation_id,
            account_check_state="pending",
            account_check_stage="queued",
            account_check_current=0,
            account_check_total=None,
            account_check_started_at=None,
            account_check_finished_at=None,
            account_check_valid=0,
            account_check_invalid=0,
            account_check_errors=0,
            account_check_error=None,
        )
        await self._refresh_account_check(manager)

        async def report(current: int, total: int, stage: str) -> None:
            manager.dialog_data.update(
                account_check_state="running",
                account_check_stage=stage,
                account_check_current=current,
                account_check_total=total,
                account_check_started_at=(manager.dialog_data.get("account_check_started_at") or _now()),
            )
            await self._refresh_account_check(manager)

        run = await self.service.check_account(manager.actor_id, selected, progress=report)
        manager.dialog_data.update(
            account_check_state="success",
            account_check_stage="completed",
            account_check_finished_at=_now(),
            account_check_valid=run.valid,
            account_check_invalid=run.invalid,
            account_check_errors=run.errors,
        )
        manager.dialog_data["check_summary"] = (
            f"Проверка: действительных {run.valid}, недействительных {run.invalid}, ошибок {run.errors}"
        )
        await self._refresh_account_check(manager)

    async def cancel_selected_check(self, manager: DialogManager) -> None:
        if self.jobs is None:
            return
        job_id = manager.dialog_data.get("account_check_job_id")
        if not isinstance(job_id, str):
            return
        job = await self.jobs.cancel(manager.actor_id, job_id)
        self._show_account_job(manager, job)

    async def check_all(self, manager: DialogManager) -> None:
        if self.jobs is not None:
            job = await self.jobs.check_all(manager.actor_id, dialog_ref_from_manager(manager))
            self._show_job(manager, job)
            await manager.switch_to(AdminPanel.job_progress, show_mode=ShowMode.NO_UPDATE)
            return
        run = await self.service.check_all(manager.actor_id)
        manager.dialog_data["check_summary"] = (
            f"Проверено {run.total}: действительных {run.valid}, "
            f"недействительных {run.invalid}, ошибок {run.errors}"
        )

    async def check_imported(self, manager: DialogManager) -> None:
        import_id = manager.dialog_data.get("last_import_id")
        if not isinstance(import_id, str):
            return
        if self.jobs is not None:
            job = await self.jobs.check_import(
                manager.actor_id,
                dialog_ref_from_manager(manager),
                import_id,
            )
            self._show_job(manager, job)
            await manager.switch_to(AdminPanel.job_progress, show_mode=ShowMode.NO_UPDATE)
            return
        run = await self.service.check_import(manager.actor_id, import_id)
        manager.dialog_data["check_summary"] = (
            f"Проверено из импорта {run.total}: действительных {run.valid}, "
            f"недействительных {run.invalid}, ошибок {run.errors}"
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

    async def delete_account(self, event: DialogSubmission, manager: DialogManager) -> None:
        selected = manager.dialog_data.get("selected_account_id")
        if not isinstance(selected, str):
            await event.validation_error("Выбранный аккаунт больше недоступен")
            return
        if not event.values["confirm_account_delete"]:
            await event.field_errors({"confirm_account_delete": "Подтвердите удаление выбранного аккаунта"})
            return
        try:
            await self.service.delete_account(manager.actor_id, selected)
        except AdminInputError:
            await event.validation_error("Выбранный аккаунт больше недоступен")
            return
        manager.dialog_data.pop("selected_account_id", None)
        for key in tuple(manager.dialog_data):
            if key.startswith("account_check_"):
                manager.dialog_data.pop(key, None)
        await event.answer()
        await manager.back(show_mode=ShowMode.EDIT)

    async def import_accounts(self, event: DialogSubmission, manager: DialogManager) -> None:
        if len(event.file_ids) != 1:
            await event.field_errors({"import_file": "Загрузите ровно один JSONL-файл"})
            return
        if self.jobs is not None:
            job = await self.jobs.import_accounts(
                manager.actor_id,
                dialog_ref_from_manager(manager),
                game=event.values["game"],
                file_id=event.file_ids[0],
            )
            self._show_job(manager, job)
            await event.answer()
            await manager.switch_to(AdminPanel.job_progress, show_mode=ShowMode.EDIT)
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

    async def cancel_job(self, manager: DialogManager) -> None:
        if self.jobs is None:
            return
        job_id = manager.dialog_data.get("job_id")
        if not isinstance(job_id, str):
            return
        job = await self.jobs.cancel(manager.actor_id, job_id)
        self._show_job(manager, job)
