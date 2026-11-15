from asyncio import Lock
from collections import defaultdict
from collections.abc import AsyncGenerator, Hashable, Mapping
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, overload

from mattergram.exceptions import DataNotDictLikeError
from mattergram.fsm.state import State
from mattergram.fsm.storage.base import (
    BaseEventIsolation,
    BaseStorage,
    StateType,
    StorageKey,
)


@dataclass
class MemoryStorageRecord:
    data: dict[str, Any] = field(default_factory=dict)
    state: str | None = None


class MemoryStorage(BaseStorage):
    """
    Default FSM storage; uses a regular :class:`dict` to store data and
    does not persist it across restarts.

    .. warning::

        This storage is not recommended for production use, as all data is lost
        when the bot restarts
    """

    def __init__(self) -> None:
        self.storage: defaultdict[StorageKey, MemoryStorageRecord] = defaultdict(
            MemoryStorageRecord,
        )

    async def close(self) -> None:
        self.storage.clear()

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        self.storage[key].state = state.state if isinstance(state, State) else state

    async def get_state(self, key: StorageKey) -> str | None:
        return self.storage[key].state

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        if not isinstance(data, Mapping):
            msg = f"Data must be a dict or dict-like object, got {type(data).__name__}"
            raise DataNotDictLikeError(msg)
        self.storage[key].data = deepcopy(dict(data))

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        return deepcopy(self.storage[key].data)

    @overload
    async def get_value(self, storage_key: StorageKey, dict_key: str) -> Any | None: ...

    @overload
    async def get_value(self, storage_key: StorageKey, dict_key: str, default: Any) -> Any: ...

    async def get_value(
        self,
        storage_key: StorageKey,
        dict_key: str,
        default: Any | None = None,
    ) -> Any | None:
        data = self.storage[storage_key].data
        return deepcopy(data.get(dict_key, default))


class DisabledEventIsolation(BaseEventIsolation):
    @asynccontextmanager
    async def lock(self, key: StorageKey) -> AsyncGenerator[None, None]:
        yield

    async def close(self) -> None:
        pass


class SimpleEventIsolation(BaseEventIsolation):
    def __init__(self) -> None:
        self._locks: dict[Hashable, tuple[Lock, int]] = {}
        self._closed = False

    @asynccontextmanager
    async def lock(self, key: StorageKey) -> AsyncGenerator[None, None]:
        if self._closed:
            raise RuntimeError("Event isolation is closed")
        lock, references = self._locks.get(key, (Lock(), 0))
        self._locks[key] = (lock, references + 1)
        try:
            async with lock:
                yield
        finally:
            current = self._locks.get(key)
            if current is not None:
                if current[1] == 1:
                    del self._locks[key]
                else:
                    self._locks[key] = (lock, current[1] - 1)

    async def close(self) -> None:
        if self._locks:
            raise RuntimeError("Drain handlers before closing event isolation")
        self._closed = True
