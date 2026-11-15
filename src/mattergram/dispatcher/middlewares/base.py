from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from mattergram.types import Event

T = TypeVar("T")


class BaseMiddleware(ABC):
    """
    Generic middleware class
    """

    @abstractmethod
    async def __call__(
        self,
        handler: Callable[[Event, dict[str, Any]], Awaitable[Any]],
        event: Event,
        data: dict[str, Any],
    ) -> Any:  # pragma: no cover
        """
        Execute middleware

        :param handler: Wrapped handler in middlewares chain
        :param event: Incoming event (Subclass of :class:`aiogram.types.base.Event`)
        :param data: Contextual data. Will be mapped to handler arguments
        :return: :class:`Any`
        """
