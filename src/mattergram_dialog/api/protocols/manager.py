from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from mattergram.fsm.state import State
from mattergram_dialog.api.entities import Context, Data, ShowMode, StartMode


class DialogManager(Protocol):
    @property
    def dialog_data(self) -> dict[str, Any]: ...

    @property
    def start_data(self) -> Data: ...

    def current_context(self) -> Context: ...

    async def start(self, state: State, data: Data = None, *, mode: StartMode = StartMode.NORMAL) -> None: ...

    async def switch_to(self, state: State, *, show_mode: ShowMode = ShowMode.AUTO) -> None: ...

    async def next(self, *, show_mode: ShowMode = ShowMode.AUTO) -> None: ...

    async def back(self, *, show_mode: ShowMode = ShowMode.AUTO) -> None: ...

    async def done(self, result: Any = None, *, show_mode: ShowMode = ShowMode.AUTO) -> None: ...

    async def update(self, data: Mapping[str, Any], *, show_mode: ShowMode = ShowMode.AUTO) -> None: ...


__all__ = ["DialogManager"]
