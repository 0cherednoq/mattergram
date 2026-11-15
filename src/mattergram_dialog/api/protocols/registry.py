from __future__ import annotations

from typing import Protocol

from mattergram.fsm.state import State, StatesGroup

from .dialog import DialogProtocol


class DialogRegistryProtocol(Protocol):
    def find_dialog(self, state: State | str) -> DialogProtocol: ...

    def states(self, group: type[StatesGroup] | None = None) -> tuple[State, ...]: ...


__all__ = ["DialogRegistryProtocol"]
