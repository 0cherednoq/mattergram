from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from mattergram import Bot, Dispatcher, Router
from mattergram.fsm.state import State
from mattergram.plugins import (
    MissingPluginCapabilityError,
    PluginBuilder,
    PluginContext,
    PluginManager,
    PluginManifest,
    PluginValidationError,
    ProviderKey,
)
from mattergram.provisioning import CommandSpec
from mattergram_dialog import Const, Dialog, DialogRegistry, Window


@dataclass
class RecordingPlugin:
    manifest: PluginManifest
    events: list[str]
    declare: Callable[[PluginBuilder], None] = lambda builder: None
    fail_startup: bool = False
    setup_calls: int = field(default=0, init=False)

    def setup(self, builder: PluginBuilder) -> None:
        self.setup_calls += 1
        self.events.append("setup:" + self.manifest.id)
        self.declare(builder)

    async def startup(self, context: PluginContext) -> None:
        del context
        self.events.append("start:" + self.manifest.id)
        if self.fail_startup:
            raise RuntimeError("startup failed")

    async def shutdown(self, context: PluginContext) -> None:
        del context
        self.events.append("stop:" + self.manifest.id)


def manager(*plugins: RecordingPlugin, capabilities: tuple[str, ...] = ()) -> PluginManager:
    return PluginManager(
        bot=Bot(token="test-token", base_url="https://mattermost.test"),
        dispatcher=Dispatcher(),
        plugins=plugins,
        capabilities=capabilities,
    )


def test_manifest_validation_and_unique_plugin_ids() -> None:
    with pytest.raises(ValueError, match="Plugin id"):
        PluginManifest("Not Valid", "1.0.0")
    with pytest.raises(ValueError, match="version"):
        PluginManifest("valid", "latest")
    with pytest.raises(ValueError, match="depend on itself"):
        PluginManifest("valid", "1.0.0", requires=("valid",))

    events: list[str] = []
    duplicate = RecordingPlugin(PluginManifest("same", "1.0.0"), events)
    with pytest.raises(PluginValidationError, match="registered twice"):
        manager(duplicate, duplicate).setup()
    assert duplicate.setup_calls == 0


@pytest.mark.asyncio
async def test_dependency_order_and_reverse_shutdown() -> None:
    events: list[str] = []
    dependent = RecordingPlugin(PluginManifest("dependent", "1.0.0", requires=("base",)), events)
    base = RecordingPlugin(PluginManifest("base", "1.0.0"), events)
    plugins = manager(dependent, base)

    plugins.setup()
    await plugins.startup()
    await plugins.shutdown()

    assert plugins.ordered_plugin_ids == ("base", "dependent")
    assert events == [
        "setup:base",
        "setup:dependent",
        "start:base",
        "start:dependent",
        "stop:dependent",
        "stop:base",
    ]


def test_missing_dependency_cycle_and_capability_fail_before_setup() -> None:
    events: list[str] = []
    missing = RecordingPlugin(PluginManifest("child", "1.0.0", requires=("absent",)), events)
    with pytest.raises(PluginValidationError, match="missing plugins"):
        manager(missing).setup()
    assert missing.setup_calls == 0

    left = RecordingPlugin(PluginManifest("left", "1.0.0", requires=("right",)), events)
    right = RecordingPlugin(PluginManifest("right", "1.0.0", requires=("left",)), events)
    with pytest.raises(PluginValidationError, match="cycle"):
        manager(left, right).setup()
    assert left.setup_calls == right.setup_calls == 0

    capable = RecordingPlugin(PluginManifest("capable", "1.0.0", capabilities=("plugin.example",)), events)
    with pytest.raises(MissingPluginCapabilityError, match="plugin.example"):
        manager(capable).setup()
    assert capable.setup_calls == 0


@pytest.mark.asyncio
async def test_partial_startup_rolls_back_only_completed_plugins() -> None:
    events: list[str] = []
    first = RecordingPlugin(PluginManifest("first", "1.0.0"), events)
    broken = RecordingPlugin(PluginManifest("broken", "1.0.0"), events, fail_startup=True)
    plugins = manager(first, broken)
    plugins.setup()

    with pytest.raises(RuntimeError, match="startup failed"):
        await plugins.startup()

    assert events[-3:] == ["start:first", "start:broken", "stop:first"]
    await plugins.shutdown()
    assert events.count("stop:first") == 1


def test_conflicts_are_rejected_before_dispatcher_mutation() -> None:
    shared_router = Router(name="shared")
    provider = ProviderKey[object]("shared_service")
    events: list[str] = []

    def first(builder: PluginBuilder) -> None:
        builder.include_router(shared_router)
        builder.provide(provider, object())
        builder.add_commands(CommandSpec("same-command"))

    def second(builder: PluginBuilder) -> None:
        builder.include_router(shared_router)
        builder.provide(provider, object())
        builder.add_commands(CommandSpec("same-command"))

    left = RecordingPlugin(PluginManifest("left", "1.0.0"), events, first)
    right = RecordingPlugin(PluginManifest("right", "1.0.0"), events, second)
    plugins = manager(left, right)

    with pytest.raises(PluginValidationError):
        plugins.setup()

    assert shared_router.parent_router is None
    assert "shared_service" not in plugins.dispatcher.workflow_data
    assert plugins.dispatcher.sub_routers == []


def test_dialog_state_and_existing_provider_conflicts() -> None:
    state = State("shared")
    dialog_a = Dialog(Window(Const("A"), state=state))
    dialog_b = Dialog(Window(Const("B"), state=state))
    events: list[str] = []
    left = RecordingPlugin(
        PluginManifest("left", "1.0.0"), events, lambda builder: builder.include_dialog(dialog_a)
    )
    right = RecordingPlugin(
        PluginManifest("right", "1.0.0"), events, lambda builder: builder.include_dialog(dialog_b)
    )
    plugins = manager(left, right)
    with pytest.raises(PluginValidationError, match="Dialog state"):
        plugins.setup()
    assert plugins.dialog_registry.states() == ()

    existing = Dispatcher(shared_service=object())
    provider_plugin = RecordingPlugin(
        PluginManifest("provider", "1.0.0"),
        events,
        lambda builder: builder.provide(ProviderKey[Any]("shared_service"), object()),
    )
    plugins = PluginManager(
        bot=Bot(token="test-token", base_url="https://mattermost.test"),
        dispatcher=existing,
        plugins=(provider_plugin,),
        dialog_registry=DialogRegistry(),
    )
    with pytest.raises(PluginValidationError, match="Provider"):
        plugins.setup()


def test_builder_has_no_platform_keyboard_contract_and_exports_commands_and_di() -> None:
    service = object()
    service_key = ProviderKey[object]("sample_service")
    events: list[str] = []

    def declare(builder: PluginBuilder) -> None:
        builder.provide(service_key, service)
        builder.add_commands(CommandSpec("sample"))

    plugin = RecordingPlugin(PluginManifest("sample", "1.0.0"), events, declare)
    plugins = manager(plugin)
    context = plugins.setup()

    assert not hasattr(PluginBuilder, "set_composer_keyboard")
    assert not hasattr(PluginBuilder, "add_keyboard_button")
    assert context.provider(service_key) is service
    assert plugins.dispatcher.workflow_data["sample_service"] is service
    assert [command.trigger for command in context.command_manifest.commands] == ["sample"]
