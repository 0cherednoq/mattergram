"""Persistent local identities and secret-free provisioning journal snapshots."""

from __future__ import annotations

import hashlib
import json
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StoreModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)


class ProvisioningScope(StoreModel):
    server: str
    application: str = Field(min_length=1, max_length=256)

    @field_validator("server")
    @classmethod
    def server_root(cls, value: str) -> str:
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
            or any(c.isspace() or ord(c) < 32 for c in value)
        ):
            raise ValueError("Expected an HTTP(S) server root without credentials/query/fragment")
        parts.port
        return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + "/", "", ""))

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class ResourceKey(StoreModel):
    kind: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=256)
    team_id: str | None = Field(default=None, min_length=1)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class ResourceBinding(StoreModel):
    key: ResourceKey
    resource_id: str = Field(min_length=1)
    ownership: Literal["owned", "reused"]
    owner_id: str | None = None
    fingerprint: str | None = None
    has_secret: bool = False


class ProvisioningIntent(StoreModel):
    id: str = Field(min_length=1)
    key: ResourceKey
    action: Literal["create", "update", "reuse"]
    fingerprint: str | None = None
    require_secret: bool = False
    phase: Literal["pending", "indeterminate", "completed", "aborted"] = "pending"
    resource_id: str | None = Field(default=None, min_length=1)


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()
