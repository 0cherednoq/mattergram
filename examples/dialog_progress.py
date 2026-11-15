"""Declare a durable one-post job progress card with a safe Cancel action."""

from __future__ import annotations

from typing import Any

from mattergram.fsm.state import State, StatesGroup
from mattergram_dialog import (
    Button,
    Case,
    CoalescingDialogUpdater,
    Const,
    DataBlock,
    Dialog,
    DialogRef,
    Format,
    Multi,
    Progress,
    Window,
)
from mattergram_dialog.api.protocols.manager import DialogManager


class Job(StatesGroup):
    progress = State()


async def load_job(data: Any, manager: DialogManager) -> None:
    if isinstance(data, dict):
        manager.dialog_data.update(data)


async def cancel_job(manager: DialogManager) -> None:
    manager.dialog_data.update(
        phase="cancelled",
        stage="Отмена запрошена",
        can_cancel=False,
    )


job_dialog = Dialog(
    Window(
        Const("# Импорт каталога"),
        DataBlock(
            "Состояние задачи",
            Multi(
                Progress(
                    current="done",
                    total="total",
                    label=Format("{stage}"),
                    when=lambda data, manager: data.get("phase") == "running",
                ),
                Case(
                    {
                        "queued": Const("⏳ Задача ожидает запуска"),
                        "succeeded": Format("✅ Готово · обработано {done}"),
                        "failed": Format("❌ Ошибка: {error}"),
                        "cancelled": Const("⏹ Отмена запрошена"),
                    },
                    selector="phase",
                ),
            ),
            Button(
                Const("Отменить импорт"),
                id="cancelImport",
                on_click=cancel_job,
                style="danger",
                when="can_cancel",
            ),
            color="#3d85c6",
        ),
        state=Job.progress,
    ),
    on_start=load_job,
)


def publish_worker_snapshot(
    updates: CoalescingDialogUpdater,
    ref: DialogRef,
    *,
    revision: int,
    done: int,
    total: int,
    stage: str,
) -> bool:
    """Queue one monotonic worker snapshot without blocking the worker."""

    return updates.submit(
        ref,
        revision,
        {
            "phase": "running",
            "done": done,
            "total": total,
            "stage": stage,
            "can_cancel": True,
        },
    )


__all__ = ["Job", "job_dialog", "publish_worker_snapshot"]
