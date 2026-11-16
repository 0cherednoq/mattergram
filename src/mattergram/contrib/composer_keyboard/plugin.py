"""Application lifecycle adapter for the Mattergram Menu composer keyboard."""

from __future__ import annotations

from mattergram.plugins import PluginBuilder, PluginContext, PluginManifest
from mattergram.ui.composer import ComposerKeyboard

MENU_CAPABILITY = "com.mattergram.menu"


class ComposerKeyboardPlugin:
    """Install one complete keyboard in explicitly configured direct channels.

    The plugin owns only the UI lifecycle. It does not register message handlers,
    interpret button IDs or route the text posted by a user.
    """

    manifest = PluginManifest(
        id="mattergram.composer_keyboard",
        version="1.0.0",
        capabilities=(MENU_CAPABILITY,),
    )

    def __init__(self, *, targets: tuple[str, ...], keyboard: ComposerKeyboard) -> None:
        if not isinstance(targets, tuple) or not targets:
            raise ValueError("Composer keyboard targets must be a nonempty tuple")
        if not all(isinstance(target, str) and target for target in targets):
            raise TypeError("Composer keyboard target IDs must be nonempty strings")
        if len(targets) != len(set(targets)):
            raise ValueError("Composer keyboard targets must be unique")
        if not isinstance(keyboard, ComposerKeyboard):
            raise TypeError("ComposerKeyboardPlugin requires a ComposerKeyboard")
        # Revalidate a detached snapshot at the plugin boundary. In particular,
        # duplicate IDs are rejected before any plugin HTTP request is attempted.
        self.keyboard = ComposerKeyboard.model_validate(keyboard.model_dump(mode="python"))
        self.targets = targets
        self._previous: dict[str, ComposerKeyboard | None] = {}
        self._installed: list[str] = []
        self._desired: dict[str, ComposerKeyboard] = {}
        self._context: PluginContext | None = None
        self._owner_id: str | None = None

    def setup(self, builder: PluginBuilder) -> None:
        builder.add_health_check(self._health_check)

    async def startup(self, context: PluginContext) -> None:
        self._context = context
        try:
            self._owner_id = context.bot.user_id or (await context.bot.get_me()).id
            for channel_id in self.targets:
                await self.install(channel_id)
        except BaseException as primary:
            cleanup = await self._restore(context)
            self._context = None
            self._owner_id = None
            if cleanup:
                raise BaseExceptionGroup(
                    "Composer keyboard startup failed and rollback was incomplete",
                    [primary, *cleanup],
                ) from None
            raise

    async def shutdown(self, context: PluginContext) -> None:
        errors = await self._restore(context)
        self._context = None
        self._owner_id = None
        if errors:
            raise BaseExceptionGroup("Composer keyboard restoration failed", errors)

    async def install(
        self,
        channel_id: str,
        *,
        keyboard: ComposerKeyboard | None = None,
    ) -> None:
        """Install or replace the owned keyboard in one direct channel."""

        context = self._require_started()
        if not isinstance(channel_id, str) or not channel_id:
            raise TypeError("Composer keyboard channel ID must be a nonempty string")
        selected = self.keyboard if keyboard is None else keyboard
        if not isinstance(selected, ComposerKeyboard):
            raise TypeError("Composer keyboard must be a ComposerKeyboard declaration")
        desired = ComposerKeyboard.model_validate(selected.model_dump(mode="python"))
        channel = await context.bot.get_channel(channel_id)
        if channel.type != "D":
            raise ValueError("Composer keyboards are restricted to configured direct channels: " + channel.id)
        if channel_id not in self._previous:
            previous = await context.bot.get_composer_keyboard(channel_id)
            self._previous[channel_id] = (
                previous.keyboard if previous is not None and previous.owner_id != self._owner_id else None
            )
        current = await context.bot.get_composer_keyboard(channel_id)
        if current is None or current.keyboard != desired:
            await context.bot.set_composer_keyboard(channel_id, desired)
        if channel_id not in self._installed:
            self._installed.append(channel_id)
        self._desired[channel_id] = desired

    async def prune_owned(self, active_channel_ids: set[str]) -> None:
        """Delete this bot's persisted keyboards outside the current access set."""

        context = self._require_started()
        if not isinstance(active_channel_ids, set) or any(
            not isinstance(channel_id, str) or not channel_id for channel_id in active_channel_ids
        ):
            raise TypeError("Active composer keyboard channels must be a set of nonempty strings")
        keep = active_channel_ids | set(self.targets)
        for state in await context.bot.list_composer_keyboards():
            if state.owner_id == self._owner_id and state.channel_id not in keep:
                await context.bot.delete_composer_keyboard(state.channel_id)

    async def uninstall(self, channel_id: str) -> None:
        """Restore the state that existed before this plugin first owned the channel."""

        context = self._require_started()
        if channel_id not in self._installed:
            return
        await self._restore_channel(context, channel_id)

    def _require_started(self) -> PluginContext:
        if self._context is None:
            raise RuntimeError("ComposerKeyboardPlugin has not started")
        return self._context

    async def _health_check(self, context: PluginContext) -> None:
        for channel_id, expected in self._desired.items():
            state = await context.bot.get_composer_keyboard(channel_id)
            if state is None or state.keyboard != expected:
                raise RuntimeError(f"Composer keyboard was not confirmed for direct channel {channel_id!r}")

    async def _restore_channel(self, context: PluginContext, channel_id: str) -> None:
        keyboard = self._previous[channel_id]
        if keyboard is None:
            await context.bot.delete_composer_keyboard(channel_id)
        else:
            await context.bot.set_composer_keyboard(channel_id, keyboard)
        self._installed.remove(channel_id)
        self._previous.pop(channel_id, None)
        self._desired.pop(channel_id, None)

    async def _restore(self, context: PluginContext) -> list[BaseException]:
        errors: list[BaseException] = []
        while self._installed:
            channel_id = self._installed[-1]
            try:
                await self._restore_channel(context, channel_id)
            except BaseException as error:
                errors.append(error)
                self._installed.pop()
                self._previous.pop(channel_id, None)
                self._desired.pop(channel_id, None)
        return errors


__all__ = ["ComposerKeyboardPlugin", "MENU_CAPABILITY"]
