from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mattergram_dialog.widgets.common.when import WhenCondition

from .base import Text


class Format(Text):
    def __init__(self, text: str, *, when: WhenCondition = None) -> None:
        super().__init__(when=when)
        self.text = text

    async def _render_text(self, data: Mapping[str, Any], manager: Any) -> str:
        del manager
        try:
            return self.text.format_map(dict(data))
        except (KeyError, AttributeError, IndexError, ValueError) as error:
            error.add_note(f"while rendering dialog format {self.text!r}")
            raise


__all__ = ["Format"]
