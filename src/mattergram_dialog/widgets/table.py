from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from mattergram.fsm.state import State
from mattergram_dialog.api.exceptions import InvalidWidget
from mattergram_dialog.rendering import PostControl, PostControlGroup, PostTableRow
from mattergram_dialog.widgets.common.action import WidgetAction, WidgetActionResult
from mattergram_dialog.widgets.common.base import Widget
from mattergram_dialog.widgets.common.items import ItemsGetter, get_items
from mattergram_dialog.widgets.common.when import WhenCondition
from mattergram_dialog.widgets.paged import FilterGetter, PagedList
from mattergram_dialog.widgets.text import Const, Text

CellGetter = str | Callable[[Any], Any]
Alignment = Literal["left", "center", "right"]
_ACTION_ID = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,47}$")


@dataclass(frozen=True, slots=True)
class MarkdownColumn:
    title: str
    value: CellGetter
    max_chars: int = 24
    align: Alignment = "left"

    def __post_init__(self) -> None:
        if not self.title or len(self.title) > 32:
            raise ValueError("MarkdownColumn title must contain 1-32 characters")
        if type(self.max_chars) is not int or self.max_chars < 4 or self.max_chars > 80:
            raise ValueError("MarkdownColumn max_chars must be between 4 and 80")
        if self.align not in ("left", "center", "right"):
            raise ValueError("MarkdownColumn align is invalid")

    def read(self, item: Any) -> Any:
        if callable(self.value):
            return self.value(item)
        if isinstance(item, Mapping):
            return item.get(self.value)
        return getattr(item, self.value)


def _cell(value: Any, limit: int) -> str:
    result = str("" if value is None else value).replace("|", "\\|").replace("\n", " ")
    return result if len(result) <= limit else result[: limit - 1].rstrip() + "…"


class PagedTable(PagedList):
    """Render a bounded Markdown table backed by independent PagedList state."""

    def __init__(
        self,
        *columns: MarkdownColumn,
        id: str,
        items: ItemsGetter,
        page_size: int = 10,
        filter_by: FilterGetter | None = None,
        empty: Text | None = None,
        page_label: str | None = None,
        when: WhenCondition = None,
    ) -> None:
        if len(columns) < 2 or len(columns) > 6:
            raise ValueError("PagedTable requires between 2 and 6 columns")
        if sum(column.max_chars for column in columns) > 160:
            raise ValueError("PagedTable declared cell width exceeds 160 characters")
        if page_size > 25:
            raise ValueError("PagedTable page_size cannot exceed 25 rows")
        super().__init__(
            Const(""),
            id=id,
            items=items,
            page_size=page_size,
            filter_by=filter_by,
            empty=empty,
            page_label=page_label,
            when=when,
        )
        self.columns = columns

    async def _render_text(self, data: Mapping[str, Any], manager: Any) -> str:
        values, page, pages, total = self.page_items(data, manager)
        if not values:
            rendered = await self.empty.render_text(data, manager)
        else:
            header = "| " + " | ".join(column.title for column in self.columns) + " |"
            markers = {
                "left": ":---",
                "center": ":---:",
                "right": "---:",
            }
            divider = "| " + " | ".join(markers[column.align] for column in self.columns) + " |"
            rows = [
                "| "
                + " | ".join(_cell(column.read(item), column.max_chars) for column in self.columns)
                + " |"
                for item in values
            ]
            rendered = "\n".join((header, divider, *rows))
        if self.page_label is not None:
            rendered += "\n\n" + self.page_label.format(
                page=page + 1,
                pages=pages,
                total=total,
            )
        return rendered


@dataclass(frozen=True, slots=True)
class ActionColumn:
    """One bounded data column in a native Blocks action table."""

    title: str
    value: CellGetter
    max_chars: int = 24

    def __post_init__(self) -> None:
        if not self.title or len(self.title) > 32:
            raise ValueError("ActionColumn title must contain 1-32 characters")
        if type(self.max_chars) is not int or self.max_chars < 4 or self.max_chars > 80:
            raise ValueError("ActionColumn max_chars must be between 4 and 80")

    def read(self, item: Any) -> str:
        if callable(self.value):
            value = self.value(item)
        elif isinstance(item, Mapping):
            value = item.get(self.value)
        else:
            value = getattr(item, self.value)
        rendered = str("" if value is None else value).replace("\n", " ")
        return rendered if len(rendered) <= self.max_chars else rendered[: self.max_chars - 1].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class RowAction:
    """One signed local action repeated for every visible table record."""

    id: str
    label: str
    state: State
    style: str | None = None

    def __post_init__(self) -> None:
        if not _ACTION_ID.fullmatch(self.id):
            raise ValueError("RowAction id must be 1-48 ASCII letters/digits and start with a letter")
        if not self.label or len(self.label) > 24:
            raise ValueError("RowAction label must contain 1-24 characters")
        if self.state.state is None:
            raise ValueError("RowAction target state must have a stable name")
        if self.style not in (None, "primary", "danger"):
            raise ValueError("RowAction style must be primary, danger or None")


