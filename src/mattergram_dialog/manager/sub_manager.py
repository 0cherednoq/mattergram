from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mattergram_dialog.manager.manager import DialogManager


class SubManager:
    def __init__(self, manager: DialogManager, widget_id: str, item_id: str) -> None:
        if not widget_id or not item_id:
            raise ValueError("Sub-manager widget and item IDs must be nonempty")
        self.manager = manager
        self.widget_id = widget_id
        self.item_id = item_id

    @property
    def dialog_data(self) -> dict[str, Any]:
        return self.manager.dialog_data

    async def update(self, data: Mapping[str, Any]) -> None:
        await self.manager.update(data)

    def current_context(self) -> Any:
        return self.manager.current_context()


__all__ = ["SubManager"]
