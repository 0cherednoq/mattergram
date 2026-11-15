from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

from mattergram.types import Event

if TYPE_CHECKING:
    from mattergram.bot import Bot

T = TypeVar("T")


class BaseHandlerMixin(Generic[T]):
    if TYPE_CHECKING:
        event: T
        data: dict[str, Any]


class BaseHandler(BaseHandlerMixin[T], ABC):
    """
    Base class for all class-based handlers
    """

    def __init__(self, event: T, **kwargs: Any) -> None:
        self.event: T = event
        self.data: dict[str, Any] = kwargs

    @property
    def bot(self) -> Bot:
        from mattergram.bot import Bot

        if "bot" in self.data:
            return cast(Bot, self.data["bot"])
        msg = "Bot instance not found in the context"
        raise RuntimeError(msg)

    @property
    def update(self) -> Event:
        return cast(Event, self.event)

    @abstractmethod
    async def handle(self) -> Any:  # pragma: no cover
        pass

    def __await__(self) -> Any:
        return self.handle().__await__()
