"""Side-effect-free collection of one application's plugin contributions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from mattergram.dispatcher.router import Router
from mattergram.provisioning.commands import CommandSpec
from mattergram_dialog.dialog import Dialog

from .context import PluginContext, ProviderKey
from .exceptions import PluginValidationError

if TYPE_CHECKING:
    from collections.abc import Sequence

T = TypeVar("T")
HealthCheck = Callable[[PluginContext], Awaitable[None] | None]


@dataclass(slots=True)
class PluginBuilder:
    """Restricted registration surface passed to ``MattergramPlugin.setup``."""

    plugin_id: str
    routers: list[Router] = field(default_factory=list, init=False)
    dialogs: list[Dialog] = field(default_factory=list, init=False)
    commands: list[CommandSpec] = field(default_factory=list, init=False)
    providers: dict[ProviderKey[Any], object] = field(default_factory=dict, init=False)
    health_checks: list[HealthCheck] = field(default_factory=list, init=False)

    def include_router(self, router: Router) -> Router:
        if not isinstance(router, Router):
            raise TypeError("Plugin router must be a Router")
        if any(existing is router for existing in self.routers):
            raise PluginValidationError(f"Plugin {self.plugin_id!r} registered one router twice")
        self.routers.append(router)
        return router

    def include_dialog(self, dialog: Dialog) -> Dialog:
        if not isinstance(dialog, Dialog):
            raise TypeError("Plugin dialog must be a Dialog")
        if any(existing is dialog for existing in self.dialogs):
            raise PluginValidationError(f"Plugin {self.plugin_id!r} registered one dialog twice")
        self.dialogs.append(dialog)
        return dialog

    def add_commands(self, *specs: CommandSpec) -> None:
        if not specs:
            raise ValueError("At least one command spec is required")
        if not all(isinstance(spec, CommandSpec) for spec in specs):
            raise TypeError("Plugin commands must be CommandSpec instances")
        self.commands.extend(specs)

    def provide(self, interface: ProviderKey[T], value: T) -> None:
        if not isinstance(interface, ProviderKey):
            raise TypeError("Plugin provider interface must be a ProviderKey")
        if interface in self.providers:
            raise PluginValidationError(
                f"Plugin {self.plugin_id!r} registered provider {interface.name!r} twice"
            )
        self.providers[interface] = value

    def add_health_check(self, check: HealthCheck) -> None:
        if not callable(check):
            raise TypeError("Plugin health check must be callable")
        self.health_checks.append(check)

    def all_contributions(self) -> Sequence[object]:
        """Return a diagnostic snapshot without exposing mutation helpers."""

        return (*self.routers, *self.dialogs, *self.commands, *self.providers, *self.health_checks)


__all__ = ["HealthCheck", "PluginBuilder"]
