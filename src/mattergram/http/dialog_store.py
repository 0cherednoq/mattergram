"""Bounded in-process form records with the already ported event isolation."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from copy import deepcopy
from typing import Any, Protocol

from mattergram.exceptions import IngressCapacityError
from mattergram.fsm.storage.base import StorageKey
from mattergram.fsm.storage.memory import SimpleEventIsolation


class DialogStore(Protocol):
    max_ttl: float

    def lock(self, key: StorageKey) -> AbstractAsyncContextManager[None]: ...
    async def get(self, key: StorageKey) -> dict[str, Any] | None: ...
    async def put(self, key: StorageKey, record: dict[str, Any]) -> None: ...
    async def delete(self, key: StorageKey) -> None: ...
    async def close(self) -> None: ...


class MemoryDialogStore:
    def __init__(
        self, *, capacity: int = 1024, max_ttl: float = 1800, clock: Callable[[], float] = time.time
    ) -> None:
        if (
            type(capacity) is not int
            or capacity < 1
            or isinstance(max_ttl, bool)
            or not isinstance(max_ttl, (float, int))
            or not math.isfinite(max_ttl)
            or max_ttl <= 0
        ):
            raise ValueError("Positive form capacity and TTL are required")
        self.capacity = capacity
        self.max_ttl = float(max_ttl)
        self._clock = clock
        self._records: dict[StorageKey, dict[str, Any]] = {}
        self._isolation = SimpleEventIsolation()
        self._closed = False

    def _purge(self) -> None:
        if self._closed:
            raise RuntimeError("Dialog store is closed")
        for key in list(self._records):
            if self._records[key]["expires_at"] <= self._clock():
                del self._records[key]

    def lock(self, key: StorageKey) -> AbstractAsyncContextManager[None]:
        self._purge()
        return self._isolation.lock(key)

    async def get(self, key: StorageKey) -> dict[str, Any] | None:
        self._purge()
        return deepcopy(self._records.get(key))

    async def put(self, key: StorageKey, record: dict[str, Any]) -> None:
        self._purge()
        if key not in self._records and len(self._records) >= self.capacity:
            raise IngressCapacityError("Live dialog session capacity is exhausted")
        self._records[key] = deepcopy(record)

    async def delete(self, key: StorageKey) -> None:
        self._purge()
        self._records.pop(key, None)

    async def close(self) -> None:
        await self._isolation.close()
        self._closed = True
        self._records.clear()
