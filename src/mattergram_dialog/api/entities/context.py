from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mattergram.fsm.state import State

from .access import AccessSettings

Data = dict[str, Any] | list[Any] | str | int | float | bool | None
DataDict = dict[str, Any]


@dataclass(slots=True)
class Context:
    intent_id: str
    stack_id: str
    state: State
    start_data: Data = None
    dialog_data: DataDict = field(default_factory=dict)
    widget_data: DataDict = field(default_factory=dict)
    access_settings: AccessSettings | None = None

    def __post_init__(self) -> None:
        if not self.intent_id or not self.stack_id:
            raise ValueError("Dialog intent_id and stack_id must be nonempty strings")

    @property
    def id(self) -> str:
        return self.intent_id
