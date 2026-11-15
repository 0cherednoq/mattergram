from __future__ import annotations

from typing import Any

import pytest

from mattergram import ComposerButton, ComposerKeyboard, Dispatcher
from mattergram.contrib.composer_keyboard import MENU_CAPABILITY, ComposerKeyboardPlugin
from mattergram.plugins import MissingPluginCapabilityError, PluginManager
from mattergram.types import Channel
from mattergram.ui.composer import ComposerKeyboardState


class KeyboardBot:
    def __init__(self, channel_types: dict[str, str]) -> None:
        self.channel_types = channel_types
        self.states: dict[str, ComposerKeyboardState] = {}
        self.writes: list[tuple[str, str]] = []
        self.revision = 0

    async def get_channel(self, channel_id: str) -> Channel:
        return Channel.from_wire({"id": channel_id, "type": self.channel_types[channel_id]})

    async def get_composer_keyboard(self, channel_id: str) -> ComposerKeyboardState | None:
        return self.states.get(channel_id)

    async def set_composer_keyboard(
        self, channel_id: str, keyboard: ComposerKeyboard
    ) -> ComposerKeyboardState:
        self.revision += 1
        state = ComposerKeyboardState(
            channel_id=channel_id,
            revision=self.revision,
            keyboard=keyboard,
        )
        self.states[channel_id] = state
        self.writes.append(("set", channel_id))
        return state

    async def delete_composer_keyboard(self, channel_id: str) -> None:
        self.states.pop(channel_id, None)
        self.writes.append(("delete", channel_id))


def keyboard() -> ComposerKeyboard:
    return ComposerKeyboard(
        title="Administration",
        rows=((ComposerButton(id="users", text="Users", message="Users"),),),
    )


def plugin_manager(bot: Any, plugin: ComposerKeyboardPlugin, *, capable: bool = True) -> PluginManager:
    return PluginManager(
        bot=bot,
        dispatcher=Dispatcher(),
        plugins=(plugin,),
        capabilities=(MENU_CAPABILITY,) if capable else (),
    )


@pytest.mark.asyncio
async def test_keyboard_plugin_installs_without_feature_handlers_and_restores_on_shutdown() -> None:
    bot = KeyboardBot({"direct123": "D"})
    plugin = ComposerKeyboardPlugin(targets=("direct123",), keyboard=keyboard())
    plugins = plugin_manager(bot, plugin)
    context = plugins.setup()

    assert plugins.dispatcher.sub_routers == []
    assert context.command_manifest.commands == ()
    await plugins.startup()
    assert bot.states["direct123"].keyboard == keyboard()
    assert bot.writes == [("set", "direct123")]

    await plugins.shutdown()
    assert "direct123" not in bot.states
    assert bot.writes == [("set", "direct123"), ("delete", "direct123")]


@pytest.mark.asyncio
async def test_keyboard_plugin_rejects_non_direct_target_before_write() -> None:
    bot = KeyboardBot({"public123": "O"})
    plugins = plugin_manager(
        bot,
        ComposerKeyboardPlugin(targets=("public123",), keyboard=keyboard()),
    )
    plugins.setup()

    with pytest.raises(ValueError, match="direct channels"):
        await plugins.startup()
    assert bot.writes == []


def test_keyboard_plugin_requires_explicit_platform_capability() -> None:
    bot = KeyboardBot({"direct123": "D"})
    plugins = plugin_manager(
        bot,
        ComposerKeyboardPlugin(targets=("direct123",), keyboard=keyboard()),
        capable=False,
    )
    with pytest.raises(MissingPluginCapabilityError, match=MENU_CAPABILITY):
        plugins.setup()
    assert bot.writes == []
