"""Strict authored UI declarations; protocol references are recorded separately."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict


class UIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, populate_by_name=True)


def byte_limit(value: str, maximum: int) -> str:
    if len(value.encode("utf-8")) > maximum:
        raise ValueError(f"UI value exceeds {maximum} UTF-8 bytes")
    return value


def integration_url(value: str) -> str:
    if value.startswith(("/plugins/", "plugins/")) and not any(c.isspace() for c in value):
        return value
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or any(ord(c) <= 32 for c in value)
    ):
        raise ValueError("Expected an absolute HTTP(S) URL or a plugin path")
    parsed.port
    return value


def sequence(value: Any) -> Any:
    """Accept ergonomic list declarations while storing isolated tuples."""
    return tuple(value) if isinstance(value, list) else value


def http_url(value: str) -> str:
    integration_url(value)
    if urlsplit(value).scheme not in ("http", "https"):
        raise ValueError("Expected an absolute HTTP(S) URL")
    return value
