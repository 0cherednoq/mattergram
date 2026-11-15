import json
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, overload

from mattergram.fsm.state import State

StateType = str | State | None

DEFAULT_DESTINY = "default"


@dataclass(frozen=True)
class StorageKey:
    instance_id: str
    bot_id: str
    channel_id: str | None
    user_id: str | None
    thread_id: str | None = None
    team_id: str | None = None
    dialog_session_id: str | None = None
    destiny: str = DEFAULT_DESTINY

    def __post_init__(self) -> None:
        for value in (self.instance_id, self.bot_id, self.destiny):
            if not isinstance(value, str) or not value:
                raise ValueError("FSM instance/bot/destiny must be nonempty strings")
        for optional_value in (
            self.channel_id,
            self.user_id,
            self.thread_id,
            self.team_id,
            self.dialog_session_id,
        ):
            if optional_value is not None and (not isinstance(optional_value, str) or not optional_value):
                raise ValueError("FSM IDs must be nonempty strings or an explicit absent scope")


class KeyBuilder(ABC):
    @abstractmethod
    def build(self, key: StorageKey, part: Literal["data", "state", "lock"] | None = None) -> str:
        raise NotImplementedError


class DefaultKeyBuilder(KeyBuilder):
    """Always namespace instance, bot and native scopes; JSON avoids delimiter collisions."""

    def __init__(self, *, prefix: str = "fsm", separator: str = ":") -> None:
        self.prefix = prefix
        self.separator = separator

    def build(self, key: StorageKey, part: Literal["data", "state", "lock"] | None = None) -> str:
        values = [
            key.instance_id,
            key.bot_id,
            key.channel_id,
            key.user_id,
            key.thread_id,
            key.team_id,
            key.dialog_session_id,
            key.destiny,
            part,
        ]
        return self.prefix + self.separator + json.dumps(values, ensure_ascii=True, separators=(",", ":"))


class BaseStorage(ABC):
    """
    Base class for all FSM storages
    """

    @abstractmethod
    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        """
        Set state for specified key

        :param key: storage key
        :param state: new state
        """

    @abstractmethod
    async def get_state(self, key: StorageKey) -> str | None:
        """
        Get key state

        :param key: storage key
        :return: current state
        """

    @abstractmethod
    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        """
        Write data (replace)

        :param key: storage key
        :param data: new data
        """

    @abstractmethod
    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        """
        Get current data for key

        :param key: storage key
        :return: current data
        """

    @overload
    async def get_value(self, storage_key: StorageKey, dict_key: str) -> Any | None:
        """
        Get single value from data by key

        :param storage_key: storage key
        :param dict_key: value key
        :return: value stored in key of dict or ``None``
        """

    @overload
    async def get_value(self, storage_key: StorageKey, dict_key: str, default: Any) -> Any:
        """
        Get single value from data by key

        :param storage_key: storage key
        :param dict_key: value key
        :param default: default value to return
        :return: value stored in key of dict or default
        """

    async def get_value(
        self,
        storage_key: StorageKey,
        dict_key: str,
        default: Any | None = None,
    ) -> Any | None:
        data = await self.get_data(storage_key)
        return data.get(dict_key, default)

    async def update_data(self, key: StorageKey, data: Mapping[str, Any]) -> dict[str, Any]:
        """
        Update date in the storage for key (like dict.update)

        :param key: storage key
        :param data: partial data
        :return: new data
        """
        current_data = await self.get_data(key=key)
        current_data.update(data)
        await self.set_data(key=key, data=current_data)
        return current_data.copy()

    @abstractmethod
    async def close(self) -> None:  # pragma: no cover
        """
        Close storage (database connection, file or etc.)
        """


class BaseEventIsolation(ABC):
    @abstractmethod
    @asynccontextmanager
    async def lock(self, key: StorageKey) -> AsyncGenerator[None, None]:
        """
        Isolate events with lock.
        Will be used as context manager

        :param key: storage key
        :return: An async generator
        """
        yield None

    @abstractmethod
    async def close(self) -> None:
        pass
