from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mattergram_dialog.widgets.common.case import Selector, select_value
from mattergram_dialog.widgets.common.when import WhenCondition

from .base import Text


class Case(Text):
    def __init__(
        self,
        texts: Mapping[Any, Text],
        selector: Selector,
        *,
        default: Text | None = None,
        when: WhenCondition = None,
    ) -> None:
        super().__init__(when=when)
        self.texts = dict(texts)
        self.selector = selector
        self.default = default

    async def _render_text(self, data: Mapping[str, Any], manager: Any) -> str:
        text = self.texts.get(select_value(self.selector, data, manager), self.default)
        return "" if text is None else await text.render_text(data, manager)


__all__ = ["Case"]
