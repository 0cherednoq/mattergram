"""Shared strict boundary for authored, supported-subset UI input adapters."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any, TypeVar

from pydantic import ValidationError

from .actions import Action, External, OpenURL, action_id
from .base import UIModel

Model = TypeVar("Model", bound=UIModel)


def literal_text(text: str) -> str:
    """Escape ASCII Markdown punctuation in source plain-text display values."""
    return re.sub(r"([!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~])", r"\\\1", text)


def _same_json(left: Any, right: Any) -> bool:
    try:
        return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
            right, sort_keys=True, allow_nan=False
        )
    except (TypeError, ValueError, RecursionError):
        return False


class UIConversionError(ValueError):
    """A source location cannot be represented by the selected native subset.

    Messages contain paths and authored reasons, never source values or secrets.
    """

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


def budget(value: Any) -> None:
    """Bound arbitrary JSON before recursive conversion, including Python cycles."""
    remaining = 10000
    text_bytes = 0
    ancestors: set[int] = set()

    def visit(item: Any, path: str, depth: int) -> None:
        nonlocal remaining, text_bytes
        remaining -= 1
        if remaining < 0 or depth > 64:
            raise UIConversionError(path, "Input exceeds the 10000-value or 64-level limit")
        if isinstance(item, str):
            try:
                text_bytes += len(item.encode("utf-8"))
            except UnicodeError:
                raise UIConversionError(path, "Input must contain valid Unicode") from None
            if text_bytes > 1048576:
                raise UIConversionError(path, "Input strings exceed 1 MiB")
        elif isinstance(item, (dict, list, tuple)):
            if id(item) in ancestors:
                raise UIConversionError(path, "Cyclic input is not JSON")
            ancestors.add(id(item))
            if isinstance(item, dict):
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise UIConversionError(path, "Object keys must be strings")
                    visit(key, path, depth + 1)
                    visit(child, path + ".<property>", depth + 1)
            else:
                for index, child in enumerate(item):
                    visit(child, f"{path}[{index}]", depth + 1)
            ancestors.remove(id(item))
        elif item is None or type(item) in (bool, int):
            return
        elif type(item) is float and math.isfinite(item):
            return
        else:
            raise UIConversionError(path, "Expected finite JSON values")

    visit(value, "$", 0)


def obj(value: Any, path: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise UIConversionError(path, "Expected an object")
    if value.keys() - allowed:
        raise UIConversionError(path, "Unsupported properties; conversion would discard source semantics")
    return value


def array(value: Any, path: str, *, minimum: int = 0, maximum: int = 100) -> list[Any] | tuple[Any, ...]:
    if not isinstance(value, (list, tuple)) or not minimum <= len(value) <= maximum:
        raise UIConversionError(path, f"Expected an array with {minimum}..{maximum} entries")
    return value


def string(value: Any, path: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value):
        raise UIConversionError(path, "Expected a nonempty string" if not empty else "Expected a string")
    return value


def boolean(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise UIConversionError(path, "Expected a boolean")
    return bool(value)


def choice(value: Any, path: str, choices: Mapping[str, str]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise UIConversionError(path, "Unsupported value for this native target")
    return choices[value]


def native(model: type[Model], path: str, **values: Any) -> Model:
    try:
        return model(**values)
    except ValidationError:
        raise UIConversionError(path, "Value is invalid for the native target") from None


class Registry:
    def __init__(self, actions: Mapping[str, Action]) -> None:
        self.actions = dict(actions)
        self.used: set[str] = set()
        self.identifiers: set[str] = set()
        for key, value in self.actions.items():
            if not isinstance(key, str) or not isinstance(value, (External, OpenURL)):
                raise UIConversionError(
                    "$.actions", "Expected action IDs mapped to native Action declarations"
                )

    def bind(
        self, identifier: Any, path: str, *, url: str | None = None, external: bool = False
    ) -> tuple[str | None, Action]:
        if identifier is None and url is not None:
            return None, native(OpenURL, path, url=url)
        key = string(identifier, path)
        try:
            action_id(key)
        except ValueError:
            raise UIConversionError(path, "Source ID cannot be used as a native action ID") from None
        if key in self.identifiers:
            raise UIConversionError(path, "Duplicate action ID across the input")
        self.identifiers.add(key)
        action = self.actions.get(key)
        if action is None:
            if url is None:
                raise UIConversionError(path, "Missing explicit action binding")
            action = native(OpenURL, path, url=url)
        else:
            self.used.add(key)
            if url is not None and (not isinstance(action, OpenURL) or action.url != url or action.query):
                raise UIConversionError(path, "URL and action binding disagree")
        if external and not isinstance(action, External):
            raise UIConversionError(path, "This source action requires an External callback binding")
        return key, action.model_copy(deep=True)

    def data(self, action: Action, data: Any, path: str) -> None:
        if not isinstance(action, External) or not (
            (isinstance(data, str) and action.data == data)
            or (isinstance(data, dict) and action.data is None and _same_json(action.context, data))
        ):
            raise UIConversionError(path, "Embedded data must exactly match the explicit External binding")

    def finish(self) -> None:
        if self.actions.keys() - self.used:
            raise UIConversionError("$.actions", "Unused action bindings")
