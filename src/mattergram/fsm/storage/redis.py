import asyncio
import json
import math
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from redis.asyncio.client import Redis
from redis.asyncio.connection import ConnectionPool
from redis.asyncio.lock import Lock
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.typing import ExpiryT

from mattergram.exceptions import DataNotDictLikeError, StorageDataError
from mattergram.fsm.state import State
from mattergram.fsm.storage.base import (
    BaseEventIsolation,
    BaseStorage,
    DefaultKeyBuilder,
    KeyBuilder,
    StateType,
    StorageKey,
)

DEFAULT_REDIS_LOCK_KWARGS = {"timeout": 60.0, "blocking_timeout": 10.0, "sleep": 0.1}
_JsonLoads = Callable[..., Any]
_JsonDumps = Callable[..., str]


def _positive(value: Any, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be a positive finite number")
    return float(value)


def _ttl(value: ExpiryT | None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, timedelta)):
        raise ValueError("TTL must be an integer number of seconds or timedelta")
    seconds = value.total_seconds() if isinstance(value, timedelta) else value
    if _positive(seconds, "TTL") < 1:
        raise ValueError("TTL must be at least one second")


async def _release_lock(lock: Lock, timeout: float) -> None:
    """Finish bounded release even if the owner receives another cancellation."""

    async def release() -> None:
        async with asyncio.timeout(timeout):
            await lock.release()

    task = asyncio.create_task(release())
    interrupted: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            interrupted = error
    task.result()
    if interrupted is not None:
        raise interrupted


