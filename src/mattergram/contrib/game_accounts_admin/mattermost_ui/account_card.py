from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from mattergram.fsm.state import State
from mattergram.ui.blocks import Block, Collapsible, Column, ColumnSet, Container, Divider, Text
from mattergram_dialog.api.exceptions import InvalidWidget
from mattergram_dialog.rendering import PostControl, PostControlGroup
from mattergram_dialog.widgets.common.action import WidgetAction, WidgetActionResult
from mattergram_dialog.widgets.common.base import Widget
from mattergram_dialog.widgets.common.when import WhenCondition

from ..models import AccountCardData, AccountCheckData
from ..presentation import (
    ACCOUNT_STATUS_LABELS,
    CHECK_STAGE_LABELS,
    CHECK_STATUS_LABELS,
    format_datetime,
)
from .account_check import build_account_check_blocks

_TEXT_LIMIT = 16_000


def _fenced(value: str, language: str = "") -> str:
    longest = max((len(run) for run in re.findall(r"`+", value)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{value}\n{fence}"


def _inline_code(value: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", value)), default=0)
    fence = "`" * max(1, longest + 1)
    padding = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{fence}{padding}{value}{padding}{fence}"


def _cookie_metadata_json(card: AccountCardData) -> str:
    return json.dumps(
        [{"domain": cookie.domain, "name": cookie.name} for cookie in card.cookies],
        ensure_ascii=False,
        indent=2,
    )


def build_account_card_blocks(
    card: AccountCardData,
    *,
    check: Block | None = None,
    edit: Block | None = None,
    delete: Block | None = None,
    progress: Block | None = None,
) -> tuple[Block, ...]:
    """Build an expanded safe card without password or session-cookie values."""

    counts = Counter(cookie.domain for cookie in card.cookies)
    primary_domain = counts.most_common(1)[0][0] if counts else "Домен не указан"
    domains = (
        "  ".join(f"{_inline_code(domain)} · {count}" for domain, count in sorted(counts.items())) or "нет"
    )
    environment = card.environment
    cookies_json = _cookie_metadata_json(card)
    cookies_block = _fenced(cookies_json, "json")
    visible = (
        f"### Выбранный аккаунт {_inline_code(card.id)}",
        primary_domain,
        _inline_code(f"{len(card.cookies)} cookies"),
        "**Логин**\n" + _fenced(card.login),
        "Пароль\n**задан**" if card.has_password else "Пароль\n**не задан**",
        f"IP-адрес\n**{environment.ip or 'не указан'}**",
        f"Страна\n**{environment.country or 'не указана'}**",
        f"Область\n**{environment.region or 'не указана'}**",
        f"Город\n**{environment.city or 'не указан'}**",
        "**User-Agent**\n" + _fenced(environment.user_agent or "не указан", "text"),
        f"Статус\n**{ACCOUNT_STATUS_LABELS[card.status]}**",
        f"Последняя проверка\n**{format_datetime(card.last_checked_at, empty='не выполнялась')}**",
        f"Ошибка\n{card.last_error or 'нет'}",
        f"**Домены cookies:** {domains}",
        f"**Cookies · {len(card.cookies)}**",
    )
    if sum(map(len, visible)) + len(cookies_block) + 256 > _TEXT_LIMIT:
        cookies_block = (
            "Метаданные cookies не помещаются в лимит Mattermost Blocks: "
            f"{len(cookies_json)} символов. Данные не были опубликованы частично."
        )

    trailing_actions = tuple(action for action in (edit, delete) if action is not None)

    content: list[Block] = [
        ColumnSet(
            gap="large",
            columns=(
                Column(width="stretch", items=(Text(text=visible[3]),)),
                Column(width="stretch", items=(Text(text=visible[4]),)),
            ),
        ),
        ColumnSet(
            gap="large",
            columns=(
                Column(width="stretch", items=(Text(text=visible[5]),)),
                Column(width="stretch", items=(Text(text=visible[6]),)),
            ),
        ),
        ColumnSet(
            gap="large",
            columns=(
                Column(width="stretch", items=(Text(text=visible[7]),)),
                Column(width="stretch", items=(Text(text=visible[8]),)),
            ),
        ),
        Text(text=visible[9]),
        ColumnSet(
            gap="large",
            columns=(
                Column(width="stretch", items=(Text(text=visible[10]),)),
                Column(width="stretch", items=(Text(text=visible[11]),)),
            ),
        ),
        Text(text=visible[12]),
        Divider(),
        Text(text=visible[13]),
        Collapsible(
            collapsed=True,
            header=(Text(text=visible[14]),),
            content=(
                Container(
                    border=False,
                    background="gray",
                    flow="vertical",
                    gap="small",
                    max_height="large",
                    content=(Text(text=cookies_block),),
                ),
            ),
        ),
    ]
    if check is not None:
        content.extend(
            (
                Divider(),
                check,
            )
        )
    if progress is not None:
        content.append(progress)

    header_columns: list[Column] = [
        Column(
            width="stretch",
            gap="small",
            items=(Text(text=visible[0]), Text(text=visible[1], is_subtle=True)),
        ),
        Column(width="auto", items=(Text(text=visible[2]),)),
    ]
    if trailing_actions:
        header_columns.append(
            Column(
                width="auto",
                items=(
                    Container(
                        border=False,
                        flow="horizontal",
                        gap="small",
                        content=trailing_actions,
                    ),
                ),
            )
        )
    return (
        Collapsible(
            collapsed=False,
            header=(
                ColumnSet(
                    gap="small",
                    columns=tuple(header_columns),
                ),
            ),
            content=tuple(content),
        ),
    )


CheckHandler = Callable[[Any], Awaitable[None]]


class AccountCard(Widget):
    """Render one selected account and its local check action in the current post."""

    def __init__(
        self,
        *,
        id: str,
        card: str,
        on_check: CheckHandler,
        check: str | None = None,
        on_cancel: CheckHandler | None = None,
        edit_state: State | None = None,
        delete_state: State | None = None,
        when: WhenCondition = None,
    ) -> None:
        super().__init__(id=id, when=when)
        if not card:
            raise ValueError("AccountCard source must be nonempty")
        self.card = card
        self.on_check = on_check
        self.check = check
        self.on_cancel = on_cancel
        for name, state in (("edit_state", edit_state), ("delete_state", delete_state)):
            if state is not None and state.state is None:
                raise ValueError(f"AccountCard {name} must have a stable name")
        self.edit_state = edit_state
        self.delete_state = delete_state

    async def render_control_groups(
        self,
        data: Mapping[str, Any],
        manager: Any,
    ) -> tuple[PostControlGroup, ...]:
        if not await self.is_visible(data, manager):
            return ()
        card = data.get(self.card)
        if card is None:
            return ()
        if not isinstance(card, AccountCardData):
            raise InvalidWidget(f"AccountCard source {self.card!r} must contain AccountCardData")
        check = data.get(self.check) if self.check is not None else None
        if check is not None and not isinstance(check, AccountCheckData):
            raise InvalidWidget(f"AccountCard source {self.check!r} must contain AccountCheckData")
        assert self.widget_id is not None
        context = manager.current_context()
        active_check = isinstance(check, AccountCheckData) and check.active
        check_control = PostControl(
            id=f"{self.widget_id}Check",
            label="Проверить",
            intent_id=context.intent_id,
            source_state=context.state.state,
            action="account_check",
            widget_id=self.widget_id,
            style="primary",
            disabled=active_check,
        )
        edit_control = (
            PostControl(
                id=f"{self.widget_id}Edit",
                label="✏️",
                target_state=self.edit_state.state,
                intent_id=context.intent_id,
                source_state=context.state.state,
                action="account_edit",
                widget_id=self.widget_id,
                tooltip="Редактировать аккаунт",
                legacy_label="Редактировать",
            )
            if self.edit_state is not None
            else None
        )
        delete_control = (
            PostControl(
                id=f"{self.widget_id}Delete",
                label="🗑️",
                target_state=self.delete_state.state,
                intent_id=context.intent_id,
                source_state=context.state.state,
                action="account_delete",
                widget_id=self.widget_id,
                style="danger",
                disabled=active_check,
                tooltip="Удалить аккаунт",
                legacy_label="Удалить аккаунт",
            )
            if self.delete_state is not None
            else None
        )
        cancel_control = (
            PostControl(
                id=f"{self.widget_id}Cancel",
                label="Отменить проверку",
                intent_id=context.intent_id,
                source_state=context.state.state,
                action="account_check_cancel",
                widget_id=self.widget_id,
            )
            if active_check and self.on_cancel is not None
            else None
        )

        def layout(bind: Callable[[str], Block]) -> tuple[Block, ...]:
            progress = (
                build_account_check_blocks(
                    check,
                    cancel=bind(cancel_control.id) if cancel_control is not None else None,
                    embedded=True,
                )[0]
                if check is not None
                else None
            )
            return build_account_card_blocks(
                card,
                check=bind(check_control.id),
                edit=bind(edit_control.id) if edit_control is not None else None,
                delete=bind(delete_control.id) if delete_control is not None else None,
                progress=progress,
            )

        controls = tuple(
            control
            for control in (check_control, edit_control, delete_control, cancel_control)
            if control is not None
        )

        fallback_text = f"Выбран аккаунт {card.id}"
        if check is not None:
            fallback_text += (
                f"\nПроверка: {CHECK_STATUS_LABELS[check.state]} · "
                f"этап {CHECK_STAGE_LABELS.get(check.stage, 'Неизвестный этап')}"
            )

        return (
            PostControlGroup(
                controls,
                text=fallback_text,
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
        if action.type == "account_check":
            await self.on_check(manager)
            return WidgetActionResult()
        if action.type == "account_edit" and self.edit_state is not None:
            if action.target_state != self.edit_state.state:
                raise InvalidWidget("AccountCard edit target is invalid")
            return WidgetActionResult(target_state=action.target_state)
        if action.type == "account_delete" and self.delete_state is not None:
            if action.target_state != self.delete_state.state:
                raise InvalidWidget("AccountCard delete target is invalid")
            return WidgetActionResult(target_state=action.target_state)
        if action.type == "account_check_cancel" and self.on_cancel is not None:
            await self.on_cancel(manager)
            return WidgetActionResult()
        raise InvalidWidget(f"AccountCard cannot process action {action.type!r}")


__all__ = ["AccountCard", "build_account_card_blocks"]
