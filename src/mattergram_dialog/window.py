from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast

from mattergram.fsm.state import State
from mattergram.ui.forms import Form, FormElement
from mattergram_dialog.api.exceptions import InvalidWidgetIdError
from mattergram_dialog.rendering import FormRender, PostControl, PostRender, RenderPlan
from mattergram_dialog.widgets.data import Getter, load_data
from mattergram_dialog.widgets.form import NativeField
from mattergram_dialog.widgets.message import OpenForm
from mattergram_dialog.widgets.text import Text


class Window:
    def __init__(self, text: Text, *widgets: OpenForm, state: State, getter: Getter | None = None) -> None:
        if state.state is None:
            raise ValueError("Window state must have a stable name")
        self.text = text
        self.widgets = widgets
        self.state = state
        self.getters = () if getter is None else (getter,)

    async def load_data(self, manager: Any) -> dict[str, Any]:
        context = manager.current_context()
        base = {
            "dialog_data": context.dialog_data,
            "start_data": context.start_data,
            "widget_data": context.widget_data,
            "manager": manager,
        }
        base.update(context.dialog_data)
        return await load_data(self.getters, manager, base)

    async def render(self, manager: Any) -> RenderPlan:
        data = await self.load_data(manager)
        controls: list[PostControl] = []
        for widget in self.widgets:
            control = await widget.render_control(data, manager)
            if control is not None:
                controls.append(control)
        return PostRender(await self.text.render_text(data, manager), tuple(controls))

    def find(self, widget_id: str) -> Any:
        if self.text.widget_id == widget_id:
            return self.text
        for widget in self.widgets:
            if widget.widget_id == widget_id:
                return widget
        raise InvalidWidgetIdError(f"Unknown widget id: {widget_id}")


class FormWindow(Window):
    def __init__(
        self,
        *items: Text | FormElement | NativeField,
        state: State,
        title: Text | None = None,
        callback_id: str | None = None,
        introduction: Text | None = None,
        submit_label: str | None = None,
        submit_text: Text | None = None,
        on_submit: Any = None,
        getter: Getter | None = None,
    ) -> None:
        if title is None:
            if not items or not isinstance(items[0], Text):
                raise TypeError("FormWindow requires title=Text or a leading Text")
            title = items[0]
            items = items[1:]
        if any(isinstance(item, Text) for item in items):
            raise TypeError("Only native form fields may follow the FormWindow title")
        super().__init__(title, state=state, getter=getter)
        self.fields = items
        self.callback_id = callback_id or state.state or "dialog"
        self.introduction = introduction
        self.submit_label = submit_label
        self.submit_text = submit_text
        self.on_submit = on_submit

    async def render(self, manager: Any) -> RenderPlan:
        data = await self.load_data(manager)
        elements: list[FormElement] = []
        for item in self.fields:
            value = await item.render_field(data, manager) if isinstance(item, NativeField) else item
            if value is not None:
                elements.append(cast(FormElement, value))
        title = await self.text.render_text(data, manager)
        submit_label = self.submit_label
        if self.submit_text is not None:
            submit_label = await self.submit_text.render_text(data, manager)
        introduction = (
            await self.introduction.render_text(data, manager) if self.introduction is not None else None
        )
        return FormRender(
            Form(
                callback_id=self.callback_id,
                title=title,
                elements=tuple(elements),
                introduction_text=introduction,
                submit_label=submit_label,
                notify_on_cancel=True,
                state=manager.current_context().intent_id,
            )
        )

    def find(self, widget_id: str) -> Any:
        for item in self.fields:
            if isinstance(item, NativeField) and item.widget_id == widget_id:
                return item
        return super().find(widget_id)


def state_names(windows: Iterable[Window]) -> tuple[str, ...]:
    return tuple(window.state.state or "" for window in windows)


__all__ = ["FormWindow", "Window", "state_names"]
