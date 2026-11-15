"""Validation, composition and lifecycle for explicit application plugins."""

from __future__ import annotations

import inspect
from collections.abc import Iterable, Mapping, Sequence

from mattergram.bot import Bot
from mattergram.dispatcher.dispatcher import Dispatcher
from mattergram.provisioning.commands import CommandManifest, CommandSpec, collect_commands
from mattergram_dialog.registry import DialogRegistry

from .api import MattergramPlugin, PluginManifest
from .builder import PluginBuilder
from .context import PluginContext
from .exceptions import (
    MissingPluginCapabilityError,
    PluginLifecycleError,
    PluginValidationError,
)


class PluginManager:
    """Compose plugins over the existing Router, DialogRegistry and named DI."""

    def __init__(
        self,
        *,
        bot: Bot,
        dispatcher: Dispatcher,
        plugins: Sequence[MattergramPlugin],
        dialog_registry: DialogRegistry | None = None,
        capabilities: Iterable[str] = (),
    ) -> None:
        if not isinstance(dispatcher, Dispatcher):
            raise TypeError("PluginManager dispatcher must be a Dispatcher")
        self.bot = bot
        self.dispatcher = dispatcher
        self.dialog_registry = dialog_registry if dialog_registry is not None else DialogRegistry()
        self.plugins = tuple(plugins)
        self.capabilities = frozenset(capabilities)
        if not all(isinstance(value, str) and value for value in self.capabilities):
            raise TypeError("Plugin capabilities must be nonempty strings")
        self._ordered: tuple[MattergramPlugin, ...] = ()
        self._builders: dict[str, PluginBuilder] = {}
        self._context: PluginContext | None = None
        self._started: list[MattergramPlugin] = []
        self._configured = False

    @property
    def context(self) -> PluginContext:
        if self._context is None:
            raise RuntimeError("PluginManager has not been set up")
        return self._context

    @property
    def ordered_plugin_ids(self) -> tuple[str, ...]:
        return tuple(plugin.manifest.id for plugin in self._ordered)

    def setup(self) -> PluginContext:
        """Validate all declarations, then atomically attach their contributions."""

        if self._configured:
            raise RuntimeError("PluginManager is already set up")
        manifests = self._validate_manifests()
        self._ordered = self._dependency_order(manifests)
        builders: dict[str, PluginBuilder] = {}
        for plugin in self._ordered:
            builder = PluginBuilder(plugin.manifest.id)
            plugin.setup(builder)
            builders[plugin.manifest.id] = builder
        self._validate_contributions(builders)

        for plugin in self._ordered:
            builder = builders[plugin.manifest.id]
            for dialog in builder.dialogs:
                self.dialog_registry.register(dialog)
            for router in builder.routers:
                self.dispatcher.include_router(router)
            for key, value in builder.providers.items():
                self.dispatcher.workflow_data[key.name] = value

        command_manifest = self._command_manifest(builders)
        providers = {key: value for builder in builders.values() for key, value in builder.providers.items()}
        self._builders = builders
        self._context = PluginContext(
            bot=self.bot,
            dispatcher=self.dispatcher,
            dialog_registry=self.dialog_registry,
            command_manifest=command_manifest,
            providers=providers,
            capabilities=self.capabilities,
        )
        self.dispatcher.startup.register(self.startup)
        self.dispatcher.shutdown.register(self.shutdown)
        self._configured = True
        return self._context

    async def startup(self) -> None:
        """Start in dependency order and roll back completed plugins on failure."""

        context = self.context
        if self._started:
            return
        try:
            for plugin in self._ordered:
                await plugin.startup(context)
                self._started.append(plugin)
            for plugin in self._ordered:
                for check in self._builders[plugin.manifest.id].health_checks:
                    result = check(context)
                    if inspect.isawaitable(result):
                        await result
        except BaseException as primary:
            cleanup = await self._shutdown_started(context)
            if cleanup:
                raise BaseExceptionGroup(
                    "Plugin startup failed and rollback was incomplete",
                    [primary, *cleanup],
                ) from None
            raise

    async def shutdown(self) -> None:
        """Stop completed plugins in reverse dependency order."""

        if self._context is None:
            return
        errors = await self._shutdown_started(self._context)
        if errors:
            raise PluginLifecycleError(
                "Plugin shutdown failed: " + "; ".join(str(error) for error in errors)
            ) from BaseExceptionGroup("Plugin shutdown failures", errors)

    async def _shutdown_started(self, context: PluginContext) -> list[BaseException]:
        errors: list[BaseException] = []
        while self._started:
            plugin = self._started.pop()
            try:
                await plugin.shutdown(context)
            except BaseException as error:
                errors.append(error)
        return errors

    def _validate_manifests(self) -> Mapping[str, MattergramPlugin]:
        manifests: dict[str, MattergramPlugin] = {}
        for plugin in self.plugins:
            manifest = getattr(plugin, "manifest", None)
            if not isinstance(manifest, PluginManifest):
                raise PluginValidationError("Every plugin must expose a PluginManifest")
            for hook in ("setup", "startup", "shutdown"):
                if not callable(getattr(plugin, hook, None)):
                    raise PluginValidationError(f"Plugin {manifest.id!r} must define a callable {hook} hook")
            if manifest.id in manifests:
                raise PluginValidationError(f"Plugin id {manifest.id!r} is registered twice")
            missing = set(manifest.capabilities).difference(self.capabilities)
            if missing:
                names = ", ".join(sorted(missing))
                raise MissingPluginCapabilityError(
                    f"Plugin {manifest.id!r} requires unavailable capabilities: {names}"
                )
            manifests[manifest.id] = plugin
        return manifests

    def _dependency_order(self, manifests: Mapping[str, MattergramPlugin]) -> tuple[MattergramPlugin, ...]:
        for plugin in manifests.values():
            missing = set(plugin.manifest.requires).difference(manifests)
            if missing:
                names = ", ".join(sorted(missing))
                raise PluginValidationError(
                    f"Plugin {plugin.manifest.id!r} requires missing plugins: {names}"
                )
        remaining = dict(manifests)
        ordered: list[MattergramPlugin] = []
        resolved: set[str] = set()
        while remaining:
            ready = [
                plugin for plugin in remaining.values() if set(plugin.manifest.requires).issubset(resolved)
            ]
            if not ready:
                names = ", ".join(remaining)
                raise PluginValidationError(f"Plugin dependency cycle detected among: {names}")
            for plugin in ready:
                ordered.append(plugin)
                resolved.add(plugin.manifest.id)
                remaining.pop(plugin.manifest.id)
        return tuple(ordered)

    def _validate_contributions(self, builders: Mapping[str, PluginBuilder]) -> None:
        router_owners: dict[int, str] = {}
        state_owners = {state.state or "": "application" for state in self.dialog_registry.states()}
        command_owners = {spec.trigger: "application" for spec in collect_commands(self.dispatcher).commands}
        provider_owners = {name: "application" for name in self.dispatcher.workflow_data}
        for plugin in self._ordered:
            owner = plugin.manifest.id
            builder = builders[owner]
            for router in builder.routers:
                if router.parent_router is not None:
                    raise PluginValidationError(
                        f"Router from plugin {owner!r} is already attached to another router"
                    )
                previous_router_owner = router_owners.setdefault(id(router), owner)
                if previous_router_owner != owner:
                    raise PluginValidationError(
                        f"One router is contributed by plugins {previous_router_owner!r} and {owner!r}"
                    )
            for dialog in builder.dialogs:
                for state in dialog.states:
                    name = state.state or ""
                    previous_state_owner = state_owners.get(name)
                    if previous_state_owner is not None:
                        raise PluginValidationError(
                            f"Dialog state {name!r} conflicts between {previous_state_owner!r} and {owner!r}"
                        )
                    state_owners[name] = owner
            router_commands = [
                spec for router in builder.routers for spec in collect_commands(router).commands
            ]
            for spec in (*builder.commands, *router_commands):
                previous_command_owner = command_owners.get(spec.trigger)
                if previous_command_owner is not None:
                    raise PluginValidationError(
                        f"Command {spec.trigger!r} conflicts between {previous_command_owner!r} and {owner!r}"
                    )
                command_owners[spec.trigger] = owner
            for key in builder.providers:
                previous_provider_owner = provider_owners.get(key.name)
                if previous_provider_owner is not None:
                    raise PluginValidationError(
                        f"Provider {key.name!r} conflicts between {previous_provider_owner!r} and {owner!r}"
                    )
                provider_owners[key.name] = owner

    def _command_manifest(self, builders: Mapping[str, PluginBuilder]) -> CommandManifest:
        commands: dict[str, CommandSpec] = {
            spec.trigger: spec for spec in collect_commands(self.dispatcher).commands
        }
        for builder in builders.values():
            for spec in builder.commands:
                commands[spec.trigger] = spec
        return CommandManifest(tuple(commands[key] for key in sorted(commands)))


__all__ = ["PluginManager"]
