from __future__ import annotations

import inspect
import re
import secrets
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any, TypeAlias, cast

from mattergram.fsm.state import State
from mattergram.ui.forms import Option
from mattergram_dialog.api.exceptions import InvalidWidget, InvalidWidgetIdError
from mattergram_dialog.rendering import PostControl, PostControlGroup
from mattergram_dialog.widgets.common.action import WidgetAction, WidgetActionResult
from mattergram_dialog.widgets.common.base import Widget
from mattergram_dialog.widgets.common.when import WhenCondition
from mattergram_dialog.widgets.paged import PagedList
from mattergram_dialog.widgets.text import Const, Text

if TYPE_CHECKING:
    from mattergram_dialog.navigation import DialogManager

_ACTION_ID = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,47}$")

ButtonResult: TypeAlias = Awaitable[None] | None
SimpleButtonClick: TypeAlias = Callable[["DialogManager"], ButtonResult]
FullButtonClick: TypeAlias = Callable[[Any, "Button", "DialogManager"], ButtonResult]
ButtonClick: TypeAlias = SimpleButtonClick | FullButtonClick
_CompiledButtonClick: TypeAlias = Callable[[Any, "Button", "DialogManager"], ButtonResult]


def _callback_name(callback: ButtonClick) -> str:
    return str(
        getattr(
            callback,
            "__qualname__",
            getattr(callback, "__name__", type(callback).__qualname__),
        )
    )


def _invalid_callback(callback: ButtonClick) -> TypeError:
    return TypeError(
        f"Button callback {_callback_name(callback)!r} must accept exactly "
        "(manager) or (event, button, manager)"
    )


def _compile_button_click(callback: ButtonClick) -> _CompiledButtonClick:
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        raise _invalid_callback(callback) from None
    parameters = tuple(signature.parameters.values())
    if any(
        parameter.kind
        in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
        for parameter in parameters
    ):
        raise _invalid_callback(callback)
    positional = tuple(
        parameter
        for parameter in parameters
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    )
    if len(positional) == 1:
        simple = cast(SimpleButtonClick, callback)

        def call_simple(event: Any, button: Button, manager: DialogManager) -> ButtonResult:
            del event, button
            return simple(manager)

        return call_simple
    if len(positional) == 3:
        return cast(FullButtonClick, callback)
    raise _invalid_callback(callback)


class Button(Widget):
    """Run a full or manager-only callback, then rebuild the current post window."""

    def __init__(
        self,
        text: Text,
        *,
        id: str,
        on_click: ButtonClick | None = None,
        style: str | None = None,
        disabled: bool = False,
        when: WhenCondition = None,
    ) -> None:
        super().__init__(id=id, when=when)
        if not _ACTION_ID.fullmatch(id):
            raise ValueError("Button id must be 1-48 ASCII letters/digits and start with a letter")
        if on_click is not None and not callable(on_click):
            raise TypeError("Button on_click must be callable or None")
        if type(disabled) is not bool:
            raise TypeError("Button disabled must be a bool")
        self.text = text
        self.on_click = on_click
        self._on_click = None if on_click is None else _compile_button_click(on_click)
        self.style = style
        self.disabled = disabled

    async def render_controls(self, data: Mapping[str, Any], manager: Any) -> tuple[PostControl, ...]:
        if not await self.is_visible(data, manager):
            return ()
        assert self.widget_id is not None
        context = manager.current_context()
        state = context.widget_data.setdefault(self.widget_id, {})
        if not isinstance(state, dict):
            raise InvalidWidget(f"Button {self.widget_id!r} state must be a mapping")
        action_token = secrets.token_urlsafe(12)
        state["action_token"] = action_token
        return (
            PostControl(
                self.widget_id,
                await self.text.render_text(data, manager),
                intent_id=context.intent_id,
                source_state=context.state.state,
                action="widget",
                widget_id=self.widget_id,
                value=action_token,
                style=self.style,
                disabled=self.disabled,
            ),
        )

    async def process_event(self, value: str | None, event: Any, manager: Any) -> bool:
        assert self.widget_id is not None
        state = manager.current_context().widget_data.get(self.widget_id)
        if not isinstance(state, dict) or value is None or state.get("action_token") != value:
            return False
        del state["action_token"]
        if self.disabled or self._on_click is None:
            return True
        result = self._on_click(event, self, manager)
        if inspect.isawaitable(result):
            await result
        return True

    async def process_action(self, action: WidgetAction, event: Any, manager: Any) -> WidgetActionResult:
        if action.type != "widget":
            raise InvalidWidget(f"Button cannot process action {action.type!r}")
        return WidgetActionResult(await self.process_event(action.value, event, manager))


