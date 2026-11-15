from __future__ import annotations

from mattergram.fsm.state import State, StatesGroup
from mattergram_dialog.api.exceptions import UnregisteredDialogError
from mattergram_dialog.dialog import Dialog


class DialogRegistry:
    def __init__(self, *dialogs: Dialog) -> None:
        self._dialogs: dict[str, Dialog] = {}
        self._states: dict[str, State] = {}
        for dialog in dialogs:
            self.register(dialog)

    def register(self, dialog: Dialog) -> None:
        for state in dialog.states:
            name = state.state
            if name is None:
                raise ValueError("Dialog states must have stable names")
            if name in self._dialogs:
                raise ValueError(f"State {name!r} is registered twice")
            self._dialogs[name] = dialog
            self._states[name] = state

    def find_dialog(self, state: State | str) -> Dialog:
        name = state.state if isinstance(state, State) else state
        try:
            return self._dialogs[name or ""]
        except KeyError:
            raise UnregisteredDialogError(f"No dialog registered for state {name!r}") from None

    def resolve_state(self, state: State | str) -> State:
        name = state.state if isinstance(state, State) else state
        try:
            return self._states[name or ""]
        except KeyError:
            raise UnregisteredDialogError(f"No dialog registered for state {name!r}") from None

    def states(self, group: type[StatesGroup] | None = None) -> tuple[State, ...]:
        values = tuple(self._states.values())
        return values if group is None else tuple(state for state in values if state in group)


__all__ = ["DialogRegistry"]