class RedisStorage(BaseStorage):
    """
    Redis storage requires the :code:`redis` package (:code:`pip install redis`)
    """

    def __init__(
        self,
        redis: Redis,
        key_builder: KeyBuilder | None = None,
        state_ttl: ExpiryT | None = None,
        data_ttl: ExpiryT | None = None,
        json_loads: _JsonLoads = json.loads,
        json_dumps: _JsonDumps = json.dumps,
        *,
        owns_client: bool = False,
    ) -> None:
        """
        :param redis: instance of Redis connection
        :param key_builder: builder that helps to convert contextual key to string
        :param state_ttl: TTL for state records
        :param data_ttl: TTL for data records
        """
        if key_builder is None:
            key_builder = DefaultKeyBuilder()
        self.redis = redis
        if type(owns_client) is not bool:
            raise ValueError("owns_client must be a bool")
        self._owns_client = owns_client
        self._closed = False
        self.key_builder = key_builder
        _ttl(state_ttl)
        _ttl(data_ttl)
        self.state_ttl = state_ttl
        self.data_ttl = data_ttl
        self.json_loads = json_loads
        self.json_dumps = json_dumps

    @classmethod
    def from_url(
        cls,
        url: str,
        connection_kwargs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> "RedisStorage":
        """
        Create an instance of :class:`RedisStorage` with the specified connection url

        :param url: the connection url (i.e. :code:`redis://user:password@host:port/db`)
        :param connection_kwargs: see :code:`redis` docs
        :param kwargs: arguments passed to :class:`RedisStorage`
        :return: an instance of :class:`RedisStorage`
        """
        connection_kwargs = {
            "socket_connect_timeout": 5.0,
            "socket_timeout": 5.0,
            "retry": Retry(NoBackoff(), 0),
            **(connection_kwargs or {}),
        }
        pool = ConnectionPool.from_url(url, **connection_kwargs)
        redis = Redis(connection_pool=pool)
        return cls(redis=redis, owns_client=True, **kwargs)

    def create_isolation(self, **kwargs: Any) -> "RedisEventIsolation":
        self._check_open()
        return RedisEventIsolation(redis=self.redis, key_builder=self.key_builder, **kwargs)

    @property
    def is_closed(self) -> bool:
        return self._closed

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("RedisStorage is closed")

    async def close(self) -> None:
        self._closed = True
        if self._owns_client:
            await self.redis.aclose(close_connection_pool=True)

    async def set_state(
        self,
        key: StorageKey,
        state: StateType = None,
    ) -> None:
        self._check_open()
        redis_key = self.key_builder.build(key, "state")
        state = state.state if isinstance(state, State) else state
        if state is not None and not isinstance(state, str):
            raise TypeError("FSM state must be a string, State or None")
        if state is None:
            await self.redis.delete(redis_key)
        else:
            await self.redis.set(
                redis_key,
                state,
                ex=self.state_ttl,
            )

    async def get_state(
        self,
        key: StorageKey,
    ) -> str | None:
        self._check_open()
        redis_key = self.key_builder.build(key, "state")
        value = await self.redis.get(redis_key)
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except UnicodeError:
                raise StorageDataError("Stored FSM state is not UTF-8") from None
        if value is None or isinstance(value, str):
            return value
        raise StorageDataError("Stored FSM state is not a string")

    async def set_data(
        self,
        key: StorageKey,
        data: Mapping[str, Any],
    ) -> None:
        self._check_open()
        if not isinstance(data, Mapping):
            msg = f"Data must be a dict or dict-like object, got {type(data).__name__}"
            raise DataNotDictLikeError(msg)

        redis_key = self.key_builder.build(key, "data")
        if not all(isinstance(item, str) for item in data):
            raise TypeError("FSM data keys must be strings")
        if not data:
            await self.redis.delete(redis_key)
            return
        await self.redis.set(
            redis_key,
            self.json_dumps(dict(data)),
            ex=self.data_ttl,
        )

    async def get_data(
        self,
        key: StorageKey,
    ) -> dict[str, Any]:
        self._check_open()
        redis_key = self.key_builder.build(key, "data")
        value = await self.redis.get(redis_key)
        if value is None:
            return {}
        try:
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            data = self.json_loads(value)
        except (ValueError, TypeError, UnicodeError):
            raise StorageDataError("Stored FSM data is not valid JSON") from None
        if not isinstance(data, dict) or not all(isinstance(item, str) for item in data):
            raise StorageDataError("Stored FSM data must be an object with string keys")
        return data


class RedisEventIsolation(BaseEventIsolation):
    def __init__(
        self,
        redis: Redis,
        key_builder: KeyBuilder | None = None,
        lock_kwargs: dict[str, Any] | None = None,
        *,
        owns_client: bool = False,
        hold_timeout: float | None = None,
    ) -> None:
        if key_builder is None:
            key_builder = DefaultKeyBuilder()
        settings = {**DEFAULT_REDIS_LOCK_KWARGS, **(lock_kwargs or {})}
        if set(settings) - {"timeout", "blocking_timeout", "sleep"}:
            raise ValueError("Supported lock settings: timeout, blocking_timeout, sleep")
        for name, value in settings.items():
            settings[name] = _positive(value, name)
        lease = settings["timeout"]
        hold_timeout = _positive(hold_timeout if hold_timeout is not None else lease * 0.8, "hold_timeout")
        if hold_timeout >= lease:
            raise ValueError("hold_timeout must be shorter than the lock lease")
        self.redis = redis
        if type(owns_client) is not bool:
            raise ValueError("owns_client must be a bool")
        self._owns_client = owns_client
        self._closed = False
        self.key_builder = key_builder
        self.lock_kwargs = settings
        self.hold_timeout = hold_timeout
        self._active_locks = 0

    @classmethod
    def from_url(
        cls,
        url: str,
        connection_kwargs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> "RedisEventIsolation":
        connection_kwargs = {
            "socket_connect_timeout": 5.0,
            "socket_timeout": 5.0,
            "retry": Retry(NoBackoff(), 0),
            **(connection_kwargs or {}),
        }
        pool = ConnectionPool.from_url(url, **connection_kwargs)
        redis = Redis(connection_pool=pool)
        return cls(redis=redis, owns_client=True, **kwargs)

    @asynccontextmanager
    async def lock(
        self,
        key: StorageKey,
    ) -> AsyncGenerator[None, None]:
        if self._closed:
            raise RuntimeError("RedisEventIsolation is closed")
        self._active_locks += 1
        try:
            redis_key = self.key_builder.build(key, "lock")
            # Start before SET: acquisition/transport latency consumes the local
            # budget rather than extending it past the server-side lease.
            deadline = asyncio.get_running_loop().time() + self.hold_timeout
            task = asyncio.current_task()
            if task is None:
                raise RuntimeError("Redis isolation requires an asyncio task")
            cancellations = task.cancelling()
            lock = self.redis.lock(name=redis_key, **self.lock_kwargs, lock_class=Lock)
            acquired = False
            try:
                # Retain redis-py's token/SET-NX/Lua ownership, but make the
                # polling boundary ours. In the verified 3.11/redis-py 8.1 path
                # a completed command can swallow cancellation; checking the
                # task count prevents its blocking acquire loop from continuing.
                async with asyncio.timeout(self.lock_kwargs["blocking_timeout"]):
                    while not acquired:
                        if task.cancelling() > cancellations:
                            raise asyncio.CancelledError
                        acquired = await lock.acquire(blocking=False)
                        if task.cancelling() > cancellations:
                            raise asyncio.CancelledError
                        if not acquired:
                            await asyncio.sleep(self.lock_kwargs["sleep"])
                # Lease is deliberately longer than handler time. Cancellation-
                # cooperative handlers cannot silently outlive their local lease.
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("FSM lock acquisition consumed the handler budget")
                async with asyncio.timeout_at(deadline) as budget:
                    yield None
                if budget.expired():
                    raise TimeoutError("FSM handler exceeded its lease budget")
            finally:
                if acquired:
                    await _release_lock(lock, self.lock_kwargs["blocking_timeout"])
        finally:
            self._active_locks -= 1

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def close(self) -> None:
        if self._active_locks:
            raise RuntimeError("Drain handlers and waiters before closing RedisEventIsolation")
        self._closed = True
        if self._owns_client:
            await self.redis.aclose(close_connection_pool=True)
