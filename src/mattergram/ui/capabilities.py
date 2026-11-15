"""Explicit server/client capability evidence; unknown is never silently supported."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from mattergram.exceptions import UnsupportedCapabilityError

Support = Literal["supported", "unsupported", "unknown"]

_BASIC = frozenset(
    {"forms.basic", "forms.multi", "forms.lookup", "forms.refresh", "forms.steps", "forms.date"}
)
_CONFIG = frozenset({"forms.datetime_config", "forms.timezone", "forms.manual_time"})
_MODERN = frozenset({"forms.date_bounds_config", "forms.file", "forms.action_button"})
_TWELVE = frozenset(
    {"forms.checkbox_group", "forms.checkbox_matrix", "forms.collapsible", "forms.label_position", "blocks"}
)
SERVER_FEATURES: dict[str, frozenset[str]] = {
    "11.1.0": _BASIC,
    "11.7.0": _BASIC | _CONFIG,
    "11.11.0": _BASIC | _CONFIG | _MODERN | {"blocks"},
    "12.0.2": _BASIC | _CONFIG | _MODERN | _TWELVE,
}


@dataclass(frozen=True)
class Capability:
    status: Support
    source: str
    checked_at: str = "2026-11-12"
    reason: str = ""


@dataclass(frozen=True)
class UIProfile:
    """Exact referenced server plus explicitly established client/flag capabilities.

    client_features is an application declaration, not an automatic assertion that
    every web/desktop/mobile version implements those features. Record its evidence.
    Overrides permit an explicit experiment with unknown support, never a known
    unsupported server field. Permissions and native validation still apply.
    """

    server_version: str
    client: str = "unknown"
    client_features: frozenset[str] = field(default_factory=frozenset)
    client_evidence: str = "unverified"
    mm_blocks_enabled: bool | None = None
    overrides: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.client_features, frozenset) or not isinstance(self.overrides, frozenset):
            raise TypeError("Client features and overrides must be explicit frozensets")
        if self.mm_blocks_enabled is not None and type(self.mm_blocks_enabled) is not bool:
            raise TypeError("MmBlocksEnabled must be a bool or unknown")

    def capability(self, feature: str) -> Capability:
        server = SERVER_FEATURES.get(self.server_version)
        source = "manifests/ui-profile.json"
        if server is not None and feature not in server:
            return Capability("unsupported", source, reason="Not in this referenced server schema")
        if feature == "blocks" and self.mm_blocks_enabled is False:
            return Capability("unsupported", source, reason="MmBlocksEnabled is false")
        if (
            server is None
            or feature not in self.client_features
            or (feature == "blocks" and self.mm_blocks_enabled is None)
        ):
            if feature in self.overrides:
                return Capability(
                    "supported",
                    "explicit application override",
                    reason="Unverified support explicitly requested",
                )
            return Capability("unknown", source, reason="Server, client or feature-flag evidence is missing")
        return Capability(
            "supported", self.client_evidence, reason="Referenced server and declared client support"
        )

    def require(self, feature: str) -> None:
        capability = self.capability(feature)
        if capability.status != "supported":
            raise UnsupportedCapabilityError(f"{feature}: {capability.status}; {capability.reason}")
