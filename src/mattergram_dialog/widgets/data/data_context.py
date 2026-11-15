from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

Getter = Callable[..., Mapping[str, Any] | Awaitable[Mapping[str, Any]]]


async def invoke_getter(getter: Getter, manager: Any) -> dict[str, Any]:
    parameters = inspect.signature(getter).parameters
    value = getter(manager=manager) if "manager" in parameters else getter()
    if inspect.isawaitable(value):
        value = await value
    if not isinstance(value, Mapping):
        raise TypeError("Dialog getter must return a mapping")
    return dict(value)


async def load_data(getters: tuple[Getter, ...], manager: Any, base: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for getter in getters:
        result.update(await invoke_getter(getter, manager))
    return result


__all__ = ["Getter", "invoke_getter", "load_data"]
