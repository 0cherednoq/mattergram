from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from magic_filter import MagicFilter

ItemsGetter = str | MagicFilter | Sequence[Any] | Callable[[Mapping[str, Any]], Sequence[Any]]


def get_items(source: ItemsGetter, data: Mapping[str, Any]) -> Sequence[Any]:
    if isinstance(source, str):
        value = data.get(source, ())
    elif isinstance(source, MagicFilter):
        value = source.resolve(data)
    elif callable(source):
        value = source(data)
    else:
        value = source
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("Items getter must return a non-string sequence")
    return value


__all__ = ["ItemsGetter", "get_items"]
