"""Prepared runtime data contains no setup credential or setup client."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from mattergram.http.auth import SlashBinding

from .models import NativeChannel, NativeTeam
from .store_models import ResourceKey


@dataclass(frozen=True)
class ProvisioningStep:
    key: ResourceKey
    status: Literal["created", "reused", "updated", "skipped", "failed"]
    resource_id: str | None = None
    error_type: str | None = None


@dataclass(frozen=True)
class ProvisioningReport:
    steps: tuple[ProvisioningStep, ...] = ()
    runtime_verified: bool = False
    callback_verified: bool = False


@dataclass(frozen=True)
class RuntimeResources:
    teams: Mapping[str, NativeTeam]
    channels: Mapping[str, NativeChannel]
    slash_bindings: tuple[SlashBinding, ...] = field(repr=False)
    callback_url: str
    http_path_prefix: str
    report: ProvisioningReport
