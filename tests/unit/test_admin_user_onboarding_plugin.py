from __future__ import annotations

from typing import Any

import pytest

from mattergram import ComposerButton, ComposerKeyboard, Dispatcher
from mattergram.contrib.admin_user_onboarding import AdminUserOnboardingPlugin
from mattergram.contrib.admin_users import (
    ADMIN_USERS_RUNTIME,
    AdminUser,
    AdminUsersPlugin,
    MemoryAdminUserRepository,
    TeamUser,
)
from mattergram.contrib.composer_keyboard import MENU_CAPABILITY, ComposerKeyboardPlugin
from mattergram.plugins import PluginManager
from mattergram.types import Channel, Message
from mattergram.ui.composer import ComposerKeyboardState
from mattergram_dialog import DialogRegistry


class Directory:
    async def list_users(self) -> tuple[TeamUser, ...]:
        return (
            TeamUser("owner-id", "owner"),
            TeamUser("alice-id", "alice"),
            TeamUser("bob-id", "bob"),
        )


class Runtime:
    async def start(self, event: Any, state: object, data: object = None, **kwargs: Any) -> None:
        del event, state, data, kwargs

    async def respond(self, event: Any, state: object, data: object = None) -> None:
        del event, state, data


class Bot:
    def __init__(self) -> None:
        self.user_id = "b" * 26
        self.channels = {
            "owner-id": Channel.from_wire({"id": "o" * 26, "type": "D"}),
            "alice-id": Channel.from_wire({"id": "a" * 26, "type": "D"}),
            "bob-id": Channel.from_wire({"id": "b" * 26, "type": "D"}),
        }
        self.channel_types = {channel.id: channel.type for channel in self.channels.values()}
        self.states: dict[str, ComposerKeyboardState] = {}
        self.messages: list[tuple[str, str]] = []
        self.revision = 0

    async def create_direct_channel(self, user_id: str) -> Channel:
        return self.channels[user_id]

    async def get_channel(self, channel_id: str) -> Channel:
        return Channel.from_wire({"id": channel_id, "type": self.channel_types[channel_id]})

    async def get_composer_keyboard(self, channel_id: str) -> ComposerKeyboardState | None:
        return self.states.get(channel_id)

    async def set_composer_keyboard(
        self,
        channel_id: str,
        keyboard: ComposerKeyboard,
    ) -> ComposerKeyboardState:
        self.revision += 1
        state = ComposerKeyboardState(
            channel_id=channel_id,
            revision=self.revision,
            owner_id=self.user_id,
            keyboard=keyboard,
        )
        self.states[channel_id] = state
        return state

    async def delete_composer_keyboard(self, channel_id: str) -> None:
        self.states.pop(channel_id, None)

    async def list_composer_keyboards(self) -> tuple[ComposerKeyboardState, ...]:
        return tuple(self.states.values())

    async def send_message(self, channel_id: str, text: str) -> Message:
        self.messages.append((channel_id, text))
        return Message.from_wire(
            {
                "id": f"post-{len(self.messages)}",
                "channel_id": channel_id,
                "user_id": "bot-id",
                "message": text,
                "create_at": 1,
            }
        )


def keyboard(identifier: str, title: str) -> ComposerKeyboard:
    return ComposerKeyboard(
        title=title,
        rows=((ComposerButton(id=identifier, text=title, message=title),),),
    )


@pytest.mark.asyncio
async def test_onboarding_reconciles_existing_and_new_users_without_feature_routing_coupling() -> None:
    repository = MemoryAdminUserRepository(
        users=(
            AdminUser("owner", "owner-id", "owner", "admin"),
            AdminUser("alice", "alice-id", "alice", "operator"),
        )
    )
    bot = Bot()
    full = keyboard("admin", "Admin")
    limited = keyboard("users", "Users")
    owner_dm = "o" * 26
    alice_dm = "a" * 26
    bob_dm = "b" * 26
    bot.states[bob_dm] = ComposerKeyboardState(
        channel_id=bob_dm,
        revision=1,
        owner_id=bot.user_id,
        keyboard=limited,
    )
    composer = ComposerKeyboardPlugin(targets=(owner_dm,), keyboard=full)
    onboarding = AdminUserOnboardingPlugin(
        repository=repository,
        keyboard=composer,
        role_keyboards={"operator": limited},
    )
    users = AdminUsersPlugin(
        repository=repository,
        directory=Directory(),
        initial_admin_ids=("owner-id",),
        change_sink=onboarding,
    )
    dispatcher = Dispatcher()
    plugins = PluginManager(
        bot=bot,  # type: ignore[arg-type]
        dispatcher=dispatcher,
        dialog_registry=DialogRegistry(),
        plugins=(users, composer, onboarding),
        capabilities=(MENU_CAPABILITY,),
    )
    plugins.setup()
    dispatcher.workflow_data[ADMIN_USERS_RUNTIME.name] = Runtime()
    await plugins.startup()

    assert bot.states[owner_dm].keyboard == full
    assert bot.states[alice_dm].keyboard == limited
    assert bob_dm not in bot.states
    assert [channel for channel, _ in bot.messages] == [alice_dm]

    created = await users.service.add_user(
        "owner-id",
        mattermost_user_id="bob-id",
        role="operator",
        enabled=True,
    )
    assert bot.states[bob_dm].keyboard == limited
    assert [channel for channel, _ in bot.messages] == [alice_dm, bob_dm]

    await users.service.update_user(
        "owner-id",
        created.id,
        role="operator",
        enabled=False,
    )
    assert bob_dm not in bot.states
    await plugins.shutdown()
