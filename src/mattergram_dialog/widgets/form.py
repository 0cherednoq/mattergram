from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from typing import Any, Literal

from magic_filter import MagicFilter

from mattergram.ui.forms import Checkbox as NativeCheckbox
from mattergram.ui.forms import CheckboxGroup as NativeCheckboxGroup
from mattergram.ui.forms import DatePicker as NativeDatePicker
from mattergram.ui.forms import DateTimePicker as NativeDateTimePicker
from mattergram.ui.forms import FileInput as NativeFileInput
from mattergram.ui.forms import FormElement, Option
from mattergram.ui.forms import MultiSelect as NativeMultiSelect
from mattergram.ui.forms import Radio as NativeRadio
from mattergram.ui.forms import Select as NativeSelect
from mattergram.ui.forms import TextArea as NativeTextArea
from mattergram.ui.forms import TextInput as NativeTextInput
from mattergram_dialog.widgets.common.base import Widget
from mattergram_dialog.widgets.common.when import WhenCondition
from mattergram_dialog.widgets.text import Text

FormValueGetter = Callable[[Mapping[str, Any], Any], Any | Awaitable[Any]]
DynamicFormValue = MagicFilter | Text | FormValueGetter
_DYNAMIC_OPTIONS_PLACEHOLDER = (Option(text="Loading", value="__mattergram_dynamic__"),)


def _is_dynamic(value: Any) -> bool:
    return isinstance(value, (MagicFilter, Text)) or callable(value)


def _split_dynamic(name: str, value: Any, fallback: Any) -> tuple[Any, dict[str, DynamicFormValue]]:
    if _is_dynamic(value):
        return fallback, {name: value}
    return value, {}


class NativeField(Widget):
    """Conditionally include an existing typed Mattermost form element."""

    def __init__(
        self,
        field: FormElement,
        *,
        dynamic_values: Mapping[str, DynamicFormValue] | None = None,
        when: WhenCondition = None,
    ) -> None:
        super().__init__(id=field.name, when=when)
        self.field = field
        self.dynamic_values = dict(dynamic_values or {})

    @staticmethod
    async def _resolve(source: DynamicFormValue, data: Mapping[str, Any], manager: Any) -> Any:
        if isinstance(source, Text):
            return await source.render_text(data, manager)
        if isinstance(source, MagicFilter):
            return source.resolve(data)
        value = source(data, manager)
        return await value if inspect.isawaitable(value) else value

    async def render_field(self, data: Mapping[str, Any], manager: Any) -> FormElement | None:
        if not await self.is_visible(data, manager):
            return None
        if not self.dynamic_values:
            return deepcopy(self.field)
        field_type = type(self.field)
        values = {name: deepcopy(getattr(self.field, name)) for name in field_type.model_fields}
        for name, source in self.dynamic_values.items():
            values[name] = await self._resolve(source, data, manager)
        return field_type.model_validate(values)


class TextInput(NativeField):
    def __init__(
        self,
        *,
        id: str,
        label: str,
        subtype: Literal["text", "email", "number", "password", "tel", "url"] = "text",
        default: str | None | DynamicFormValue = None,
        placeholder: str | None = None,
        help_text: str | None = None,
        min_length: int = 0,
        max_length: int = 150,
        optional: bool = False,
        when: WhenCondition = None,
    ) -> None:
        static_default, dynamic = _split_dynamic("default", default, None)
        super().__init__(
            NativeTextInput(
                name=id,
                label=label,
                subtype=subtype,
                default=static_default,
                placeholder=placeholder,
                help_text=help_text,
                min_length=min_length,
                max_length=max_length,
                optional=optional,
            ),
            dynamic_values=dynamic,
            when=when,
        )


class Checkbox(NativeField):
    def __init__(
        self,
        *,
        id: str,
        label: str,
        default: bool | None | DynamicFormValue = None,
        help_text: str | None = None,
        placeholder: str | None = None,
        label_position: Literal["before", "after"] | None = None,
        optional: bool = False,
        when: WhenCondition = None,
    ) -> None:
        static_default, dynamic = _split_dynamic("default", default, None)
        super().__init__(
            NativeCheckbox(
                name=id,
                label=label,
                default=static_default,
                help_text=help_text,
                placeholder=placeholder,
                label_position=label_position,
                optional=optional,
            ),
            dynamic_values=dynamic,
            when=when,
        )


class TextArea(NativeField):
    def __init__(
        self,
        *,
        id: str,
        label: str,
        default: str | None | DynamicFormValue = None,
        placeholder: str | None = None,
        help_text: str | None = None,
        min_length: int = 0,
        max_length: int = 3000,
        optional: bool = False,
        when: WhenCondition = None,
    ) -> None:
        static_default, dynamic = _split_dynamic("default", default, None)
        super().__init__(
            NativeTextArea(
                name=id,
                label=label,
                default=static_default,
                placeholder=placeholder,
                help_text=help_text,
                min_length=min_length,
                max_length=max_length,
                optional=optional,
            ),
            dynamic_values=dynamic,
            when=when,
        )


