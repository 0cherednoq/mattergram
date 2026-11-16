from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from mattergram.ui.blocks import Block, Collapsible, Column, ColumnSet, Container, Text
from mattergram_dialog.api.exceptions import InvalidWidget
from mattergram_dialog.rendering import PostControl, PostControlGroup
from mattergram_dialog.widgets.common.action import WidgetAction, WidgetActionResult
from mattergram_dialog.widgets.common.base import Widget
from mattergram_dialog.widgets.common.when import WhenCondition

from ..models import AccountCheckData
from ..presentation import CHECK_STAGE_LABELS, CHECK_STATUS_LABELS, format_datetime


def _progress(check: AccountCheckData) -> str:
    if check.total is None:
        return "Общий объём пока неизвестен"
    if check.total == 0:
        return "Выполнено: 0 / 0"
    filled = round(check.current / check.total * 10)
    bar = "█" * filled + "░" * (10 - filled)
    percent = check.current * 100 // check.total
    return f"{bar}  **{check.current} / {check.total}** · {percent}%"


def build_account_check_blocks(
    check: AccountCheckData,
    *,
    cancel: Block | None = None,
    embedded: bool = False,
) -> tuple[Block, ...]:
    """Build a secret-free status card from factual backend metrics."""

    result = (
        f"**Результат:** действительных {check.valid} · "
        f"недействительных {check.invalid} · ошибок {check.errors}"
        if check.state in {"success", "failed"}
        else "**Результат:** ещё не сформирован"
    )
    detail_items: list[Block] = [
        Text(text=f"**Операция:** `{check.job_id}`"),
        Text(text=f"**Этап:** {CHECK_STAGE_LABELS.get(check.stage, 'Неизвестный этап')}"),
        Text(text=f"**Начало:** {format_datetime(check.started_at, empty='ещё не началась')}"),
        Text(text=f"**Завершение:** {format_datetime(check.finished_at, empty='—')}"),
        Text(text=result),
    ]
    if check.error is not None:
        detail_items.append(Text(text=f"**Ошибка:** {check.error}"))

    content: list[Block] = [
        ColumnSet(
            gap="large",
            columns=(
                Column(
                    width="stretch",
                    items=(Text(text=f"Статус\n**{CHECK_STATUS_LABELS[check.state]}**"),),
                ),
                Column(
                    width="stretch",
                    items=(
                        Text(
                            text=(
                                "Текущий этап\n**"
                                + CHECK_STAGE_LABELS.get(check.stage, "Неизвестный этап")
                                + "**"
                            )
                        ),
                    ),
                ),
            ),
        ),
        Text(text="**Прогресс**\n" + _progress(check)),
        Collapsible(
            collapsed=True,
            header=(Text(text="**Подробности выполнения**"),),
            content=(Container(border=False, gap="small", content=tuple(detail_items)),),
        ),
    ]
    if check.error is not None:
        content.insert(2, Text(text=f"**Ошибка:** {check.error}"))
    if cancel is not None:
        content.append(cancel)
    return (
        Collapsible(
            collapsed=False,
            header=(
                Text(text=("### Проверка" if embedded else f"### Проверка аккаунта `{check.account_id}`")),
            ),
            content=tuple(content),
        ),
    )


CheckHandler = Callable[[Any], Awaitable[None]]


class AccountCheck(Widget):
    """Render one account-check lifecycle and an optional cancellation action."""

    def __init__(
        self,
        *,
        id: str,
        check: str,
        on_cancel: CheckHandler | None = None,
        when: WhenCondition = None,
    ) -> None:
        super().__init__(id=id, when=when)
        if not check:
            raise ValueError("AccountCheck source must be nonempty")
        self.check = check
        self.on_cancel = on_cancel

    async def render_control_groups(
        self,
        data: Mapping[str, Any],
        manager: Any,
    ) -> tuple[PostControlGroup, ...]:
        if not await self.is_visible(data, manager):
            return ()
        check = data.get(self.check)
        if check is None:
            return ()
        if not isinstance(check, AccountCheckData):
            raise InvalidWidget(f"AccountCheck source {self.check!r} must contain AccountCheckData")
        if not check.active or self.on_cancel is None:
            return (
                PostControlGroup(
                    (),
                    text=f"Проверка аккаунта {check.account_id}: {CHECK_STATUS_LABELS[check.state]}",
                    native_blocks=build_account_check_blocks(check),
                ),
            )

        assert self.widget_id is not None
        context = manager.current_context()
        cancel = PostControl(
            id=f"{self.widget_id}Cancel",
            label="Отменить проверку",
            intent_id=context.intent_id,
            source_state=context.state.state,
            action="account_check_cancel",
            widget_id=self.widget_id,
        )

        def layout(bind: Callable[[str], Block]) -> tuple[Block, ...]:
            return build_account_check_blocks(check, cancel=bind(cancel.id))

        return (
            PostControlGroup(
                (cancel,),
                text=f"Проверка аккаунта {check.account_id}: {CHECK_STATUS_LABELS[check.state]}",
                native_control_blocks=layout,
            ),
        )

    async def process_action(
        self,
        action: WidgetAction,
        event: Any,
        manager: Any,
    ) -> WidgetActionResult:
        del event
        if action.type != "account_check_cancel" or self.on_cancel is None:
            raise InvalidWidget(f"AccountCheck cannot process action {action.type!r}")
        await self.on_cancel(manager)
        return WidgetActionResult()


__all__ = ["AccountCheck", "build_account_check_blocks"]
