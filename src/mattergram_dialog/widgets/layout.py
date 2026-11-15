from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mattergram_dialog.api.exceptions import InvalidWidget, InvalidWidgetIdError
from mattergram_dialog.rendering import PostControl, PostControlGroup
from mattergram_dialog.widgets.common.base import Widget
from mattergram_dialog.widgets.common.when import WhenCondition
from mattergram_dialog.widgets.text import Const, Text


def _find_text(text: Text, widget_id: str) -> Any | None:
    if text.widget_id == widget_id:
        return text
    for name in ("texts", "body", "title", "item_text", "empty", "default"):
        nested = getattr(text, name, None)
        values = (
            nested.values()
            if isinstance(nested, dict)
            else nested
            if isinstance(nested, tuple)
            else (nested,)
        )
        for item in values:
            if isinstance(item, Text):
                found = _find_text(item, widget_id)
                if found is not None:
                    return found
    return None


class Section(Text):
    """Render a Markdown heading and a deliberately separated content block."""

    def __init__(
        self,
        title: str | Text,
        *body: Text,
        level: int = 2,
        divider: bool = False,
        id: str | None = None,
        when: WhenCondition = None,
    ) -> None:
        super().__init__(id=id, when=when)
        if type(level) is not int or level < 1 or level > 4:
            raise ValueError("Section level must be between 1 and 4")
        if not body:
            raise ValueError("Section requires at least one body widget")
        self.title = Const(title) if isinstance(title, str) else title
        self.body = body
        self.level = level
        self.divider = divider

    async def _render_text(self, data: Mapping[str, Any], manager: Any) -> str:
        title = await self.title.render_text(data, manager)
        body = [await item.render_text(data, manager) for item in self.body]
        parts = ["#" * self.level + " " + title, *filter(None, body)]
        value = "\n\n".join(parts)
        return value + ("\n\n---" if self.divider else "")


class DataBlock(Widget):
    """Keep one data object and all of its local actions in one attachment."""

    def __init__(
        self,
        title: str | Text,
        content: Text,
        *widgets: Widget,
        id: str | None = None,
        color: str | None = None,
        when: WhenCondition = None,
    ) -> None:
        super().__init__(id=id, when=when)
        if any(not callable(getattr(widget, "render_controls", None)) for widget in widgets):
            raise TypeError("DataBlock children must render controls")
        self.title = Const(title) if isinstance(title, str) else title
        self.content = content
        self.widgets = widgets
        self.color = color

    async def render_control_groups(
        self, data: Mapping[str, Any], manager: Any
    ) -> tuple[PostControlGroup, ...]:
        if not await self.is_visible(data, manager):
            return ()
        content = await self.content.render_text(data, manager)
        before_rows: list[tuple[PostControl, ...]] = []
        after_rows: list[tuple[PostControl, ...]] = []
        detail: tuple[str, str, str | None] | None = None
        for widget in self.widgets:
            render_controls = getattr(widget, "render_controls")
            row = tuple(await render_controls(data, manager))
            if row:
                target = (
                    before_rows if getattr(widget, "content_position", "after") == "before" else after_rows
                )
                target.append(row)
            render_detail = getattr(widget, "render_detail", None)
            if callable(render_detail):
                rendered_detail = await render_detail(data, manager)
                if rendered_detail is not None:
                    if detail is not None:
                        raise InvalidWidget("DataBlock can render only one selected-item detail card")
                    detail = rendered_detail
        rows = before_rows + after_rows
        controls = tuple(control for row in rows for control in row)
        title = await self.title.render_text(data, manager)
        if not title or len(title) > 80:
            raise InvalidWidget("DataBlock title must contain 1-80 characters")
        return (
            PostControlGroup(
                controls,
                title=title,
                color=self.color,
                text=content,
                rows=tuple(rows),
                content_row_index=len(before_rows),
                detail_title=None if detail is None else detail[0],
                detail_text=None if detail is None else detail[1],
                detail_color=None if detail is None else detail[2],
            ),
        )

    def find(self, widget_id: str) -> Any:
        content = _find_text(self.content, widget_id)
        if content is not None:
            return content
        title = _find_text(self.title, widget_id)
        if title is not None:
            return title
        for widget in self.widgets:
            if widget.widget_id == widget_id:
                return widget
            find = getattr(widget, "find", None)
            if callable(find):
                try:
                    return find(widget_id)
                except InvalidWidgetIdError:
                    pass
        raise InvalidWidgetIdError(f"Unknown widget id: {widget_id}")


__all__ = ["DataBlock", "Section"]