class OpenForm(Widget):
    def __init__(self, text: Text, *, id: str, state: State, when: WhenCondition = None) -> None:
        super().__init__(id=id, when=when)
        if state.state is None:
            raise ValueError("OpenForm target state must have a stable name")
        self.text = text
        self.state = state

    async def render_control(self, data: Mapping[str, Any], manager: Any) -> PostControl | None:
        if not await self.is_visible(data, manager):
            return None
        assert self.widget_id is not None
        context = manager.current_context()
        return PostControl(
            id=self.widget_id,
            label=await self.text.render_text(data, manager),
            target_state=self.state.state or "",
            intent_id=context.intent_id,
            source_state=context.state.state,
        )

    async def render_controls(self, data: Mapping[str, Any], manager: Any) -> tuple[PostControl, ...]:
        control = await self.render_control(data, manager)
        return () if control is None else (control,)


class Navigate(OpenForm):
    """Render a signed post action that switches to another post window."""


SwitchTo = Navigate


class ActionRow(Widget):
    """Declare controls that belong to one logical horizontal row."""

    def __init__(
        self,
        *widgets: Widget,
        id: str | None = None,
        when: WhenCondition = None,
    ) -> None:
        super().__init__(id=id, when=when)
        if not widgets:
            raise ValueError("ActionRow requires at least one widget")
        if any(not callable(getattr(widget, "render_controls", None)) for widget in widgets):
            raise TypeError("ActionRow children must render controls")
        self.widgets = widgets

    @property
    def content_position(self) -> str:
        positions = {getattr(widget, "content_position", "after") for widget in self.widgets}
        if len(positions) != 1:
            raise ValueError("ActionRow cannot mix controls from above and below DataBlock content")
        return positions.pop()

    async def render_controls(self, data: Mapping[str, Any], manager: Any) -> tuple[PostControl, ...]:
        if not await self.is_visible(data, manager):
            return ()
        controls: list[PostControl] = []
        for widget in self.widgets:
            controls.extend(await widget.render_controls(data, manager))  # type: ignore[attr-defined]
        return tuple(controls)

    async def render_detail(
        self, data: Mapping[str, Any], manager: Any
    ) -> tuple[str, str, str | None] | None:
        if not await self.is_visible(data, manager):
            return None
        detail: tuple[str, str, str | None] | None = None
        for widget in self.widgets:
            render_detail = getattr(widget, "render_detail", None)
            if not callable(render_detail):
                continue
            rendered = await render_detail(data, manager)
            if rendered is not None:
                if detail is not None:
                    raise InvalidWidget("ActionRow can render only one selected-item detail card")
                detail = rendered
        return detail

    def find(self, widget_id: str) -> Any:
        for widget in self.widgets:
            if widget.widget_id == widget_id:
                return widget
            find = getattr(widget, "find", None)
            if callable(find):
                try:
                    return find(widget_id)
                except InvalidWidgetIdError:
                    pass
        raise InvalidWidgetIdError(f"Unknown widget id: {widget_id}")


