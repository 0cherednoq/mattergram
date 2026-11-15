from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import Any

from magic_filter import MagicFilter

WhenCondition = str | MagicFilter | Callable[[Mapping[str, Any], Any], bool | Any] | None


async def is_visible(condition: WhenCondition, data: Mapping[str, Any], manager: Any) -> bool:
    if condition is None:
        return True
    if isinstance(condition, str):
        return bool(data.get(condition))
    if isinstance(condition, MagicFilter):
        return bool(condition.resolve(data))
    result = condition(data, manager)
    if inspect.isawaitable(result):
        result = await result
    return bool(result)


__all__ = ["WhenCondition", "is_visible"]
