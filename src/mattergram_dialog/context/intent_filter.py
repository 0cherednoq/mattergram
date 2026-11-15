from __future__ import annotations

from mattergram.fsm.state import State, StatesGroup
from mattergram_dialog.api.entities import Context


class IntentFilter:
    def __init__(self, *states: State | type[StatesGroup]) -> None:
        self._state_names = {
            name
            for item in states
            for name in (
                item.__all_states_names__
                if isinstance(item, type) and issubclass(item, StatesGroup)
                else (item.state,)
            )
            if name is not None
        }

    def __call__(self, context: Context | None) -> bool:
        return context is not None and context.state.state in self._state_names


__all__ = ["IntentFilter"]
