from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from magic_filter import MagicFilter

Selector = str | MagicFilter | Callable[[Mapping[str, Any], Any], Any]


def select_value(selector: Selector, data: Mapping[str, Any], manager: Any) -> Any:
    if isinstance(selector, str):
        return data.get(selector)
    if isinstance(selector, MagicFilter):
        return selector.resolve(data)
    return selector(data, manager)


__all__ = ["Selector", "select_value"]