class Select(NativeField):
    def __init__(
        self,
        *,
        id: str,
        label: str,
        options: tuple[Option, ...] | DynamicFormValue = (),
        data_source: Literal["users", "channels", "dynamic"] | None = None,
        data_source_url: str | None = None,
        default: str | None | DynamicFormValue = None,
        placeholder: str | None = None,
        help_text: str | None = None,
        refresh: bool = False,
        optional: bool = False,
        when: WhenCondition = None,
    ) -> None:
        static_options, dynamic_options = _split_dynamic("options", options, _DYNAMIC_OPTIONS_PLACEHOLDER)
        static_default, dynamic = _split_dynamic("default", default, None)
        super().__init__(
            NativeSelect(
                name=id,
                label=label,
                options=static_options,
                data_source=data_source,
                data_source_url=data_source_url,
                default=static_default,
                placeholder=placeholder,
                help_text=help_text,
                refresh=refresh,
                optional=optional,
            ),
            dynamic_values={**dynamic_options, **dynamic},
            when=when,
        )


class MultiSelect(NativeField):
    def __init__(
        self,
        *,
        id: str,
        label: str,
        options: tuple[Option, ...] | DynamicFormValue = (),
        data_source: Literal["users", "channels", "dynamic"] | None = None,
        data_source_url: str | None = None,
        default_values: tuple[str, ...] | DynamicFormValue = (),
        placeholder: str | None = None,
        help_text: str | None = None,
        refresh: bool = False,
        optional: bool = False,
        when: WhenCondition = None,
    ) -> None:
        static_options, dynamic_options = _split_dynamic("options", options, _DYNAMIC_OPTIONS_PLACEHOLDER)
        static_defaults, dynamic = _split_dynamic("default_values", default_values, ())
        super().__init__(
            NativeMultiSelect(
                name=id,
                label=label,
                options=static_options,
                data_source=data_source,
                data_source_url=data_source_url,
                default_values=static_defaults,
                placeholder=placeholder,
                help_text=help_text,
                refresh=refresh,
                optional=optional,
            ),
            dynamic_values={**dynamic_options, **dynamic},
            when=when,
        )


class Radio(NativeField):
    def __init__(
        self,
        *,
        id: str,
        label: str,
        options: tuple[Option, ...],
        default: str | None | DynamicFormValue = None,
        help_text: str | None = None,
        optional: bool = False,
        when: WhenCondition = None,
    ) -> None:
        static_default, dynamic = _split_dynamic("default", default, None)
        super().__init__(
            NativeRadio(
                name=id,
                label=label,
                options=options,
                default=static_default,
                help_text=help_text,
                optional=optional,
            ),
            dynamic_values=dynamic,
            when=when,
        )


class CheckboxGroup(NativeField):
    def __init__(
        self,
        *,
        id: str,
        label: str,
        options: tuple[Option, ...],
        default_values: tuple[str, ...] | DynamicFormValue = (),
        help_text: str | None = None,
        optional: bool = False,
        when: WhenCondition = None,
    ) -> None:
        static_defaults, dynamic = _split_dynamic("default_values", default_values, ())
        super().__init__(
            NativeCheckboxGroup(
                name=id,
                label=label,
                options=options,
                default_values=static_defaults,
                help_text=help_text,
                optional=optional,
            ),
            dynamic_values=dynamic,
            when=when,
        )


class DatePicker(NativeField):
    def __init__(
        self,
        *,
        id: str,
        label: str,
        default: str | None | DynamicFormValue = None,
        placeholder: str | None = None,
        min_date: str | None = None,
        max_date: str | None = None,
        help_text: str | None = None,
        optional: bool = False,
        when: WhenCondition = None,
    ) -> None:
        static_default, dynamic = _split_dynamic("default", default, None)
        super().__init__(
            NativeDatePicker(
                name=id,
                label=label,
                default=static_default,
                placeholder=placeholder,
                min_date=min_date,
                max_date=max_date,
                help_text=help_text,
                optional=optional,
            ),
            dynamic_values=dynamic,
            when=when,
        )


class FileInput(NativeField):
    def __init__(
        self,
        *,
        id: str,
        label: str,
        allow_multiple: bool = False,
        placeholder: str | None = None,
        help_text: str | None = None,
        optional: bool = False,
        when: WhenCondition = None,
    ) -> None:
        super().__init__(
            NativeFileInput(
                name=id,
                label=label,
                allow_multiple=allow_multiple,
                placeholder=placeholder,
                help_text=help_text,
                optional=optional,
            ),
            when=when,
        )


class DateTimePicker(NativeField):
    def __init__(
        self,
        *,
        id: str,
        label: str,
        default: str | None | DynamicFormValue = None,
        placeholder: str | None = None,
        min_date: str | None = None,
        max_date: str | None = None,
        help_text: str | None = None,
        optional: bool = False,
        time_interval: int = 60,
        when: WhenCondition = None,
    ) -> None:
        static_default, dynamic = _split_dynamic("default", default, None)
        super().__init__(
            NativeDateTimePicker(
                name=id,
                label=label,
                default=static_default,
                placeholder=placeholder,
                min_date=min_date,
                max_date=max_date,
                help_text=help_text,
                optional=optional,
                time_interval=time_interval,
            ),
            dynamic_values=dynamic,
            when=when,
        )


__all__ = [
    "Checkbox",
    "CheckboxGroup",
    "DatePicker",
    "DateTimePicker",
    "DynamicFormValue",
    "FileInput",
    "MultiSelect",
    "NativeField",
    "FormValueGetter",
    "Radio",
    "Select",
    "TextArea",
    "TextInput",
]
