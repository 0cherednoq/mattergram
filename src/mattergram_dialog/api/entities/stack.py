from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from mattergram.fsm.state import State
from mattergram_dialog.api.exceptions import DialogStackOverflow

from .access import AccessSettings
from .context import Context, Data

DEFAULT_STACK_ID = "default"
STACK_LIMIT = 100


def new_id() -> str:
    return secrets.token_urlsafe(12)


@dataclass(slots=True)
class Stack:
    id: str = DEFAULT_STACK_ID
    intents: list[str] = field(default_factory=list)
    last_post_id: str | None = None
    access_settings: AccessSettings | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Dialog stack ID must be nonempty")

    def push(self, state: State, data: Data = None) -> Context:
        if len(self.intents) >= STACK_LIMIT:
            raise DialogStackOverflow(f"Cannot open more than {STACK_LIMIT} dialog contexts")
        context = Context(intent_id=new_id(), stack_id=self.id, state=state, start_data=data)
        self.intents.append(context.id)
        return context

    def pop(self) -> str:
        if not self.intents:
            raise IndexError("Dialog stack is empty")
        return self.intents.pop()

    def last_intent_id(self) -> str:
        if not self.intents:
            raise IndexError("Dialog stack is empty")
        return self.intents[-1]

    def empty(self) -> bool:
        return not self.intents
