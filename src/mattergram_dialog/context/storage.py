from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any

from mattergram.fsm.state import State
from mattergram.fsm.storage.base import BaseEventIsolation, BaseStorage, StorageKey
from mattergram_dialog.api.entities import AccessSettings, Context, Stack

_PAYLOAD_KEY = "mattergram_dialog"


def _state(raw: str) -> State:
    group, separator, name = raw.rpartition(":")
    return State(name if separator else raw, group_name=group if separator else None)


def _access(value: object) -> AccessSettings | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Invalid persisted dialog access settings")
    actor_ids = value.get("actor_ids", ())
    if not isinstance(actor_ids, (list, tuple)):
        raise ValueError("Invalid persisted dialog actor IDs")
    return AccessSettings(tuple(str(item) for item in actor_ids), value.get("custom"))


@dataclass(slots=True)
class DialogSession:
    stack: Stack
    contexts: dict[str, Context]

    def current(self) -> Context | None:
        return self.contexts.get(self.stack.intents[-1]) if self.stack.intents else None


class DialogStorage:
    def __init__(self, storage: BaseStorage, isolation: BaseEventIsolation) -> None:
        self.storage = storage
        self.isolation = isolation

    @staticmethod
    def key(
        *, instance_id: str, bot_id: str, channel_id: str, actor_id: str, thread_id: str | None = None
    ) -> StorageKey:
        return StorageKey(
            instance_id=instance_id,
            bot_id=bot_id,
            channel_id=channel_id,
            user_id=actor_id,
            thread_id=thread_id,
            destiny="mattergram_dialog",
        )

    async def load(self, key: StorageKey) -> DialogSession:
        outer = await self.storage.get_data(key)
        raw = outer.get(_PAYLOAD_KEY)
        if raw is None:
            return DialogSession(Stack(), {})
        if not isinstance(raw, dict):
            raise ValueError("Invalid persisted dialog session")
        raw_stack = raw.get("stack", {})
        raw_contexts = raw.get("contexts", {})
        if not isinstance(raw_stack, dict) or not isinstance(raw_contexts, dict):
            raise ValueError("Invalid persisted dialog session shape")
        stack = Stack(
            id=str(raw_stack.get("id", "default")),
            intents=[str(item) for item in raw_stack.get("intents", [])],
            last_post_id=raw_stack.get("last_post_id"),
            access_settings=_access(raw_stack.get("access_settings")),
        )
        contexts: dict[str, Context] = {}
        for intent_id, value in raw_contexts.items():
            if (
                not isinstance(intent_id, str)
                or not isinstance(value, dict)
                or not isinstance(value.get("state"), str)
            ):
                raise ValueError("Invalid persisted dialog context")
            contexts[intent_id] = Context(
                intent_id=intent_id,
                stack_id=str(value.get("stack_id", stack.id)),
                state=_state(value["state"]),
                start_data=deepcopy(value.get("start_data")),
                dialog_data=deepcopy(value.get("dialog_data", {})),
                widget_data=deepcopy(value.get("widget_data", {})),
                access_settings=_access(value.get("access_settings")),
            )
        if any(intent_id not in contexts for intent_id in stack.intents):
            raise ValueError("Persisted dialog stack references a missing context")
        return DialogSession(stack, contexts)

    async def save(self, key: StorageKey, session: DialogSession) -> None:
        outer = await self.storage.get_data(key)
        contexts: dict[str, dict[str, Any]] = {}
        for intent_id, context in session.contexts.items():
            raw = asdict(context)
            state = context.state.state
            if state is None:
                raise ValueError("Dialog states must have stable names")
            raw["state"] = state
            contexts[intent_id] = raw
        outer[_PAYLOAD_KEY] = {"stack": asdict(session.stack), "contexts": contexts}
        await self.storage.set_data(key, outer)
        current = session.current()
        await self.storage.set_state(key, current.state if current is not None else None)

    @asynccontextmanager
    async def session(self, key: StorageKey) -> AsyncIterator[DialogSession]:
        async with self.isolation.lock(key):
            value = await self.load(key)
            working = deepcopy(value)
            yield working
            await self.save(key, working)


__all__ = ["DialogSession", "DialogStorage"]
