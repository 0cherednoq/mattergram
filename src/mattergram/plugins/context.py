"""Immutable runtime context shared with plugin lifecycle hooks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

if TYPE_CHECKING:
    from mattergram.bot import Bot
    from mattergram.dispatcher.dispatcher import Dispatcher
    from mattergram.provisioning.commands import CommandManifest
    from mattergram_dialog.registry import DialogRegistry

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ProviderKey(Generic[T]):
    """Typed token whose name is also used by Mattergram's named handler DI."""

    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.isidentifier() or self.name.startswith("_"):
            raise ValueError("Provider name must be a public Python identifier")


@dataclass(frozen=True, slots=True)
class PluginContext:
    """Runtime objects available only to plugin lifecycle hooks."""

    bot: Bot
    dispatcher: Dispatcher
    dialog_registry: DialogRegistry
    command_manifest: CommandManifest
    providers: Mapping[ProviderKey[Any], object]
    capabilities: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "providers", MappingProxyType(dict(self.providers)))

    def provider(self, key: ProviderKey[T]) -> T:
        """Resolve a typed provider contributed during setup."""

        try:
            return cast(T, self.providers[key])
        except KeyError:
            raise LookupError(f"Provider {key.name!r} is not registered") from None


__all__ = ["PluginContext", "ProviderKey"]
