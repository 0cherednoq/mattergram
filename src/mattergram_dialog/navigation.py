from __future__ import annotations

from typing import Any

from mattergram.fsm.state import State
from mattergram_dialog.api.entities import ShowMode
from mattergram_dialog.api.exceptions import InvalidWidgetIdError, UnknownState
from mattergram_dialog.manager.manager import DialogManager as BaseDialogManager
from mattergram_dialog.rendering import PostContainer, PostControl, PostControlGroup, PostRender
from mattergram_dialog.widgets.data import Getter
from mattergram_dialog.widgets.text import Text
from mattergram_dialog.window import Window as BaseWindow

_HISTORY_KEY = "__mattergram_navigation_history_v1__"
_HISTORY_LIMIT = 100


def _history(manager: BaseDialogManager) -> list[str]:
    raw = manager.current_context().widget_data.setdefault(_HISTORY_KEY, [])
    if not isinstance(raw, list) or any(not isinstance(item, str) or not item for item in raw):
        raise ValueError("Dialog navigation history is invalid")
    return raw


class DialogManager(BaseDialogManager):
    """Mattermost manager with bounded, persisted path-aware navigation history."""

    async def __aenter__(self) -> DialogManager:
        await super().__aenter__()
        return self

    async def switch_to(self, state: State, *, show_mode: ShowMode = ShowMode.AUTO) -> None:
        resolved = self.registry.resolve_state(state)
        context = self.current_context()
        if context.state == resolved:
            await self.show(show_mode)
            return
        current = context.state.state
        if current is None:
            raise UnknownState("Current dialog state has no stable name")
        history = _history(self)
        history.append(current)
        if len(history) > _HISTORY_LIMIT:
            del history[: len(history) - _HISTORY_LIMIT]
        await super().switch_to(resolved, show_mode=show_mode)

    async def next(self, *, show_mode: ShowMode = ShowMode.AUTO) -> None:
        dialog = self.current_dialog()
        index = dialog.index(self.current_context().state)
        if index + 1 >= len(dialog.windows):
            raise UnknownState("Current window is the last dialog state")
        await self.switch_to(dialog.windows[index + 1].state, show_mode=show_mode)

    async def back(self, *, show_mode: ShowMode = ShowMode.AUTO) -> None:
        history = _history(self)
        if not history:
            await super().back(show_mode=show_mode)
            return
        target = self.registry.resolve_state(history.pop())
        if self.registry.find_dialog(target) is not self.current_dialog():
            raise UnknownState("Navigation history crosses a dialog boundary")
        await BaseDialogManager.switch_to(self, target, show_mode=show_mode)

    def can_back(self) -> bool:
        return bool(_history(self)) or self.current_dialog().index(self.current_context().state) > 0


def _find_text(text: Text, widget_id: str) -> Any | None:
    if text.widget_id == widget_id:
        return text
    for name in ("texts", "body", "title", "item_text", "empty", "default"):
        nested = getattr(text, name, None)
        values = (
            nested.values()
            if isinstance(nested, dict)
            else nested
            if isinstance(nested, tuple)
            else (nested,)
        )
        for item in values:
            if isinstance(item, Text):
                found = _find_text(item, widget_id)
                if found is not None:
                    return found
    return None


def _widget_ids(widget: Any) -> list[str]:
    result = [] if widget.widget_id is None else [widget.widget_id]
    content = getattr(widget, "content", None)
    if isinstance(content, Text):
        result.extend(_text_widget_ids(content))
    for child in getattr(widget, "widgets", ()):
        result.extend(_widget_ids(child))
    for row in getattr(widget, "rows", ()):
        result.extend(_widget_ids(row))
    return result


def _text_widget_ids(text: Text) -> list[str]:
    result = [] if text.widget_id is None else [text.widget_id]
    for name in ("texts", "body", "title", "item_text", "empty", "default"):
        nested = getattr(text, name, None)
        values = (
            nested.values()
            if isinstance(nested, dict)
            else nested
            if isinstance(nested, tuple)
            else (nested,)
        )
        for item in values:
            if isinstance(item, Text):
                result.extend(_text_widget_ids(item))
    return result


class Window(BaseWindow):
    """Post window that accepts single or grouped native navigation widgets."""

    def __init__(
        self,
        text: Text,
        *widgets: Any,
        state: State,
        getter: Getter | None = None,
        container: PostContainer | None = None,
    ) -> None:
        if state.state is None:
            raise ValueError("Window state must have a stable name")
        if any(
            not callable(getattr(widget, "render_controls", None))
            and not callable(getattr(widget, "render_control_groups", None))
            for widget in widgets
        ):
            raise TypeError("Post window widgets must render controls or control groups")
        widget_ids = [widget_id for widget in widgets for widget_id in _widget_ids(widget)]
        if len(widget_ids) != len(set(widget_ids)):
            raise InvalidWidgetIdError("Post window widget IDs must be unique")
        self.text = text
        self.widgets = widgets
        self.state = state
        self.getters = () if getter is None else (getter,)
        self.container = container

    async def render(self, manager: Any) -> PostRender:
        data = await self.load_data(manager)
        text = await self.text.render_text(data, manager)
        controls: list[PostControl] = []
        groups: list[PostControlGroup] = []
        for widget in self.widgets:
            render_groups = getattr(widget, "render_control_groups", None)
            if callable(render_groups):
                rendered_groups = await render_groups(data, manager)
            else:
                rendered = await widget.render_controls(data, manager)
                rendered_groups = () if not rendered else (PostControlGroup(rendered),)
            groups.extend(rendered_groups)
            controls.extend(
                control for rendered_group in rendered_groups for control in rendered_group.controls
            )
        return PostRender(text, tuple(controls), tuple(groups), self.container)

    def find(self, widget_id: str) -> Any:
        text = _find_text(self.text, widget_id)
        if text is not None:
            return text
        for widget in self.widgets:
            if widget.widget_id == widget_id:
                return widget
            nested_find = getattr(widget, "find", None)
            if callable(nested_find):
                try:
                    return nested_find(widget_id)
                except InvalidWidgetIdError:
                    pass
        raise InvalidWidgetIdError(f"Unknown widget id: {widget_id}")


__all__ = ["DialogManager", "Window"]
