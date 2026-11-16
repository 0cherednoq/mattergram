from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any, cast

from mattergram.ui.blocks import Block, Column, ColumnSet, Container, Text
from mattergram.ui.forms import Option
from mattergram_dialog.api.exceptions import InvalidWidget
from mattergram_dialog.rendering import PostControl, PostControlGroup
from mattergram_dialog.widgets.common.action import WidgetAction, WidgetActionResult
from mattergram_dialog.widgets.common.base import Widget
from mattergram_dialog.widgets.common.when import WhenCondition

from ..models import AccountListFilter, AccountListPage, AccountListQuery, AccountListSort

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,31}$")
_FILTERS: tuple[tuple[str, AccountListFilter], ...] = (
    ("Все", "all"),
    ("Креды", "credentials"),
    ("Cookies", "cookies"),
)
_SORTS: tuple[tuple[str, AccountListSort], ...] = (
    ("Сначала новые", "newest"),
    ("Сначала старые", "oldest"),
    ("По количеству cookies ↓", "cookies_desc"),
    ("По количеству cookies ↑", "cookies_asc"),
)


def _short(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1].rstrip() + "…"


def _code(value: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", value)), default=0)
    fence = "`" * max(1, longest + 1)
    padding = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{fence}{padding}{value}{padding}{fence}"


def _table_cell(value: str, limit: int, *, code: bool = False) -> str:
    shortened = _short(value, limit).replace("|", "\\|")
    return _code(shortened) if code else shortened


def _table(page: AccountListPage) -> str:
    lines = [
        "| ID | Логин | Креды | Cookies | Страна |",
        "| :--- | :--- | :---: | ---: | :--- |",
    ]
    lines.extend(
        "| "
        + " | ".join(
            (
                _table_cell(item.id, 18, code=True),
                _table_cell(item.login, 30, code=True),
                "есть" if item.has_credentials else "нет",
                str(item.cookie_count),
                _table_cell(item.country, 20),
            )
        )
        + " |"
        for item in page.items
    )
    return "\n".join(lines)


class AccountList(Widget):
    """Backend-paged account table with per-dialog filter and sort state."""

    def __init__(
        self,
        *,
        id: str,
        page: str,
        when: WhenCondition = None,
    ) -> None:
        super().__init__(id=id, when=when)
        if not _ID.fullmatch(id):
            raise ValueError("AccountList id must be 1-32 ASCII letters/digits")
        if not page:
            raise ValueError("AccountList page source must be nonempty")
        self.page = page

    def _state(self, manager: Any) -> dict[str, Any]:
        assert self.widget_id is not None
        raw = manager.current_context().widget_data.setdefault(self.widget_id, {})
        if not isinstance(raw, dict):
            raise InvalidWidget("AccountList state must be a mapping")
        return raw

    def query(self, manager: Any) -> AccountListQuery:
        state = self._state(manager)
        page = state.get("page", 0)
        selected_filter = state.get("filter", "all")
        selected_sort = state.get("sort", "newest")
        if type(page) is not int or page < 0:
            raise InvalidWidget("AccountList page is invalid")
        if selected_filter not in {value for _, value in _FILTERS}:
            raise InvalidWidget("AccountList filter is invalid")
        if selected_sort not in {value for _, value in _SORTS}:
            raise InvalidWidget("AccountList sort is invalid")
        return AccountListQuery(
            page=page,
            page_size=5,
            filter=cast(AccountListFilter, selected_filter),
            sort=cast(AccountListSort, selected_sort),
        )

    def _layout(
        self,
        page: AccountListPage,
        filter_ids: tuple[str, ...],
        sort_id: str,
        pager_ids: tuple[str, ...],
        select_id: str | None,
    ) -> Callable[[Callable[[str], Block]], tuple[Block, ...]]:
        def build(bind: Callable[[str], Block]) -> tuple[Block, ...]:
            filters = tuple(bind(identifier) for identifier in filter_ids)
            table = Text(
                text=_table(page) if page.items else "Записи не найдены",
                is_subtle=not page.items,
            )

            footer_columns = [Column(width="auto", items=(bind(identifier),)) for identifier in pager_ids]
            if select_id is not None:
                footer_columns.append(Column(width="stretch", items=(bind(select_id),)))
            return (
                Container(
                    border=False,
                    flow="vertical",
                    gap="medium",
                    content=(
                        ColumnSet(
                            gap="large",
                            columns=(
                                Column(
                                    width="stretch",
                                    gap="small",
                                    items=(
                                        Text(text="**Фильтрация**"),
                                        Container(
                                            border=False,
                                            background="none",
                                            flow="horizontal",
                                            gap="small",
                                            content=filters,
                                        ),
                                    ),
                                ),
                                Column(
                                    width="stretch",
                                    gap="small",
                                    items=(Text(text="**Сортировка**"), bind(sort_id)),
                                ),
                            ),
                        ),
                        table,
                        Text(text=f"Найдено записей: **{page.total}**"),
                        ColumnSet(gap="small", columns=tuple(footer_columns)),
                    ),
                ),
            )

        return build

    async def render_control_groups(
        self,
        data: Mapping[str, Any],
        manager: Any,
    ) -> tuple[PostControlGroup, ...]:
        if not await self.is_visible(data, manager):
            return ()
        page = data.get(self.page)
        if not isinstance(page, AccountListPage):
            raise InvalidWidget(f"AccountList source {self.page!r} must contain AccountListPage")
        state = self._state(manager)
        state.update(
            page=page.page,
            pages=page.pages,
            filter=page.query.filter,
            sort=page.query.sort,
            visible_ids=[item.id for item in page.items],
        )
        assert self.widget_id is not None
        context = manager.current_context()
        filter_controls = tuple(
            PostControl(
                id=f"{self.widget_id}Filter{index}",
                label=label,
                intent_id=context.intent_id,
                source_state=context.state.state,
                action="account_filter",
                widget_id=self.widget_id,
                value=value,
                style="primary" if value == page.query.filter else None,
            )
            for index, (label, value) in enumerate(_FILTERS)
        )
        sort_control = PostControl(
            id=f"{self.widget_id}Sort",
            label="Сортировка",
            intent_id=context.intent_id,
            source_state=context.state.state,
            action="account_sort",
            widget_id=self.widget_id,
            options=tuple(Option(text=label, value=value) for label, value in _SORTS),
            initial_option=page.query.sort,
        )

        def pager(suffix: str, label: str, value: str, disabled: bool) -> PostControl:
            return PostControl(
                id=f"{self.widget_id}{suffix}",
                label=label,
                intent_id=context.intent_id,
                source_state=context.state.state,
                action="account_page",
                widget_id=self.widget_id,
                value=value,
                disabled=disabled,
            )

        pager_controls = (
            pager("First", "«", "first", page.page == 0),
            pager("Prev", "‹", "prev", page.page == 0),
            pager("Position", f"{page.page + 1} / {page.pages}", "current", True),
            pager("Next", "›", "next", page.page + 1 >= page.pages),
            pager("Last", "»", "last", page.page + 1 >= page.pages),
        )
        select_control = (
            PostControl(
                id=f"{self.widget_id}Select",
                label="Выбрать аккаунт",
                intent_id=context.intent_id,
                source_state=context.state.state,
                action="account_select",
                widget_id=self.widget_id,
                options=tuple(Option(text=_short(item.login, 60), value=item.id) for item in page.items),
                initial_option=(
                    selected
                    if isinstance((selected := manager.dialog_data.get("selected_account_id")), str)
                    and selected in {item.id for item in page.items}
                    else None
                ),
            )
            if page.items
            else None
        )
        controls = (
            *filter_controls,
            sort_control,
            *pager_controls,
            *((select_control,) if select_control is not None else ()),
        )
        fallback = (
            f"Аккаунты: страница {page.page + 1}/{page.pages}, найдено {page.total}. "
            "Для фильтрации и выбора требуется Mattermost Blocks."
        )
        return (
            PostControlGroup(
                controls,
                border=False,
                text=fallback,
                native_control_blocks=self._layout(
                    page,
                    tuple(control.id for control in filter_controls),
                    sort_control.id,
                    tuple(control.id for control in pager_controls),
                    select_control.id if select_control is not None else None,
                ),
            ),
        )

    async def process_action(
        self,
        action: WidgetAction,
        event: Any,
        manager: Any,
    ) -> WidgetActionResult:
        state = self._state(manager)
        if action.type == "account_filter":
            if action.value not in {value for _, value in _FILTERS}:
                raise InvalidWidget("Unknown AccountList filter")
            state.update(filter=action.value, page=0)
            manager.dialog_data.pop("selected_account_id", None)
            return WidgetActionResult()
        if action.type == "account_sort":
            selected = getattr(event, "selected_option", None)
            if selected not in {value for _, value in _SORTS}:
                raise InvalidWidget("Unknown AccountList sort")
            state.update(sort=selected, page=0)
            manager.dialog_data.pop("selected_account_id", None)
            return WidgetActionResult()
        if action.type == "account_page":
            page = state.get("page", 0)
            pages = state.get("pages", 1)
            if type(page) is not int or type(pages) is not int or pages < 1:
                raise InvalidWidget("AccountList pagination state is invalid")
            if action.value == "first":
                page = 0
            elif action.value == "prev":
                page -= 1
            elif action.value == "current":
                pass
            elif action.value == "next":
                page += 1
            elif action.value == "last":
                page = pages - 1
            else:
                raise InvalidWidget("Unknown AccountList page operation")
            state["page"] = min(max(page, 0), pages - 1)
            if action.value != "current":
                manager.dialog_data.pop("selected_account_id", None)
            return WidgetActionResult()
        if action.type == "account_select":
            selected = getattr(event, "selected_option", None)
            visible_ids = state.get("visible_ids", [])
            if not isinstance(selected, str) or selected not in visible_ids:
                raise InvalidWidget("Selected account is not on the rendered page")
            manager.dialog_data["selected_account_id"] = selected
            return WidgetActionResult()
        raise InvalidWidget(f"AccountList cannot process action {action.type!r}")


__all__ = ["AccountList"]
