"""Bridge app-user access changes to bot DM and composer-keyboard lifecycle."""

from __future__ import annotations

from collections.abc import Mapping

from mattergram.contrib.admin_users import AdminUser, AdminUserChange, AdminUserRepository
from mattergram.contrib.composer_keyboard import ComposerKeyboardPlugin
from mattergram.plugins import PluginBuilder, PluginContext, PluginManifest
from mattergram.ui.composer import ComposerKeyboard


class AdminUserOnboardingPlugin:
    """Reconcile enabled app users with discoverable bot direct messages.

    This is an explicit bridge plugin. ``AdminUsersPlugin`` remains independent
    from keyboard declarations and continues to route ordinary message text.
    """

    manifest = PluginManifest(
        id="mattergram.admin_user_onboarding",
        version="1.0.0",
        requires=("mattergram.admin_users", "mattergram.composer_keyboard"),
    )

    def __init__(
        self,
        *,
        repository: AdminUserRepository,
        keyboard: ComposerKeyboardPlugin,
        role_keyboards: Mapping[str, ComposerKeyboard] | None = None,
        welcome_message: str = (
            "Доступ к боту активирован. Используйте меню над полем ввода или отправьте «Пользователи»."
        ),
    ) -> None:
        if not isinstance(keyboard, ComposerKeyboardPlugin):
            raise TypeError("Admin user onboarding requires ComposerKeyboardPlugin")
        if not isinstance(welcome_message, str) or not welcome_message.strip():
            raise ValueError("Admin user onboarding welcome message must be nonempty")
        configured = dict(role_keyboards or {})
        if any(not isinstance(role, str) or not role for role in configured):
            raise TypeError("Role keyboard IDs must be nonempty strings")
        if any(not isinstance(value, ComposerKeyboard) for value in configured.values()):
            raise TypeError("Role keyboards must be ComposerKeyboard declarations")
        self.repository = repository
        self.keyboard = keyboard
        self.role_keyboards = {
            role: ComposerKeyboard.model_validate(value.model_dump(mode="python"))
            for role, value in configured.items()
        }
        self.welcome_message = welcome_message
        self._context: PluginContext | None = None
        self._channels: dict[str, str] = {}
        self._pending: dict[str, AdminUser] = {}

    def setup(self, builder: PluginBuilder) -> None:
        del builder

    async def startup(self, context: PluginContext) -> None:
        self._context = context
        try:
            users = {user.mattermost_user_id: user for user in await self.repository.list_users()}
            users.update(self._pending)
            self._pending.clear()
            for user in users.values():
                if user.enabled:
                    await self._sync_enabled(user)
            await self.keyboard.prune_owned(set(self._channels.values()))
        except BaseException:
            self._context = None
            self._channels.clear()
            raise

    async def shutdown(self, context: PluginContext) -> None:
        del context
        self._context = None
        self._channels.clear()

    async def write(self, change: AdminUserChange) -> None:
        """Consume the generic post-commit event emitted by AdminUsersService."""

        if self._context is None:
            self._pending[change.current.mattermost_user_id] = change.current
            return
        if change.current.enabled:
            await self._sync_enabled(change.current)
            return
        channel_id = self._channels.pop(change.current.mattermost_user_id, None)
        if channel_id is not None:
            await self.keyboard.uninstall(channel_id)

    async def _sync_enabled(self, user: AdminUser) -> None:
        if self._context is None:
            raise RuntimeError("AdminUserOnboardingPlugin has not started")
        channel = await self._context.bot.create_direct_channel(user.mattermost_user_id)
        previous = await self._context.bot.get_composer_keyboard(channel.id)
        desired = self.role_keyboards.get(user.role, self.keyboard.keyboard)
        await self.keyboard.install(channel.id, keyboard=desired)
        self._channels[user.mattermost_user_id] = channel.id
        if previous is None:
            await self._context.bot.send_message(channel.id, self.welcome_message)


__all__ = ["AdminUserOnboardingPlugin"]
