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

    def setup(self, builder: PluginBuilder) -> None:
        builder.add_health_check(self._health_check)

    async def startup(self, context: PluginContext) -> None:
        bot = context.bot
        channels = [await bot.get_channel(channel_id) for channel_id in self.targets]
        invalid = [channel.id for channel in channels if channel.type != "D"]
        if invalid:
            raise ValueError(
                "Composer keyboards are restricted to configured direct channels: " + ", ".join(invalid)
            )

        previous = {channel.id: await bot.get_composer_keyboard(channel.id) for channel in channels}
        self._previous = {
            channel_id: state.keyboard if state is not None else None
            for channel_id, state in previous.items()
        }
        try:
            for channel in channels:
                await bot.set_composer_keyboard(channel.id, self.keyboard)
                self._installed.append(channel.id)
        except BaseException as primary:
            cleanup = await self._restore(context)
            if cleanup:
                raise BaseExceptionGroup(
                    "Composer keyboard startup failed and rollback was incomplete",
                    [primary, *cleanup],
                ) from None
            raise

    async def shutdown(self, context: PluginContext) -> None:
        errors = await self._restore(context)
        if errors:
            raise BaseExceptionGroup("Composer keyboard restoration failed", errors)

    async def _health_check(self, context: PluginContext) -> None:
        for channel_id in self.targets:
            state = await context.bot.get_composer_keyboard(channel_id)
            if state is None or state.keyboard != self.keyboard:
                raise RuntimeError(f"Composer keyboard was not confirmed for direct channel {channel_id!r}")

    async def _restore(self, context: PluginContext) -> list[BaseException]:
        errors: list[BaseException] = []
        while self._installed:
            channel_id = self._installed.pop()
            try:
                keyboard = self._previous[channel_id]
                if keyboard is None:
                    await context.bot.delete_composer_keyboard(channel_id)
                else:
                    await context.bot.set_composer_keyboard(channel_id, keyboard)
            except BaseException as error:
                errors.append(error)
        self._previous.clear()
        return errors


__all__ = ["ComposerKeyboardPlugin", "MENU_CAPABILITY"]