class ButtonGrid(Widget):
    """Render explicitly declared action rows as one logical control group."""

    def __init__(
        self,
        *rows: ActionRow,
        id: str | None = None,
        title: str | None = None,
        color: str | None = None,
        when: WhenCondition = None,
    ) -> None:
        super().__init__(id=id, when=when)
        if not rows or any(not isinstance(row, ActionRow) for row in rows):
            raise TypeError("ButtonGrid requires one or more ActionRow declarations")
        if title is not None and (not title or len(title) > 80):
            raise ValueError("ButtonGrid title must contain 1-80 characters or be None")
        self.rows = rows
        self.title = title
        self.color = color

    async def render_control_groups(
        self, data: Mapping[str, Any], manager: Any
    ) -> tuple[PostControlGroup, ...]:
        if not await self.is_visible(data, manager):
            return ()
        collected_rows: list[tuple[PostControl, ...]] = []
        for row in self.rows:
            controls = await row.render_controls(data, manager)
            if controls:
                collected_rows.append(controls)
        rendered_rows = tuple(collected_rows)
        controls = tuple(control for row in rendered_rows for control in row)
        if not controls:
            return ()
        return (
            PostControlGroup(
                controls,
                title=self.title,
                color=self.color,
                rows=rendered_rows,
                row_width="stretch",
            ),
        )

    def find(self, widget_id: str) -> Any:
        for row in self.rows:
            if row.widget_id == widget_id:
                return row
            try:
                return row.find(widget_id)
            except InvalidWidgetIdError:
                pass
        raise InvalidWidgetIdError(f"Unknown widget id: {widget_id}")


class ButtonRow(Widget):
    """Combine controls into one logical group that wraps at client width."""

    def __init__(
        self,
        *widgets: Widget,
        id: str | None = None,
        title: str | None = None,
        color: str | None = None,
        when: WhenCondition = None,
    ) -> None:
        super().__init__(id=id, when=when)
        if not widgets:
            raise ValueError("ButtonRow requires at least one widget")
        if any(not callable(getattr(widget, "render_controls", None)) for widget in widgets):
            raise TypeError("ButtonRow children must render controls")
        if title is not None and (not title or len(title) > 80):
            raise ValueError("ButtonRow title must contain 1-80 characters or be None")
        self.widgets = widgets
        self.title = title
        self.color = color

    async def render_control_groups(
        self, data: Mapping[str, Any], manager: Any
    ) -> tuple[PostControlGroup, ...]:
        if not await self.is_visible(data, manager):
            return ()
        controls: list[PostControl] = []
        for widget in self.widgets:
            render_controls = getattr(widget, "render_controls")
            controls.extend(await render_controls(data, manager))
        if not controls:
            return ()
        rendered = tuple(controls)
        return (
            PostControlGroup(
                rendered,
                title=self.title,
                color=self.color,
                rows=(rendered,),
            ),
        )

    def find(self, widget_id: str) -> Any:
        for widget in self.widgets:
            if widget.widget_id == widget_id:
                return widget
        raise InvalidWidgetIdError(f"Unknown widget id: {widget_id}")


Row = ButtonRow


class Back(Widget):
    """Return to the actual previous window in this dialog context."""

    def __init__(self, text: Text, *, id: str = "back", when: WhenCondition = None) -> None:
        super().__init__(id=id, when=when)
        self.text = text

    async def render_controls(self, data: Mapping[str, Any], manager: Any) -> tuple[PostControl, ...]:
        if not await self.is_visible(data, manager) or not manager.can_back():
            return ()
        assert self.widget_id is not None
        context = manager.current_context()
        return (
            PostControl(
                self.widget_id,
                await self.text.render_text(data, manager),
                intent_id=context.intent_id,
                source_state=context.state.state,
                action="back",
            ),
        )


class Next(Widget):
    """Open the next declared window and record the current one for Back."""

    def __init__(self, text: Text, *, id: str = "next", when: WhenCondition = None) -> None:
        super().__init__(id=id, when=when)
        self.text = text

    async def render_controls(self, data: Mapping[str, Any], manager: Any) -> tuple[PostControl, ...]:
        if not await self.is_visible(data, manager):
            return ()
        dialog = manager.current_dialog()
        if dialog.index(manager.current_context().state) + 1 >= len(dialog.windows):
            return ()
        assert self.widget_id is not None
        context = manager.current_context()
        return (
            PostControl(
                self.widget_id,
                await self.text.render_text(data, manager),
                intent_id=context.intent_id,
                source_state=context.state.state,
                action="next",
            ),
        )


