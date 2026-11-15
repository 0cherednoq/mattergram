from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from mattergram.bot import Bot
from mattergram.dispatcher.router import Router
from mattergram.exceptions import APIError
from mattergram.fsm.state import State
from mattergram.http.auth import CallbackSigner
from mattergram.types import Message
from mattergram.types.dialogs import DialogCancel, DialogSubmission
from mattergram.types.http import CallbackQuery, SlashCommand
from mattergram.ui.actions import ActionContext
from mattergram.ui.capabilities import UIProfile
from mattergram_dialog.api.entities import Data, ShowMode, StartMode
from mattergram_dialog.api.exceptions import OutdatedIntent
from mattergram_dialog.bindings import DialogActions
from mattergram_dialog.context.storage import DialogStorage
from mattergram_dialog.navigation import DialogManager
from mattergram_dialog.registry import DialogRegistry
from mattergram_dialog.rendering import MattermostRenderer
from mattergram_dialog.widgets.common.action import WidgetAction, WidgetActionResult
from mattergram_dialog.window import FormWindow

EntryEvent = Message | SlashCommand | CallbackQuery | DialogSubmission | DialogCancel
_BLOCKS_ACTION_SETTLE_DELAY = 0.1
_UPDATE_STATE_KEY = "__mattergram_dialog_updates_v1__"
_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DialogRef:
    """Durable coordinates for rebuilding one actor-scoped dialog post."""

    channel_id: str
    actor_id: str
    post_id: str
    intent_id: str
    thread_id: str | None = None
    team_id: str | None = None

    def __post_init__(self) -> None:
        if not self.channel_id or not self.actor_id or not self.post_id or not self.intent_id:
            raise ValueError("DialogRef channel, actor, post and intent IDs must be nonempty")


