"""Public contracts for explicit Mattergram application plugins."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from .builder import PluginBuilder
from .context import PluginContext

_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){0,2}(?:[-+][0-9A-Za-z.-]+)?$")


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """Stable plugin identity, dependency edges and required platform capabilities."""

    id: str
    version: str
    requires: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or _PLUGIN_ID.fullmatch(self.id) is None:
            raise ValueError("Plugin id must be a lowercase dotted, dashed or underscored identifier")
        if not isinstance(self.version, str) or _VERSION.fullmatch(self.version) is None:
            raise ValueError("Plugin version must be a numeric release identifier")
        self._validate_names("requires", self.requires)
        self._validate_names("capabilities", self.capabilities)
        if self.id in self.requires:
            raise ValueError("A plugin cannot depend on itself")

    @staticmethod
    def _validate_names(field: str, values: tuple[str, ...]) -> None:
        if not isinstance(values, tuple) or not all(isinstance(value, str) and value for value in values):
            raise TypeError(f"Plugin {field} must be a tuple of nonempty strings")
        if len(values) != len(set(values)):
            raise ValueError(f"Plugin {field} must be unique")


class MattergramPlugin(Protocol):
    """One explicitly configured reusable application module."""

    manifest: PluginManifest

    def setup(self, builder: PluginBuilder) -> None:
        """Declare contributions without network writes or runtime side effects."""

    async def startup(self, context: PluginContext) -> None:
        """Start runtime resources after the whole plugin graph has been validated."""

    async def shutdown(self, context: PluginContext) -> None:
        """Release resources acquired by :meth:`startup`."""


__all__ = ["MattergramPlugin", "PluginManifest"]
