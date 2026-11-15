from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from mattergram.fsm.state import State
from mattergram_dialog.api.entities import LaunchMode
from mattergram_dialog.api.exceptions import UnknownState
from mattergram_dialog.rendering import RenderPlan
from mattergram_dialog.window import Window

Lifecycle = Callable[..., Awaitable[None]]


class Dialog:
    def __init__(
        self,
        *windows: Window,
        launch_mode: LaunchMode = LaunchMode.STANDARD,
        on_start: Lifecycle | None = None,
        on_process_result: Lifecycle | None = None,
        on_close: Lifecycle | None = None,
    ) -> None:
        if not windows:
            raise ValueError("Dialog requires at least one window")
        names = [window.state.state for window in windows]
        if None in names or len(set(names)) != len(names):
            raise ValueError("Dialog window states must be stable and unique")
        self.windows = tuple(windows)
        self.launch_mode = launch_mode
        self.on_start = on_start
        self.on_process_result = on_process_result
        self.on_close = on_close

    @property
    def states(self) -> Sequence[State]:
        return tuple(window.state for window in self.windows)

    def window_for(self, state: State | str) -> Window:
        name = state.state if isinstance(state, State) else state
        for window in self.windows:
            if window.state.state == name:
                return window
        raise UnknownState(f"State {name!r} does not belong to this dialog")

    def index(self, state: State | str) -> int:
        return self.windows.index(self.window_for(state))

    async def render(self, manager: Any) -> RenderPlan:
        return await self.window_for(manager.current_context().state).render(manager)


__all__ = ["Dialog", "Lifecycle"]
