from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from mattergram_dialog.api.exceptions import InvalidWidget
from mattergram_dialog.widgets.common.when import WhenCondition
from mattergram_dialog.widgets.text import Const, Text


@dataclass(frozen=True, slots=True)
class CursorPage:
    """One result page from an opaque-cursor API."""

    items: Sequence[Any]
    next_cursor: str | None = None


CursorLoader = Callable[[str | None, int, Any], CursorPage | Awaitable[CursorPage]]


class CursorList(Text):
    """Render pages loaded by an opaque forward cursor with local Back history."""

    def __init__(
        self,
        item_text: Text,
        *,
        id: str,
        loader: CursorLoader,
        page_size: int = 20,
        empty: Text | None = None,
        sep: str = "\n",
        page_label: str | None = "Page {page}",
        when: WhenCondition = None,
    ) -> None:
        super().__init__(id=id, when=when)
        if type(page_size) is not int or page_size < 1 or page_size > 100:
            raise ValueError("CursorList page_size must be between 1 and 100")
        if not callable(loader):
            raise TypeError("CursorList loader must be callable")
        self.item_text = item_text
        self.loader = loader
        self.page_size = page_size
        self.empty = empty or Const("No items")
        self.sep = sep
        self.page_label = page_label

    def widget_state(self, manager: Any) -> dict[str, Any]:
        assert self.widget_id is not None
        raw = manager.current_context().widget_data.setdefault(self.widget_id, {})
        if not isinstance(raw, dict):
            raise InvalidWidget(f"CursorList {self.widget_id!r} state must be a mapping")
        cursor = raw.get("cursor")
        history = raw.setdefault("history", [])
        page = raw.setdefault("page", 0)
        if cursor is not None and not isinstance(cursor, str):
            raise InvalidWidget("CursorList cursor must be a string or None")
        if not isinstance(history, list) or any(
            item is not None and not isinstance(item, str) for item in history
        ):
            raise InvalidWidget("CursorList history must contain string cursors or None")
        if type(page) is not int or page < 0:
            raise InvalidWidget("CursorList page must be a nonnegative integer")
        return raw

    async def _load(self, cursor: str | None, manager: Any) -> CursorPage:
        value = self.loader(cursor, self.page_size, manager)
        if inspect.isawaitable(value):
            value = await value
        if not isinstance(value, CursorPage):
            raise TypeError("CursorList loader must return CursorPage")
        if value.next_cursor is not None and not value.next_cursor:
            raise ValueError("CursorPage next_cursor must be nonempty or None")
        return value

    async def _render_text(self, data: Mapping[str, Any], manager: Any) -> str:
        state = self.widget_state(manager)
        page = await self._load(state.get("cursor"), manager)
        state["next_cursor"] = page.next_cursor
        state["count"] = len(page.items)
        rendered: list[str] = []
        for index, item in enumerate(page.items):
            item_data = dict(data)
            item_data.update(
                item=item,
                item_index=index,
                page_item_index=index,
                page=state["page"] + 1,
            )
            value = await self.item_text.render_text(item_data, manager)
            if value:
                rendered.append(value)
        if not rendered:
            rendered.append(await self.empty.render_text(data, manager))
        if self.page_label is not None:
            rendered.append(self.page_label.format(page=state["page"] + 1))
        return self.sep.join(filter(None, rendered))

    def move(self, value: str | None, manager: Any) -> None:
        state = self.widget_state(manager)
        history = state["history"]
        if value == "next":
            next_cursor = state.get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                return
            history.append(state.get("cursor"))
            state["cursor"] = next_cursor
            state["page"] += 1
        elif value == "prev":
            if not history:
                return
            state["cursor"] = history.pop()
            state["page"] = max(0, state["page"] - 1)
        else:
            raise InvalidWidget("Unknown CursorList operation")

    def reset(self, manager: Any) -> None:
        state = self.widget_state(manager)
        state.clear()
        state.update(cursor=None, history=[], page=0)


__all__ = ["CursorList", "CursorLoader", "CursorPage"]
