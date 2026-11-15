from __future__ import annotations

import inspect
import math
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from magic_filter import MagicFilter

from mattergram_dialog.api.exceptions import InvalidWidget
from mattergram_dialog.widgets.common.when import WhenCondition
from mattergram_dialog.widgets.text import Const, Text

Number = int | float
ProgressValue = (
    str | MagicFilter | Number | Callable[[Mapping[str, Any], Any], Number | None | Awaitable[Number | None]]
)


class Progress(Text):
    """Render an accessible determinate or indeterminate Markdown progress line."""

    def __init__(
        self,
        *,
        current: ProgressValue,
        total: ProgressValue | None = None,
        label: str | Text | None = None,
        width: int = 12,
        filled: str = "█",
        empty: str = "░",
        show_percent: bool = True,
        show_count: bool = True,
        indeterminate_prefix: str = "⏳",
        id: str | None = None,
        when: WhenCondition = None,
    ) -> None:
        super().__init__(id=id, when=when)
        if type(width) is not int or width < 4 or width > 40:
            raise ValueError("Progress width must be between 4 and 40")
        if len(filled) != 1 or len(empty) != 1:
            raise ValueError("Progress filled and empty markers must contain one character")
        if not indeterminate_prefix:
            raise ValueError("Progress indeterminate_prefix must be nonempty")
        self.current = current
        self.total = total
        self.label = Const(label) if isinstance(label, str) else label
        self.width = width
        self.filled = filled
        self.empty = empty
        self.show_percent = show_percent
        self.show_count = show_count
        self.indeterminate_prefix = indeterminate_prefix

    @staticmethod
    async def _resolve(source: ProgressValue, data: Mapping[str, Any], manager: Any) -> Number | None:
        if isinstance(source, str):
            value = data.get(source)
        elif isinstance(source, MagicFilter):
            value = source.resolve(data)
        elif callable(source):
            value = source(data, manager)
            if inspect.isawaitable(value):
                value = await value
        else:
            value = source
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise InvalidWidget("Progress values must be finite numbers or None")
        return value

    @staticmethod
    def _number(value: Number) -> str:
        return str(value) if isinstance(value, int) else f"{value:g}"

    async def _render_text(self, data: Mapping[str, Any], manager: Any) -> str:
        current = await self._resolve(self.current, data, manager)
        if current is None or current < 0:
            raise InvalidWidget("Progress current value must be a nonnegative number")
        total = None if self.total is None else await self._resolve(self.total, data, manager)
        label = "" if self.label is None else await self.label.render_text(data, manager)
        if total is None:
            parts = [self.indeterminate_prefix]
            if label:
                parts.append(label)
            if self.show_count:
                parts.append(self._number(current))
            return " ".join(parts)
        if total <= 0 or current > total:
            raise InvalidWidget("Progress total must be positive and no smaller than current")
        ratio = current / total
        completed = min(self.width, int(ratio * self.width + 0.5))
        bar = self.filled * completed + self.empty * (self.width - completed)
        suffix: list[str] = []
        if self.show_percent:
            suffix.append(f"{ratio:.0%}")
        if self.show_count:
            suffix.append(f"{self._number(current)}/{self._number(total)}")
        rendered = " ".join((bar, " · ".join(suffix))) if suffix else bar
        return rendered if not label else label + "\n" + rendered


__all__ = ["Progress", "ProgressValue"]