class DialogRuntime:
    """Bind declarative dialogs to trusted Mattermost slash/callback/form events."""

    def __init__(
        self,
        bot: Bot,
        storage: DialogStorage,
        registry: DialogRegistry,
        *,
        instance_id: str,
        bot_id: str,
        callback_url: str,
        callback_signer: CallbackSigner,
        ui_profile: UIProfile | None = None,
        action_ttl: float = 900,
    ) -> None:
        if not instance_id or not bot_id:
            raise ValueError("Dialog runtime instance_id and bot_id must be nonempty")
        if (
            isinstance(action_ttl, bool)
            or not isinstance(action_ttl, (float, int))
            or not math.isfinite(action_ttl)
            or not 0 < action_ttl <= callback_signer.max_ttl
        ):
            raise ValueError("Dialog action_ttl must be positive and no greater than signer max_ttl")
        self.bot = bot
        self.storage = storage
        self.registry = registry
        self.instance_id = instance_id
        self.bot_id = bot_id
        self.callback_url = callback_url
        self.callback_signer = callback_signer
        self.ui_profile = ui_profile
        self.action_ttl = float(action_ttl)
        self._post_update_tasks: set[asyncio.Task[None]] = set()
        self._post_update_errors: list[BaseException] = []
        self._form_callback_ids = frozenset(
            window.callback_id
            for state in registry.states()
            for window in (registry.find_dialog(state).window_for(state),)
            if isinstance(window, FormWindow)
        )

    def _schedule_blocks_update(
        self,
        *,
        channel_id: str,
        post_id: str,
        text: str,
        props: Mapping[str, Any],
    ) -> None:
        async def edit_after_action_response() -> None:
            # Mattermost may finish persisting the action after the integration
            # response. Editing before that point can be overwritten by the
            # server's stale copy of the post and its action registry.
            await asyncio.sleep(_BLOCKS_ACTION_SETTLE_DELAY)
            await self.bot.edit_message_text(channel_id, post_id, text, props=props)

        task = asyncio.create_task(edit_after_action_response())
        self._post_update_tasks.add(task)

        def finished(done: asyncio.Task[None]) -> None:
            self._post_update_tasks.discard(done)
            if done.cancelled():
                return
            error = done.exception()
            if error is not None:
                self._post_update_errors.append(error)
                _logger.error(
                    "Deferred Mattermost Blocks post update failed",
                    exc_info=(type(error), error, error.__traceback__),
                )

        task.add_done_callback(finished)

    async def drain(self) -> None:
        """Wait for deferred Blocks post edits and surface delivery failures."""

        while self._post_update_tasks:
            await asyncio.gather(*tuple(self._post_update_tasks), return_exceptions=True)
        if self._post_update_errors:
            error = self._post_update_errors.pop(0)
            self._post_update_errors.clear()
            raise error

    def _manager(self, event: EntryEvent) -> DialogManager:
        thread_id = event.root_id if isinstance(event, Message) else getattr(event, "thread_id", None)
        team_id = (
            (event.chat.team_id or None) if isinstance(event, Message) else getattr(event, "team_id", None)
        )
        key = self.storage.key(
            instance_id=self.instance_id,
            bot_id=self.bot_id,
            channel_id=event.channel_id,
            actor_id=event.user_id,
            thread_id=thread_id,
        )
        open_form = event.next if isinstance(event, DialogSubmission) else getattr(event, "open_dialog", None)
        post_id = event.post_id if isinstance(event, CallbackQuery) else None
        actions = DialogActions(
            self.callback_url,
            ActionContext(
                signer=self.callback_signer,
                channel_id=event.channel_id,
                post_id=post_id,
                user_id=event.user_id,
                team_id=team_id,
                thread_id=thread_id,
                ttl=self.action_ttl,
            ),
            ui_profile=self.ui_profile,
        )

        async def update_post(text: str, props: Mapping[str, Any] | None) -> None:
            if not isinstance(event, CallbackQuery):
                raise RuntimeError("Only a post action can update its source post response")
            if props is None:
                raise TypeError("Dialog post props must be a mapping")
            use_blocks = (
                self.ui_profile is not None and self.ui_profile.capability("blocks").status == "supported"
            )
            if use_blocks:
                await event.answer()
                self._schedule_blocks_update(
                    channel_id=event.channel_id,
                    post_id=event.post_id,
                    text=text,
                    props=props,
                )
            else:
                await event.update_message(text, props=props)

        renderer = MattermostRenderer(
            self.bot,
            event.channel_id,
            root_id=thread_id,
            open_form=open_form,
            actions_factory=actions,
            update_post=update_post if isinstance(event, CallbackQuery) else None,
            ui_profile=self.ui_profile,
        )
        return DialogManager(
            self.storage,
            key,
            self.registry,
            renderer,
            actor_id=event.user_id,
            event=event,
        )

    async def start(
        self,
        event: SlashCommand | CallbackQuery,
        state: State,
        data: Data = None,
        *,
        mode: StartMode = StartMode.NORMAL,
    ) -> DialogRef | None:
        async with self._manager(event) as manager:
            await manager.start(state, data, mode=mode)
            post_id = manager.stack.last_post_id
            if post_id is None:
                return None
            return DialogRef(
                channel_id=event.channel_id,
                actor_id=event.user_id,
                post_id=post_id,
                intent_id=manager.current_context().intent_id,
                thread_id=getattr(event, "thread_id", None),
                team_id=getattr(event, "team_id", None),
            )

    async def navigate(self, event: Message, state: State) -> DialogRef:
        """Open or switch an actor's dialog from a posted keyboard message.

        Persistent composer keyboards publish ordinary Mattermost messages and
        therefore do not carry an interactive-message callback.  This entry
        point reuses the actor/channel/thread dialog session and edits its
        existing post.  If the actor has not opened the dialog yet, the target
        state becomes the first post in a fresh session.
        """

        async with self._manager(event) as manager:
            if manager.stack.intents:
                recovery_data = dict(manager.current_context().dialog_data)
                try:
                    await manager.switch_to(state, show_mode=ShowMode.EDIT)
                except APIError as error:
                    if error.status != 404:
                        raise
                    await manager.start(state, recovery_data, mode=StartMode.RESET_STACK)
            else:
                await manager.start(state, mode=StartMode.RESET_STACK)
            post_id = manager.stack.last_post_id
            if post_id is None:
                raise RuntimeError("A keyboard navigation target must render a post window")
            return DialogRef(
                channel_id=event.channel_id,
                actor_id=event.user_id,
                post_id=post_id,
                intent_id=manager.current_context().intent_id,
                thread_id=event.root_id,
                team_id=event.chat.team_id,
            )

    async def respond(self, event: Message, state: State, data: Data = None) -> DialogRef:
        """Render a fresh dialog post for an ordinary keyboard message.

        Reply-keyboard buttons publish visible actor messages.  Each accepted
        message resets that actor's active dialog stack and creates a new bot
        post, so command delivery and handler completion remain independently
        observable in the channel history.
        """

        async with self._manager(event) as manager:
            await manager.start(state, data, mode=StartMode.RESET_STACK)
            post_id = manager.stack.last_post_id
            if post_id is None:
                raise RuntimeError("A keyboard response target must render a post window")
            return DialogRef(
                channel_id=event.channel_id,
                actor_id=event.user_id,
                post_id=post_id,
                intent_id=manager.current_context().intent_id,
                thread_id=event.root_id,
                team_id=event.chat.team_id,
            )

    def _manager_from_ref(self, ref: DialogRef) -> DialogManager:
        key = self.storage.key(
            instance_id=self.instance_id,
            bot_id=self.bot_id,
            channel_id=ref.channel_id,
            actor_id=ref.actor_id,
            thread_id=ref.thread_id,
        )
        actions = DialogActions(
            self.callback_url,
            ActionContext(
                signer=self.callback_signer,
                channel_id=ref.channel_id,
                post_id=ref.post_id,
                user_id=ref.actor_id,
                team_id=ref.team_id,
                thread_id=ref.thread_id,
                ttl=self.action_ttl,
            ),
            ui_profile=self.ui_profile,
        )
        renderer = MattermostRenderer(
            self.bot,
            ref.channel_id,
            root_id=ref.thread_id,
            actions_factory=actions,
            ui_profile=self.ui_profile,
        )
        return DialogManager(
            self.storage,
            key,
            self.registry,
            renderer,
            actor_id=ref.actor_id,
        )

    async def update(
        self,
        ref: DialogRef,
        data: Mapping[str, Any] | None = None,
        *,
        revision: int | None = None,
    ) -> bool:
        """Patch dialog data and rebuild the referenced post through REST.

        A nonnegative monotonic ``revision`` makes stale worker snapshots a
        no-op. The revision is persisted inside the actor-scoped dialog
        transaction, so concurrent runtimes share the same ordering guard.
        """

        if revision is not None and (type(revision) is not int or revision < 0):
            raise ValueError("Dialog update revision must be a nonnegative integer or None")

        async with self._manager_from_ref(ref) as manager:
            if (
                manager.current_context().intent_id != ref.intent_id
                or manager.stack.last_post_id != ref.post_id
            ):
                raise OutdatedIntent("DialogRef belongs to an outdated dialog post or intent")
            if revision is not None:
                raw = manager.current_context().widget_data.setdefault(_UPDATE_STATE_KEY, {})
                if not isinstance(raw, dict):
                    raise ValueError("Persisted dialog update state must be a mapping")
                current = raw.get("revision")
                if current is not None and (type(current) is not int or current < 0):
                    raise ValueError("Persisted dialog update revision is invalid")
                if current is not None and revision <= current:
                    return False
                raw["revision"] = revision
            if data is None:
                await manager.show(ShowMode.EDIT)
            else:
                await manager.update(data, show_mode=ShowMode.EDIT)
            return True

    def is_transition(self, event: CallbackQuery) -> bool:
        return DialogActions.matches(event.data)

    def is_submission(self, event: DialogSubmission) -> bool:
        return event.form.callback_id in self._form_callback_ids

    def is_cancel(self, event: DialogCancel) -> bool:
        return event.form.callback_id in self._form_callback_ids

    async def handle_transition(self, event: CallbackQuery) -> None:
        action = DialogActions.decode(event.data)
        async with self._manager(event) as manager:
            context = manager.current_context()
            if (
                context.intent_id != action.intent_id
                or context.state.state != action.source_state
                or manager.stack.last_post_id != event.post_id
            ):
                await event.answer("This dialog action is outdated")
                return
            if action.action == "switch":
                assert action.target_state is not None
                await manager.switch_to(
                    self.registry.resolve_state(action.target_state),
                    show_mode=ShowMode.NO_UPDATE,
                )
            elif action.action == "back":
                await manager.back(show_mode=ShowMode.NO_UPDATE)
            elif action.action == "next":
                await manager.next(show_mode=ShowMode.NO_UPDATE)
            else:
                assert action.widget_id is not None
                widget = manager.find(action.widget_id)
                process_action = getattr(widget, "process_action", None)
                if not callable(process_action):
                    raise ValueError("Dialog action target does not implement the widget action protocol")
                result = await process_action(
                    WidgetAction(action.action, action.value, action.target_state),
                    event,
                    manager,
                )
                if not isinstance(result, WidgetActionResult):
                    raise TypeError("Widget process_action must return WidgetActionResult")
                if not result.accepted:
                    await event.answer("This dialog action is outdated")
                    return
                if result.target_state is not None:
                    await manager.switch_to(
                        self.registry.resolve_state(result.target_state),
                        show_mode=ShowMode.NO_UPDATE,
                    )
            window = manager.current_dialog().window_for(manager.current_context().state)
            await manager.show(ShowMode.FORM if isinstance(window, FormWindow) else ShowMode.EDIT)

    async def handle_submission(self, event: DialogSubmission) -> None:
        async with self._manager(event) as manager:
            context = manager.current_context()
            if context.intent_id != event.application_state:
                raise OutdatedIntent("Form submission belongs to another dialog intent")
            window = manager.current_dialog().window_for(context.state)
            if not isinstance(window, FormWindow) or window.callback_id != event.form.callback_id:
                raise OutdatedIntent("Form submission does not match the current dialog window")
            await manager.submit(event)

    async def handle_cancel(self, event: DialogCancel) -> None:
        async with self._manager(event) as manager:
            context = manager.current_context()
            if context.intent_id != event.application_state:
                raise OutdatedIntent("Form cancellation belongs to another dialog intent")
            window = manager.current_dialog().window_for(context.state)
            if not isinstance(window, FormWindow) or window.callback_id != event.form.callback_id:
                raise OutdatedIntent("Form cancellation does not match the current dialog window")
            if manager.can_back():
                await manager.back(show_mode=ShowMode.NO_UPDATE)
            else:
                await manager.done(show_mode=ShowMode.NO_UPDATE)
            await event.answer()


def setup_dialogs(router: Router, runtime: DialogRuntime) -> None:
    router.callback_query.register(runtime.handle_transition, runtime.is_transition)
    router.dialog_submit.register(runtime.handle_submission, runtime.is_submission)
    router.dialog_cancel.register(runtime.handle_cancel, runtime.is_cancel)


__all__ = ["DialogRef", "DialogRuntime", "EntryEvent", "setup_dialogs"]