class Pager(Widget):
    """Render bounded previous/next controls for one PagedList."""

    def __init__(
        self,
        *,
        id: str,
        target: str,
        previous: str = "← Previous",
        next: str = "Next →",
        when: WhenCondition = None,
    ) -> None:
        super().__init__(id=id, when=when)
        if not _ACTION_ID.fullmatch(id):
            raise ValueError("Pager id must be 1-48 ASCII letters/digits and start with a letter")
        if not target:
            raise ValueError("Pager target must be nonempty")
        if not previous or not next:
            raise ValueError("Pager labels must be nonempty")
        self.target = target
        self.previous = previous
        self.next = next

    def _target(self, manager: Any) -> PagedList:
        target = manager.find(self.target)
        if not isinstance(target, PagedList):
            raise InvalidWidget(f"Pager target {self.target!r} is not a PagedList")
        return target

    async def render_controls(self, data: Mapping[str, Any], manager: Any) -> tuple[PostControl, ...]:
        if not await self.is_visible(data, manager):
            return ()
        target = self._target(manager)
        state = target.widget_state(manager)
        page = state.get("page", 0)
        pages = state.get("pages", 1)
        if type(page) is not int or type(pages) is not int:
            raise InvalidWidget("PagedList cursor is invalid")
        assert self.widget_id is not None
        context = manager.current_context()
        controls: list[PostControl] = []
        if page > 0:
            controls.append(
                PostControl(
                    self.widget_id + "Prev",
                    self.previous,
                    intent_id=context.intent_id,
                    source_state=context.state.state,
                    action="page",
                    widget_id=self.widget_id,
                    value="prev",
                )
            )
        if page + 1 < pages:
            controls.append(
                PostControl(
                    self.widget_id + "Next",
                    self.next,
                    intent_id=context.intent_id,
                    source_state=context.state.state,
                    action="page",
                    widget_id=self.widget_id,
                    value="next",
                )
            )
        return tuple(controls)

    def apply(self, value: str | None, manager: Any) -> None:
        self._target(manager).move(value, manager)

    async def process_action(self, action: WidgetAction, event: Any, manager: Any) -> WidgetActionResult:
        del event
        if action.type != "page":
            raise InvalidWidget(f"Pager cannot process action {action.type!r}")
        self.apply(action.value, manager)
        return WidgetActionResult()


class CompactPager(Pager):
    """Stable three-control preset: < 1/25 >."""

    async def render_controls(self, data: Mapping[str, Any], manager: Any) -> tuple[PostControl, ...]:
        if not await self.is_visible(data, manager):
            return ()
        target = self._target(manager)
        state = target.widget_state(manager)
        page = state.get("page", 0)
        pages = state.get("pages", 1)
        if type(page) is not int or type(pages) is not int:
            raise InvalidWidget("PagedList cursor is invalid")
        assert self.widget_id is not None
        widget_id = self.widget_id
        context = manager.current_context()

        def control(suffix: str, label: str, value: str, disabled: bool) -> PostControl:
            return PostControl(
                widget_id + suffix,
                label,
                intent_id=context.intent_id,
                source_state=context.state.state,
                action="page",
                widget_id=self.widget_id,
                value=value,
                disabled=disabled,
            )

        return (
            control("Prev", "<", "prev", page == 0),
            control("Position", f"{page + 1}/{pages}", "current", True),
            control("Next", ">", "next", page + 1 >= pages),
        )