class ActionTable(Widget):
    """Render a bounded Blocks table with signed actions local to each record."""

    def __init__(
        self,
        *columns: ActionColumn,
        id: str,
        title: str,
        items: ItemsGetter,
        item_id: CellGetter,
        selection_key: str,
        actions: tuple[RowAction, ...],
        color: str = "#3d85c6",
        when: WhenCondition = None,
    ) -> None:
        super().__init__(id=id, when=when)
        if not _ACTION_ID.fullmatch(id):
            raise ValueError("ActionTable id must be 1-48 ASCII letters/digits and start with a letter")
        if len(columns) < 2 or len(columns) > 4:
            raise ValueError("ActionTable requires two to four data columns")
        if sum(column.max_chars for column in columns) > 160:
            raise ValueError("ActionTable declared cell width exceeds 160 characters")
        if not title or len(title) > 80:
            raise ValueError("ActionTable title must contain 1-80 characters")
        if not selection_key:
            raise ValueError("ActionTable selection_key must be nonempty")
        if not actions or len(actions) > 3:
            raise ValueError("ActionTable requires one to three row actions")
        if len({action.id for action in actions}) != len(actions):
            raise ValueError("ActionTable row action IDs must be unique")
        if any(len(id) + len(action.id) + 2 > 48 for action in actions):
            raise ValueError("ActionTable and RowAction IDs are too long for a stable row action ID")
        self.columns = columns
        self.title = title
        self.items = items
        self.item_id = item_id
        self.selection_key = selection_key
        self.actions = actions
        self.color = color

    def _state(self, manager: Any) -> dict[str, Any]:
        assert self.widget_id is not None
        raw = manager.current_context().widget_data.setdefault(self.widget_id, {})
        if not isinstance(raw, dict):
            raise InvalidWidget(f"ActionTable {self.widget_id!r} state must be a mapping")
        return raw

    def _item_id(self, item: Any) -> str:
        if callable(self.item_id):
            value = self.item_id(item)
        elif isinstance(item, Mapping):
            value = item.get(self.item_id)
        else:
            value = getattr(item, self.item_id)
        return str("" if value is None else value)

    async def render_control_groups(
        self, data: Mapping[str, Any], manager: Any
    ) -> tuple[PostControlGroup, ...]:
        if not await self.is_visible(data, manager):
            return ()
        values = list(get_items(self.items, data))
        if not values:
            return ()
        total_columns = len(self.columns) + len(self.actions)
        estimated_blocks = 1 + (len(values) + 1) * (1 + 2 * total_columns)
        if estimated_blocks > 80:
            raise InvalidWidget("ActionTable exceeds its 80-block layout budget")
        assert self.widget_id is not None
        context = manager.current_context()
        visible_ids: list[str] = []
        rows: list[PostTableRow] = []
        for index, item in enumerate(values):
            record_id = self._item_id(item)
            if not record_id or record_id in visible_ids:
                raise InvalidWidget("ActionTable item IDs must be nonempty and unique")
            visible_ids.append(record_id)
            controls = tuple(
                PostControl(
                    id=f"{self.widget_id}{action.id}{index}",
                    label=action.label,
                    target_state=action.state.state,
                    intent_id=context.intent_id,
                    source_state=context.state.state,
                    action="item",
                    widget_id=self.widget_id,
                    value=record_id,
                    style=action.style,
                )
                for action in self.actions
            )
            rows.append(
                PostTableRow(
                    cells=tuple(column.read(item) for column in self.columns),
                    controls=controls,
                )
            )
        self._state(manager)["visible_ids"] = tuple(visible_ids)
        rendered_rows = tuple(rows)
        controls = tuple(control for row in rendered_rows for control in row.controls)
        return (
            PostControlGroup(
                controls,
                title=self.title,
                color=self.color,
                table_headers=tuple(column.title for column in self.columns)
                + tuple(action.label for action in self.actions),
                table_data_columns=len(self.columns),
                table_rows=rendered_rows,
            ),
        )

    def apply_item(self, value: str | None, manager: Any) -> None:
        if value is None or value not in self._state(manager).get("visible_ids", ()):
            raise InvalidWidget("ActionTable item is not present in the current view")
        manager.dialog_data[self.selection_key] = value

    async def process_action(self, action: WidgetAction, event: Any, manager: Any) -> WidgetActionResult:
        del event
        if action.type != "item" or action.target_state is None:
            raise InvalidWidget("ActionTable requires an item action with a target state")
        self.apply_item(action.value, manager)
        return WidgetActionResult(target_state=action.target_state)


__all__ = [
    "ActionColumn",
    "ActionTable",
    "Alignment",
    "CellGetter",
    "MarkdownColumn",
    "PagedTable",
    "RowAction",
]
