from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from mattergram_dialog.api.exceptions import InvalidWidget
from mattergram_dialog.widgets.common.items import ItemsGetter, get_items
from mattergram_dialog.widgets.common.when import WhenCondition
from mattergram_dialog.widgets.text import Const, Text

FilterGetter = str | Callable[[Any], Any]


class PagedList(Text):
    """Render a filtered page and keep its cursor in the dialog context."""

    def __init__(
        self,
        item_text: Text,
        *,
        id: str,
        items: ItemsGetter,
        page_size: int = 10,
        filter_by: FilterGetter | None = None,
        empty: Text | None = None,
        sep: str = "\n",
        page_label: str | None = "Page {page}/{pages}",
        when: WhenCondition = None,
    ) -> None:
        super().__init__(id=id, when=when)
        if type(page_size) is not int or page_size < 1 or page_size > 100:
            raise ValueError("PagedList page_size must be between 1 and 100")
        if not isinstance(sep, str):
            raise TypeError("PagedList sep must be a string")
        if page_label is not None and not isinstance(page_label, str):
            raise TypeError("PagedList page_label must be a string or None")
        self.item_text = item_text
        self.items = items
        self.page_size = page_size
        self.filter_by = filter_by
        self.empty = empty or Const("No items")
        self.sep = sep
        self.page_label = page_label

    def widget_state(self, manager: Any) -> dict[str, Any]:
        assert self.widget_id is not None
        raw = manager.current_context().widget_data.setdefault(self.widget_id, {})
        if not isinstance(raw, dict):
            raise InvalidWidget(f"PagedList {self.widget_id!r} state must be a mapping")
        page = raw.get("page", 0)
        selected = raw.get("filter")
        if type(page) is not int or page < 0:
            raise InvalidWidget(f"PagedList {self.widget_id!r} page must be a nonnegative integer")
        if selected is not None and not isinstance(selected, str):
            raise InvalidWidget(f"PagedList {self.widget_id!r} filter must be a string or None")
        return raw

    def _item_filter(self, item: Any) -> str | None:
        selector = self.filter_by
        if selector is None:
            return None
        if isinstance(selector, str):
            value = item.get(selector) if isinstance(item, Mapping) else getattr(item, selector)
        else:
            value = selector(item)
        return None if value is None else str(value)

    def page_items(self, data: Mapping[str, Any], manager: Any) -> tuple[list[Any], int, int, int]:
        state = self.widget_state(manager)
        selected = state.get("filter")
        values = list(get_items(self.items, data))
        if selected is not None:
            if self.filter_by is None:
                raise InvalidWidget(f"PagedList {self.widget_id!r} has no filter_by selector")
            values = [item for item in values if self._item_filter(item) == selected]
        pages = max(1, (len(values) + self.page_size - 1) // self.page_size)
        page = min(state.get("page", 0), pages - 1)
        state.update(page=page, pages=pages, total=len(values))
        start = page * self.page_size
        return values[start : start + self.page_size], page, pages, len(values)

    async def _render_text(self, data: Mapping[str, Any], manager: Any) -> str:
        values, page, pages, total = self.page_items(data, manager)
        start = page * self.page_size
        rendered: list[str] = []
        for page_index, item in enumerate(values):
            item_data = dict(data)
            item_data.update(
                item=item,
                item_index=start + page_index,
                page_item_index=page_index,
                page=page + 1,
                pages=pages,
                total=total,
            )
            value = await self.item_text.render_text(item_data, manager)
            if value:
                rendered.append(value)
        if not rendered:
            rendered.append(await self.empty.render_text(data, manager))
        if self.page_label is not None:
            rendered.append(self.page_label.format(page=page + 1, pages=pages, total=total))
        return self.sep.join(filter(None, rendered))

    def move(self, value: str | None, manager: Any) -> None:
        state = self.widget_state(manager)
        pages = state.get("pages", 1)
        if type(pages) is not int or pages < 1:
            raise InvalidWidget(f"PagedList {self.widget_id!r} pages must be a positive integer")
        page = state.get("page", 0)
        if value == "prev":
            page -= 1
        elif value == "next":
            page += 1
        elif value == "first":
            page = 0
        elif value == "last":
            page = pages - 1
        elif value == "current":
            pass
        else:
            raise InvalidWidget("Unknown PagedList page operation")
        state["page"] = min(max(page, 0), pages - 1)

    def select_filter(self, value: str | None, manager: Any) -> None:
        if value is not None and self.filter_by is None:
            raise InvalidWidget(f"PagedList {self.widget_id!r} has no filter_by selector")
        state = self.widget_state(manager)
        state["filter"] = value
        state["page"] = 0


__all__ = ["FilterGetter", "PagedList"]