class InlinePager(Pager):
    """Stable two-control preset for a DataBlock that also contains filters."""

    def __init__(
        self,
        *,
        id: str,
        target: str,
        previous: str = "<",
        next: str = ">",
        when: WhenCondition = None,
    ) -> None:
        super().__init__(
            id=id,
            target=target,
            previous=previous,
            next=next,
            when=when,
        )

    async def render_controls(self, data: Mapping[str, Any], manager: Any) -> tuple[PostControl, ...]:
        if not await self.is_visible(data, manager):
            return ()
        target = self._target(manager)
        state = target.widget_state(manager)
        page = state.get("page", 0)
        pages = state.get("pages", 1)
        if type(page) is not int or type(pages) is not int:
            raise InvalidWidget("PagedList cursor is invalid")
        assert self.widget_id is not None
        widget_id = self.widget_id
        context = manager.current_context()

        def control(suffix: str, label: str, value: str, disabled: bool) -> PostControl:
            return PostControl(
                widget_id + suffix,
                label,
                intent_id=context.intent_id,
                source_state=context.state.state,
                action="page",
                widget_id=widget_id,
                value=value,
                disabled=disabled,
            )

        return (
            control("Prev", self.previous, "prev", page == 0),
            control("Next", self.next, "next", page + 1 >= pages),
        )


class PagePager(Pager):
    """Stable five-control preset: << < 1/25 > >>."""

    async def render_controls(self, data: Mapping[str, Any], manager: Any) -> tuple[PostControl, ...]:
        if not await self.is_visible(data, manager):
            return ()
        target = self._target(manager)
        state = target.widget_state(manager)
        page = state.get("page", 0)
        pages = state.get("pages", 1)
        if type(page) is not int or type(pages) is not int:
            raise InvalidWidget("PagedList cursor is invalid")
        assert self.widget_id is not None
        widget_id = self.widget_id
        context = manager.current_context()

        def control(suffix: str, label: str, value: str, disabled: bool) -> PostControl:
            return PostControl(
                widget_id + suffix,
                label,
                intent_id=context.intent_id,
                source_state=context.state.state,
                action="page",
                widget_id=self.widget_id,
                value=value,
                disabled=disabled,
            )

        return (
            control("First", "<<", "first", page == 0),
            control("Prev", "<", "prev", page == 0),
            control("Position", f"{page + 1}/{pages}", "current", True),
            control("Next", ">", "next", page + 1 >= pages),
            control("Last", ">>", "last", page + 1 >= pages),
        )


_ItemValue = str | Callable[[Any], Any]


