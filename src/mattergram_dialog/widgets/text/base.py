from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mattergram_dialog.widgets.common.base import Widget
from mattergram_dialog.widgets.common.when import WhenCondition


class Text(Widget):
    async def render_text(self, data: Mapping[str, Any], manager: Any) -> str:
        if not await self.is_visible(data, manager):
            return ""
        return await self._render_text(data, manager)

    async def _render_text(self, data: Mapping[str, Any], manager: Any) -> str:
        raise NotImplementedError


class Const(Text):
    def __init__(self, text: str, *, when: WhenCondition = None) -> None:
        super().__init__(when=when)
        self.text = text

    async def _render_text(self, data: Mapping[str, Any], manager: Any) -> str:
        del data, manager
        return self.text


class Multi(Text):
    def __init__(self, *texts: Text, sep: str = "\n", when: WhenCondition = None) -> None:
        super().__init__(when=when)
        self.texts = texts
        self.sep = sep

    async def _render_text(self, data: Mapping[str, Any], manager: Any) -> str:
        return self.sep.join(filter(None, [await item.render_text(data, manager) for item in self.texts]))


class Or(Text):
    def __init__(self, *texts: Text, when: WhenCondition = None) -> None:
        super().__init__(when=when)
        if not texts:
            raise ValueError("Or requires at least one text")
        self.texts = texts

    async def _render_text(self, data: Mapping[str, Any], manager: Any) -> str:
        for item in self.texts:
            result = await item.render_text(data, manager)
            if result:
                return result
        return ""


__all__ = ["Const", "Multi", "Or", "Text"]
