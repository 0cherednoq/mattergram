from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from mattergram.fsm.state import State
from mattergram_dialog.api.internal.window import WindowProtocol


class DialogProtocol(Protocol):
    @property
    def states(self) -> Sequence[State]: ...

    def window_for(self, state: State | str) -> WindowProtocol: ...

    async def render(self, manager: Any) -> Any: ...


__all__ = ["DialogProtocol"]
