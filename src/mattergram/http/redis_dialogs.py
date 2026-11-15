"""Optional shared form records using the reviewed Redis storage/isolation port."""

from __future__ import annotations

import math
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from mattergram.exceptions import StorageDataError
from mattergram.fsm.storage.base import DefaultKeyBuilder, StorageKey
from mattergram.fsm.storage.redis import RedisEventIsolation, RedisStorage


class RedisDialogStore:
    """Constructor borrows resources; from_url owns its one pool and isolation."""

    def __init__(
        self, storage: RedisStorage, isolation: RedisEventIsolation, *, owns_resources: bool = False
    ) -> None:
        ttl = storage.data_ttl
        seconds = ttl.total_seconds() if isinstance(ttl, timedelta) else ttl
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (float, int))
            or not math.isfinite(seconds)
            or seconds <= 0
        ):
            raise ValueError("Redis dialog records require a finite positive data_ttl")
        self.max_ttl = float(seconds)
        self.storage = storage
        self.isolation = isolation
        self._owns = owns_resources
        self._closed = False
        self._active = 0

    @classmethod
    def from_url(cls, url: str, *, ttl: int = 1800, prefix: str = "mattergram-dialog") -> RedisDialogStore:
        storage = RedisStorage.from_url(url, data_ttl=ttl, key_builder=DefaultKeyBuilder(prefix=prefix))
        isolation = storage.create_isolation(
            lock_kwargs={"timeout": 60, "blocking_timeout": 5, "sleep": 0.01}, hold_timeout=45
        )
        return cls(storage, isolation, owns_resources=True)

    def _check(self) -> None:
        if self._closed:
            raise RuntimeError("Redis dialog store is closed")

    @asynccontextmanager
    async def lock(self, key: StorageKey) -> AsyncGenerator[None, None]:
        self._check()
        self._active += 1
        try:
            async with self.isolation.lock(key):
                yield
        finally:
            self._active -= 1

    async def get(self, key: StorageKey) -> dict[str, Any] | None:
        self._check()
        data = await self.storage.get_data(key)
        if not data:
            return None
        record = data.get("record")
        if not isinstance(record, dict) or not all(isinstance(k, str) for k in record):
            raise StorageDataError("Invalid stored dialog record")
        return record

    async def put(self, key: StorageKey, record: dict[str, Any]) -> None:
        self._check()
        await self.storage.set_data(key, {"record": record})

    async def delete(self, key: StorageKey) -> None:
        self._check()
        await self.storage.set_data(key, {})

    async def close(self) -> None:
        if self._active:
            raise RuntimeError("Drain dialog requests before closing their store")
        if self._owns:
            await self.isolation.close()
            await self.storage.close()
        self._closed = True