class PageItemSelect(Widget):
    """Select one item from the visible page next to its pagination controls."""

    content_position = "after"

    def __init__(
        self,
        *,
        id: str,
        target: str,
        item_id: _ItemValue,
        item_label: _ItemValue,
        selection_key: str,
        placeholder: str = "Select item",
        detail_title: str | Text | None = None,
        detail: str | Text | None = None,
        detail_color: str | None = None,
        when: WhenCondition = None,
    ) -> None:
        super().__init__(id=id, when=when)
        if not _ACTION_ID.fullmatch(id):
            raise ValueError("PageItemSelect id must be 1-48 ASCII letters/digits and start with a letter")
        if not target or not selection_key or not placeholder:
            raise ValueError("PageItemSelect target, selection_key and placeholder must be nonempty")
        if (detail_title is None) != (detail is None):
            raise ValueError("PageItemSelect detail_title and detail must be declared together")
        if detail_color is not None and detail is None:
            raise ValueError("PageItemSelect detail_color requires detail content")
        self.target = target
        self.item_id = item_id
        self.item_label = item_label
        self.selection_key = selection_key
        self.placeholder = placeholder
        self.detail_title = Const(detail_title) if isinstance(detail_title, str) else detail_title
        self.detail = Const(detail) if isinstance(detail, str) else detail
        self.detail_color = detail_color

    @staticmethod
    def _read(item: Any, getter: _ItemValue) -> str:
        if callable(getter):
            value = getter(item)
        elif isinstance(item, Mapping):
            value = item.get(getter)
        else:
            value = getattr(item, getter)
        result = str("" if value is None else value)
        if not result:
            raise InvalidWidget("PageItemSelect values and labels must be nonempty")
        return result

    def _target(self, manager: Any) -> PagedList:
        target = manager.find(self.target)
        if not isinstance(target, PagedList):
            raise InvalidWidget(f"PageItemSelect target {self.target!r} is not a PagedList")
        return target

    async def render_controls(self, data: Mapping[str, Any], manager: Any) -> tuple[PostControl, ...]:
        if not await self.is_visible(data, manager):
            return ()
        target = self._target(manager)
        items, _, _, _ = target.page_items(data, manager)
        options = tuple(
            Option(text=self._read(item, self.item_label), value=self._read(item, self.item_id))
            for item in items
        )
        if not options:
            return ()
        allowed = [option.value for option in options]
        if len(allowed) != len(set(allowed)):
            raise InvalidWidget("PageItemSelect item values must be unique on the visible page")
        target.widget_state(manager)["select_options"] = allowed
        selected = manager.dialog_data.get(self.selection_key)
        initial = selected if isinstance(selected, str) and selected in set(allowed) else None
        assert self.widget_id is not None
        context = manager.current_context()
        return (
            PostControl(
                self.widget_id,
                self.placeholder,
                intent_id=context.intent_id,
                source_state=context.state.state,
                action="select",
                widget_id=self.widget_id,
                options=options,
                initial_option=initial,
            ),
        )

    async def render_detail(
        self, data: Mapping[str, Any], manager: Any
    ) -> tuple[str, str, str | None] | None:
        if self.detail_title is None or self.detail is None:
            return None
        selected = manager.dialog_data.get(self.selection_key)
        if not isinstance(selected, str):
            return None
        items, _, _, _ = self._target(manager).page_items(data, manager)
        item = next(
            (candidate for candidate in items if self._read(candidate, self.item_id) == selected),
            None,
        )
        if item is None:
            return None
        detail_data = dict(data)
        detail_data.update(item=item, selected_item=item)
        title = await self.detail_title.render_text(detail_data, manager)
        text = await self.detail.render_text(detail_data, manager)
        if not title or len(title) > 80:
            raise InvalidWidget("PageItemSelect detail title must contain 1-80 characters")
        if not text:
            raise InvalidWidget("PageItemSelect detail must be nonempty")
        return title, text, self.detail_color

    def apply(self, value: str | None, manager: Any) -> None:
        if value is None:
            raise InvalidWidget("PageItemSelect requires a selected option")
        allowed = self._target(manager).widget_state(manager).get("select_options", [])
        if not isinstance(allowed, list) or value not in allowed:
            raise InvalidWidget("Selected item is not on the rendered page")
        manager.dialog_data[self.selection_key] = value

    async def process_action(self, action: WidgetAction, event: Any, manager: Any) -> WidgetActionResult:
        if action.type != "select":
            raise InvalidWidget(f"PageItemSelect cannot process action {action.type!r}")
        self.apply(getattr(event, "selected_option", None), manager)
        return WidgetActionResult()


class CursorPager(Widget):
    """Two-control preset for APIs that expose an opaque next cursor."""

    def __init__(
        self,
        *,
        id: str,
        target: str,
        previous: str = "< Newer",
        next: str = "Older >",
        when: WhenCondition = None,
    ) -> None:
        super().__init__(id=id, when=when)
        if not _ACTION_ID.fullmatch(id):
            raise ValueError("CursorPager id must be 1-48 ASCII letters/digits and start with a letter")
        self.target = target
        self.previous = previous
        self.next = next

    def _target(self, manager: Any) -> Any:
        from mattergram_dialog.widgets.cursor import CursorList

        target = manager.find(self.target)
        if not isinstance(target, CursorList):
            raise InvalidWidget(f"CursorPager target {self.target!r} is not a CursorList")
        return target

    async def render_controls(self, data: Mapping[str, Any], manager: Any) -> tuple[PostControl, ...]:
        if not await self.is_visible(data, manager):
            return ()
        target = self._target(manager)
        state = target.widget_state(manager)
        history = state.get("history", [])
        assert self.widget_id is not None
        context = manager.current_context()
        return (
            PostControl(
                self.widget_id + "Prev",
                self.previous,
                intent_id=context.intent_id,
                source_state=context.state.state,
                action="page",
                widget_id=self.widget_id,
                value="prev",
                disabled=not history,
            ),
            PostControl(
                self.widget_id + "Next",
                self.next,
                intent_id=context.intent_id,
                source_state=context.state.state,
                action="page",
                widget_id=self.widget_id,
                value="next",
                disabled=state.get("next_cursor") is None,
            ),
        )

    def apply(self, value: str | None, manager: Any) -> None:
        self._target(manager).move(value, manager)

    async def process_action(self, action: WidgetAction, event: Any, manager: Any) -> WidgetActionResult:
        del event
        if action.type != "page":
            raise InvalidWidget(f"CursorPager cannot process action {action.type!r}")
        self.apply(action.value, manager)
        return WidgetActionResult()


