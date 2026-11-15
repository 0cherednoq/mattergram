"""Explicit low-level client backed by the generated operation registry."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, TypeAlias
from urllib.parse import urlencode

from mattergram._http_client import _HTTPClient
from mattergram.exceptions import ProtocolError, RequestOutcomeUnknown

from ._operations import OPERATIONS
from .operation import NativeOperation

QueryScalar: TypeAlias = str | int | float | bool
QueryValue: TypeAlias = QueryScalar | Sequence[QueryScalar]
_QUERY_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*")
_UNSET = object()


def _query_scalar(value: QueryScalar) -> QueryScalar:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _json_value(value: Any, *, seen: frozenset[int] = frozenset(), depth: int = 0) -> Any:
    if depth > 64:
        raise ValueError("Native JSON request body exceeds the nesting limit")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Native JSON numbers must be finite")
        return value
    if isinstance(value, Mapping):
        if id(value) in seen:
            raise ValueError("Native JSON request body contains a cycle")
        nested = seen | {id(value)}
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Native JSON object keys must be strings")
            result[key] = _json_value(item, seen=nested, depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, memoryview)):
        if id(value) in seen:
            raise ValueError("Native JSON request body contains a cycle")
        nested = seen | {id(value)}
        return [_json_value(item, seen=nested, depth=depth + 1) for item in value]
    raise TypeError("Native request body must contain only JSON values")


def _query_string(query: Mapping[str, QueryValue]) -> str:
    values: list[tuple[str, QueryScalar]] = []
    for name, raw in query.items():
        if not isinstance(name, str) or _QUERY_NAME.fullmatch(name) is None:
            raise ValueError("Native query names must be simple nonempty identifiers")
        items: Sequence[QueryScalar]
        if isinstance(raw, (str, int, float, bool)):
            items = (raw,)
        elif isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray, memoryview)):
            items = raw
        else:
            raise TypeError(f"Unsupported native query value for {name!r}")
        if not items:
            raise ValueError(f"Native query sequence {name!r} cannot be empty")
        for value in items:
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"Native query value {name!r} must be finite")
            if not isinstance(value, (str, int, float, bool)):
                raise TypeError(f"Unsupported native query item for {name!r}")
            values.append((name, _query_scalar(value)))
    return urlencode(values)


class NativeAPI(_HTTPClient):
    """Low-level JSON operations with exact generated paths and success statuses."""

    @property
    def operations(self) -> Mapping[str, NativeOperation]:
        return OPERATIONS

    async def call(
        self,
        operation: str | NativeOperation,
        *,
        path: Mapping[str, str] | None = None,
        query: Mapping[str, QueryValue] | None = None,
        body: Any = _UNSET,
    ) -> Any:
        if isinstance(operation, str):
            try:
                spec = OPERATIONS[operation]
            except KeyError:
                raise ValueError(f"Unknown generated native operation: {operation!r}") from None
        elif isinstance(operation, NativeOperation):
            registered = OPERATIONS.get(operation.operation_id)
            if registered != operation:
                raise ValueError("Native operation is not from the generated registry")
            spec = operation
        else:
            raise TypeError("operation must be an operationId or generated NativeOperation")
        rendered = spec.render_path(dict(path or {}))
        if query:
            rendered += "?" + _query_string(query)
        if spec.body == "required" and body is _UNSET:
            raise ValueError(f"{spec.operation_id} requires a JSON request body")
        if spec.body == "forbidden" and body is not _UNSET:
            raise ValueError(f"{spec.operation_id} does not accept a JSON request body")
        payload = None if body is _UNSET else _json_value(body)
        try:
            return await self._request_json(spec.method, rendered, spec.success_statuses, payload)
        except ProtocolError:
            if spec.method not in {"GET", "HEAD", "OPTIONS"}:
                raise RequestOutcomeUnknown(
                    "Native write response was not confirmed; reconcile before retrying"
                ) from None
            raise
