import functools
from collections.abc import Callable, Sequence
from typing import Any, overload

from mattergram.dispatcher.event.bases import (
    MiddlewareEventType,
    MiddlewareType,
    NextMiddlewareType,
)
from mattergram.dispatcher.event.handler import CallbackType
from mattergram.types import Event


class MiddlewareManager(Sequence[MiddlewareType[Event]]):
    def __init__(self) -> None:
        self._middlewares: list[MiddlewareType[Event]] = []

    def register(
        self,
        middleware: MiddlewareType[Event],
    ) -> MiddlewareType[Event]:
        self._middlewares.append(middleware)
        return middleware

    def unregister(self, middleware: MiddlewareType[Event]) -> None:
        self._middlewares.remove(middleware)

    def __call__(
        self,
        middleware: MiddlewareType[Event] | None = None,
    ) -> Callable[[MiddlewareType[Event]], MiddlewareType[Event]] | MiddlewareType[Event]:
        if middleware is None:
            return self.register
        return self.register(middleware)

    @overload
    def __getitem__(self, item: int) -> MiddlewareType[Event]:
        pass

    @overload
    def __getitem__(self, item: slice) -> Sequence[MiddlewareType[Event]]:
        pass

    def __getitem__(
        self,
        item: int | slice,
    ) -> MiddlewareType[Event] | Sequence[MiddlewareType[Event]]:
        return self._middlewares[item]

    def __len__(self) -> int:
        return len(self._middlewares)

    @staticmethod
    def wrap_middlewares(
        middlewares: Sequence[MiddlewareType[MiddlewareEventType]],
        handler: CallbackType,
    ) -> NextMiddlewareType[MiddlewareEventType]:
        @functools.wraps(handler)
        def handler_wrapper(event: Event, kwargs: dict[str, Any]) -> Any:
            return handler(event, **kwargs)

        middleware = handler_wrapper
        for m in reversed(middlewares):
            middleware = functools.partial(m, middleware)  # type: ignore[assignment]
        return middleware