class Filter(Widget):
    """Render a fixed allowlist of filters for one PagedList."""

    content_position = "before"

    def __init__(
        self,
        *,
        id: str,
        target: str,
        options: Mapping[str, str | None],
        selected_prefix: str = "",
        selected_style: str | None = "primary",
        when: WhenCondition = None,
    ) -> None:
        super().__init__(id=id, when=when)
        if not _ACTION_ID.fullmatch(id):
            raise ValueError("Filter id must be 1-48 ASCII letters/digits and start with a letter")
        if not target:
            raise ValueError("Filter target must be nonempty")
        if not options or len(options) > 20:
            raise ValueError("Filter requires between 1 and 20 options")
        if any(not isinstance(label, str) or not label for label in options):
            raise ValueError("Filter labels must be nonempty strings")
        values = tuple(options.values())
        if any(value is not None and (not isinstance(value, str) or not value) for value in values):
            raise ValueError("Filter values must be nonempty strings or None")
        if len(values) != len(set(values)):
            raise ValueError("Filter values must be unique")
        self.target = target
        self.options = tuple(options.items())
        self.selected_prefix = selected_prefix
        self.selected_style = selected_style

    def _target(self, manager: Any) -> PagedList:
        target = manager.find(self.target)
        if not isinstance(target, PagedList):
            raise InvalidWidget(f"Filter target {self.target!r} is not a PagedList")
        return target

    async def render_controls(self, data: Mapping[str, Any], manager: Any) -> tuple[PostControl, ...]:
        if not await self.is_visible(data, manager):
            return ()
        target = self._target(manager)
        selected = target.widget_state(manager).get("filter")
        assert self.widget_id is not None
        context = manager.current_context()
        return tuple(
            PostControl(
                f"{self.widget_id}{index}",
                (self.selected_prefix if value == selected else "") + label,
                intent_id=context.intent_id,
                source_state=context.state.state,
                action="filter",
                widget_id=self.widget_id,
                value=value,
                style=self.selected_style if value == selected else None,
            )
            for index, (label, value) in enumerate(self.options)
        )

    def apply(self, value: str | None, manager: Any) -> None:
        if value not in {item[1] for item in self.options}:
            raise InvalidWidget("Filter value is not in the declared allowlist")
        self._target(manager).select_filter(value, manager)

    async def process_action(self, action: WidgetAction, event: Any, manager: Any) -> WidgetActionResult:
        del event
        if action.type != "filter":
            raise InvalidWidget(f"Filter cannot process action {action.type!r}")
        self.apply(action.value, manager)
        return WidgetActionResult()


__all__ = [
    "ActionRow",
    "Back",
    "Button",
    "ButtonClick",
    "ButtonResult",
    "ButtonGrid",
    "ButtonRow",
    "CompactPager",
    "CursorPager",
    "Filter",
    "FullButtonClick",
    "InlinePager",
    "Navigate",
    "Next",
    "OpenForm",
    "PageItemSelect",
    "PagePager",
    "Pager",
    "Row",
    "SimpleButtonClick",
    "SwitchTo",
]
