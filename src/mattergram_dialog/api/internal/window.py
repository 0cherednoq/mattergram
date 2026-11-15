from __future__ import annotations

from typing import Any, Protocol

from mattergram.fsm.state import State


class WindowProtocol(Protocol):
    state: State

    async def render(self, manager: Any) -> Any: ...

    def find(self, widget_id: str) -> Any: ...


__all__ = ["WindowProtocol"]
