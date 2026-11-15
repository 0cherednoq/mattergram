from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any

from mattergram.fsm.state import State
from mattergram.fsm.storage.base import StorageKey
from mattergram_dialog.api.entities import Context, Data, LaunchMode, ShowMode, Stack, StartMode
from mattergram_dialog.api.exceptions import NoContextError, OutdatedIntent, UnknownState
from mattergram_dialog.context.access_validator import DefaultStackAccessValidator
from mattergram_dialog.context.storage import DialogSession, DialogStorage
from mattergram_dialog.dialog import Dialog
from mattergram_dialog.registry import DialogRegistry
from mattergram_dialog.rendering import Renderer


class DialogManager:
    """A locked, transactional manager for one actor/channel/thread dialog scope."""

    def __init__(
        self,
        storage: DialogStorage,
        key: StorageKey,
        registry: DialogRegistry,
        renderer: Renderer,
        *,
        actor_id: str,
        event: Any = None,
    ) -> None:
        if not actor_id:
            raise ValueError("actor_id must be nonempty")
        self.storage = storage
        self.key = key
        self.registry = registry
        self.renderer = renderer
        self.actor_id = actor_id
        self.event = event
        self._session: DialogSession | None = None
        self._transaction: AbstractAsyncContextManager[DialogSession] | None = None
        self._access = DefaultStackAccessValidator()

    async def __aenter__(self) -> DialogManager:
        if self._session is not None:
            raise RuntimeError("DialogManager is already active")
        transaction = self.storage.session(self.key)
        self._transaction = transaction
        self._session = await transaction.__aenter__()
        if not self._access.is_allowed(self.actor_id, self._session.stack.access_settings, self.event):
            await transaction.__aexit__(PermissionError, PermissionError("Dialog access denied"), None)
            self._session = None
            self._transaction = None
            raise PermissionError("Dialog access denied")
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool | None:
        transaction = self._transaction
        self._session = None
        self._transaction = None
        if transaction is None:
            return None
        result = await transaction.__aexit__(exc_type, exc, traceback)
        return bool(result) if result is not None else None

    def _active(self) -> DialogSession:
        if self._session is None:
            raise RuntimeError("Use DialogManager as an async context manager")
        return self._session

    def current_context(self) -> Context:
        context = self._active().current()
        if context is None:
            raise NoContextError("There is no active dialog context")
        return context

    @property
    def dialog_data(self) -> dict[str, Any]:
        return self.current_context().dialog_data

    @property
    def start_data(self) -> Data:
        return self.current_context().start_data

    @property
    def stack(self) -> Stack:
        return self._active().stack

    def current_dialog(self) -> Dialog:
        return self.registry.find_dialog(self.current_context().state)

    async def show(self, mode: ShowMode = ShowMode.AUTO) -> None:
        if mode is ShowMode.NO_UPDATE:
            return
        dialog = self.current_dialog()
        await self.renderer.render(await dialog.render(self), self.stack, mode)

    async def start(self, state: State, data: Data = None, *, mode: StartMode = StartMode.NORMAL) -> None:
        session = self._active()
        resolved = self.registry.resolve_state(state)
        dialog = self.registry.find_dialog(resolved)
        if mode in (StartMode.RESET_STACK, StartMode.NEW_STACK) or dialog.launch_mode in (
            LaunchMode.ROOT,
            LaunchMode.EXCLUSIVE,
        ):
            session.stack = Stack()
            session.contexts.clear()
        if dialog.launch_mode is LaunchMode.SINGLE_TOP and session.current() is not None:
            current = session.current()
            assert current is not None
            if self.registry.find_dialog(current.state) is dialog:
                current.state = resolved
                current.start_data = data
                current.dialog_data.clear()
                current.widget_data.clear()
                if dialog.on_start is not None:
                    await dialog.on_start(data, self)
                await self.show()
                return
        context = session.stack.push(resolved, data)
        context.access_settings = session.stack.access_settings
        session.contexts[context.id] = context
        if dialog.on_start is not None:
            await dialog.on_start(data, self)
        await self.show()

    async def switch_to(self, state: State, *, show_mode: ShowMode = ShowMode.AUTO) -> None:
        resolved = self.registry.resolve_state(state)
        if self.registry.find_dialog(resolved) is not self.current_dialog():
            raise UnknownState("switch_to cannot cross a dialog boundary; use start")
        self.current_context().state = resolved
        await self.show(show_mode)

    async def next(self, *, show_mode: ShowMode = ShowMode.AUTO) -> None:
        dialog = self.current_dialog()
        index = dialog.index(self.current_context().state)
        if index + 1 >= len(dialog.windows):
            raise UnknownState("Current window is the last dialog state")
        self.current_context().state = dialog.windows[index + 1].state
        await self.show(show_mode)

    async def back(self, *, show_mode: ShowMode = ShowMode.AUTO) -> None:
        dialog = self.current_dialog()
        index = dialog.index(self.current_context().state)
        if index == 0:
            raise UnknownState("Current window is the first dialog state")
        self.current_context().state = dialog.windows[index - 1].state
        await self.show(show_mode)

    async def done(self, result: Any = None, *, show_mode: ShowMode = ShowMode.AUTO) -> None:
        session = self._active()
        context = self.current_context()
        if session.stack.last_intent_id() != context.id:
            raise OutdatedIntent("Only the top dialog context can be closed")
        dialog = self.current_dialog()
        if dialog.on_close is not None:
            await dialog.on_close(self)
        session.stack.pop()
        del session.contexts[context.id]
        parent = session.current()
        if parent is None:
            return
        parent.state = self.registry.resolve_state(parent.state)
        parent_dialog = self.registry.find_dialog(parent.state)
        if parent_dialog.on_process_result is not None:
            await parent_dialog.on_process_result(result, self)
        await self.show(show_mode)

    async def update(self, data: Mapping[str, Any], *, show_mode: ShowMode = ShowMode.AUTO) -> None:
        self.dialog_data.update(data)
        await self.show(show_mode)

    def find(self, widget_id: str) -> Any:
        return self.current_dialog().window_for(self.current_context().state).find(widget_id)

    async def submit(self, event: Any) -> None:
        window = self.current_dialog().window_for(self.current_context().state)
        callback = getattr(window, "on_submit", None)
        if callback is None:
            raise RuntimeError("The current dialog window has no submit handler")
        await callback(event, self)


__all__ = ["DialogManager"]
