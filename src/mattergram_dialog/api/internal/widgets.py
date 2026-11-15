from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Widget(Protocol):
    @property
    def widget_id(self) -> str | None: ...


class TextWidget(Widget, Protocol):
    async def render_text(self, data: Mapping[str, Any], manager: Any) -> str: ...


class FormWidget(Widget, Protocol):
    async def render_field(self, data: Mapping[str, Any], manager: Any) -> Any: ...


DataGetter = Any

__all__ = ["DataGetter", "FormWidget", "TextWidget", "Widget"]
