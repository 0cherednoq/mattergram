"""Native form declarations with version-aware serialization, without invented fields."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import AliasChoices, BeforeValidator, Field, field_validator, model_validator

from .base import UIModel, byte_limit, http_url, integration_url, sequence
from .capabilities import UIProfile


class Option(UIModel):
    text: str = Field(min_length=1)
    value: str = Field(min_length=1)


Options = Annotated[tuple[Option, ...], BeforeValidator(sequence)]
Strings = Annotated[tuple[str, ...], BeforeValidator(sequence)]


def unique_options(options: tuple[Option, ...], *, forbidden: str = "", maximum: int | None = None) -> None:
    values = [o.value for o in options]
    if not options or len(set(values)) != len(values):
        raise ValueError("Options must be nonempty with unique values")
    if maximum is not None and len(options) > maximum:
        raise ValueError("Too many options for the native field")
    if any(c in value for value in values for c in forbidden):
        raise ValueError("An option contains a native serialization delimiter")


def date_expression(value: str) -> str:
    if value in ("today", "tomorrow", "yesterday") or re.fullmatch(r"[+-][0-9]{1,3}[dwmHMS]", value):
        return value
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T.+)?", value):
        raise ValueError("Expected an ISO date/datetime or confirmed relative expression")
    try:
        date.fromisoformat(value)
    except ValueError:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Expected an ISO date/datetime or confirmed relative expression") from None
    return value


class FormField(UIModel):
    name: str = Field(min_length=1)
    label: str = Field(
        validation_alias=AliasChoices("label", "display_name"),
        serialization_alias="display_name",
        min_length=1,
    )
    help_text: str | None = None
    optional: bool = False

    @field_validator("name", "label", "help_text")
    @classmethod
    def limits(cls, value: str | None, info: Any) -> str | None:
        return (
            byte_limit(value, {"name": 300, "label": 24, "help_text": 150}[info.field_name])
            if value is not None
            else None
        )

    def to_wire(self, profile: UIProfile) -> dict[str, Any]:
        profile.require("forms.basic")
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class TextInput(FormField):
    type: Literal["text"] = "text"
    subtype: Literal["text", "email", "number", "password", "tel", "url"] = "text"
    default: str | None = Field(default=None, repr=False)
    placeholder: str | None = None
    min_length: int = Field(default=0, ge=0)
    max_length: int = Field(default=150, ge=0, le=150)

    @model_validator(mode="after")
    def text_limits(self) -> Self:
        if self.min_length > self.max_length:
            raise ValueError("min_length exceeds max_length")
        for value in (self.default, self.placeholder):
            if value is not None:
                byte_limit(value, 3000 if isinstance(self, TextArea) else 150)
        return self


class TextArea(TextInput):
    type: Literal["textarea"] = "textarea"  # type: ignore[assignment]
    max_length: int = Field(default=3000, ge=0, le=3000)


class Select(FormField):
    type: Literal["select"] = "select"
    options: Options = ()
    data_source: Literal["users", "channels", "dynamic"] | None = None
    data_source_url: str | None = None
    default: str | None = Field(default=None, repr=False)
    placeholder: str | None = None
    refresh: bool = False

    @model_validator(mode="after")
    def choices(self) -> Self:
        if self.data_source is None:
            unique_options(self.options, forbidden="," if isinstance(self, MultiSelect) else "")
            defaults = (
                self.default_values
                if isinstance(self, MultiSelect)
                else (() if self.default in (None, "") else (self.default,))
            )
            if any(value not in {o.value for o in self.options} for value in defaults):
                raise ValueError("Default is not in the static options")
        elif self.options:
            raise ValueError("A select cannot combine a data source and static options")
        if self.data_source == "dynamic":
            if self.data_source_url is None:
                raise ValueError("Dynamic select requires data_source_url")
            integration_url(self.data_source_url)
        elif self.data_source_url is not None:
            raise ValueError("Only dynamic selects accept data_source_url")
        for value in (self.default, self.placeholder):
            if value is not None:
                byte_limit(value, 3000)
        return self

    def to_wire(self, profile: UIProfile) -> dict[str, Any]:
        result = super().to_wire(profile)
        if self.data_source == "dynamic":
            profile.require("forms.lookup")
        if self.refresh:
            profile.require("forms.refresh")
        return result


class MultiSelect(Select):
    default: None = Field(default=None, exclude=True, repr=False)  # use typed default_values
    default_values: Strings = Field(default=(), repr=False, exclude=True)

    @model_validator(mode="after")
    def multi_defaults(self) -> Self:
        if any("," in value or value.strip() != value or not value for value in self.default_values):
            raise ValueError("Multiselect defaults must not contain commas or outer whitespace")
        byte_limit(",".join(self.default_values), 3000)
        return self

    def to_wire(self, profile: UIProfile) -> dict[str, Any]:
        profile.require("forms.multi")
        return {**super().to_wire(profile), "multiselect": True, "default": ",".join(self.default_values)}


class Checkbox(FormField):
    type: Literal["bool"] = "bool"
    default: bool | None = Field(default=None, repr=False)
    placeholder: str | None = Field(default=None, max_length=150)
    label_position: Literal["before", "after"] | None = None

    def to_wire(self, profile: UIProfile) -> dict[str, Any]:
        result = super().to_wire(profile)
        if self.default is not None:
            result["default"] = "true" if self.default else "false"
        if self.placeholder is not None:
            byte_limit(self.placeholder, 150)
        if self.label_position is not None:
            profile.require("forms.label_position")
        return result


class Radio(FormField):
    type: Literal["radio"] = "radio"
    options: Options
    default: str | None = Field(default=None, repr=False)
    label_position: Literal["before", "after"] | None = None

    @model_validator(mode="after")
    def choices(self) -> Self:
        unique_options(self.options)
        if self.default is not None and self.default not in {o.value for o in self.options}:
            raise ValueError("Default is not in the static options")
        return self

    def to_wire(self, profile: UIProfile) -> dict[str, Any]:
        if self.label_position is not None:
            profile.require("forms.label_position")
        return super().to_wire(profile)


class CheckboxGroup(FormField):
    type: Literal["checkbox_group"] = "checkbox_group"
    options: Options
    default_values: Strings = Field(default=(), repr=False, exclude=True)
    label_position: Literal["before", "after"] | None = None

    @model_validator(mode="after")
    def choices(self) -> Self:
        unique_options(self.options, forbidden=",", maximum=50)
        if any(v not in {o.value for o in self.options} for v in self.default_values):
            raise ValueError("Default is not in the checkbox group options")
        byte_limit(",".join(self.default_values), 3000)
        return self

    def to_wire(self, profile: UIProfile) -> dict[str, Any]:
        profile.require("forms.checkbox_group")
        if self.label_position is not None:
            profile.require("forms.label_position")
        return {**super().to_wire(profile), "default": ",".join(self.default_values)}


class MatrixConfig(UIModel):
    rows: Options
    columns: Options
    row_selection: Literal["multiple", "single"] = "multiple"

    @model_validator(mode="after")
    def choices(self) -> Self:
        unique_options(self.rows, forbidden=":,;", maximum=30)
        unique_options(self.columns, forbidden=":,;", maximum=10)
        return self

    def validate_selection(self, selected: dict[str, tuple[str, ...]]) -> None:
        if set(selected) - {o.value for o in self.rows}:
            raise ValueError("Unknown matrix row")
        for values in selected.values():
            if not values or len(set(values)) != len(values) or set(values) - {o.value for o in self.columns}:
                raise ValueError("Invalid matrix column selection")
            if self.row_selection == "single" and len(values) != 1:
                raise ValueError("This matrix allows one column per row")


class CheckboxMatrix(FormField):
    type: Literal["checkbox_matrix"] = "checkbox_matrix"
    matrix_config: MatrixConfig
    default_values: dict[str, Strings] = Field(default_factory=dict, exclude=True, repr=False)

    @model_validator(mode="after")
    def defaults(self) -> Self:
        self.matrix_config.validate_selection(self.default_values)
        return self

    def to_wire(self, profile: UIProfile) -> dict[str, Any]:
        profile.require("forms.checkbox_matrix")
        self.matrix_config.validate_selection(self.default_values)
        value = ";".join(row + ":" + ",".join(cols) for row, cols in self.default_values.items())
        byte_limit(value, 3000)
        return {**super().to_wire(profile), "default": value}


class DatePicker(FormField):
    type: Literal["date"] = "date"
    default: str | None = Field(default=None, repr=False)
    placeholder: str | None = Field(default=None, max_length=150)
    min_date: str | None = Field(default=None, exclude=True)
    max_date: str | None = Field(default=None, exclude=True)

    @field_validator("default", "min_date", "max_date")
    @classmethod
    def expression(cls, value: str | None) -> str | None:
        if value is not None:
            byte_limit(value, 150)
            date_expression(value)
        return value

    def to_wire(self, profile: UIProfile) -> dict[str, Any]:
        profile.require("forms.date")
        result = super().to_wire(profile)
        config = {
            k: v for k, v in (("min_date", self.min_date), ("max_date", self.max_date)) if v is not None
        }
        if profile.server_version in ("11.11.0", "12.0.2"):
            if config:
                profile.require("forms.date_bounds_config")
                result["datetime_config"] = config
        else:
            result.update(config)
        return result


class DateTimePicker(DatePicker):
    type: Literal["datetime"] = "datetime"  # type: ignore[assignment]
    time_interval: int = Field(default=60, ge=1, le=1440, exclude=True)
    location_timezone: str | None = Field(default=None, exclude=True)
    manual_time_entry: bool | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def interval(self) -> Self:
        if 1440 % self.time_interval:
            raise ValueError("time_interval must divide 1440")
        if self.default is not None and "T" in self.default:
            value = datetime.fromisoformat(self.default.replace("Z", "+00:00"))
            if value.utcoffset() is None:
                raise ValueError("Datetime defaults require an explicit timezone offset")
            if value.second or value.microsecond or (value.hour * 60 + value.minute) % self.time_interval:
                raise ValueError("Default time must align with time_interval")
        elif self.default is not None and re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.default):
            raise ValueError("A datetime default requires a time or a relative expression")
        if self.location_timezone is not None and not re.fullmatch(
            r"[A-Za-z0-9_+.-]+(?:/[A-Za-z0-9_+.-]+)*", self.location_timezone
        ):
            raise ValueError("location_timezone must be an IANA zone key")
        return self

    def to_wire(self, profile: UIProfile) -> dict[str, Any]:
        result = super().to_wire(profile)
        if profile.server_version == "11.1.0":
            if self.location_timezone is not None or self.manual_time_entry is not None:
                profile.require("forms.datetime_config")
            result["time_interval"] = self.time_interval
        else:
            profile.require("forms.datetime_config")
            config = result.setdefault("datetime_config", {})
            config["time_interval"] = self.time_interval
            if self.location_timezone is not None:
                profile.require("forms.timezone")
                config["location_timezone"] = self.location_timezone
            if self.manual_time_entry is not None:
                profile.require("forms.manual_time")
                key = "allow_manual_time_entry" if profile.server_version == "11.7.0" else "manual_time_entry"
                config[key] = self.manual_time_entry
        return result


class FileInput(FormField):
    type: Literal["file"] = "file"
    allow_multiple: bool = False
    default: str | None = Field(default=None, repr=False, max_length=300)
    placeholder: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def file_defaults(self) -> Self:
        if self.default:
            ids = [value.strip() for value in self.default.split(",") if value.strip()]
            if not ids or len(ids) > 10 or (not self.allow_multiple and len(ids) > 1):
                raise ValueError("File defaults exceed the declared native file count")
            if any(not re.fullmatch(r"[a-z0-9]{26}", value) for value in ids):
                raise ValueError("File defaults must contain native file IDs")
        return self

    def to_wire(self, profile: UIProfile) -> dict[str, Any]:
        profile.require("forms.file")
        for value in (self.default, self.placeholder):
            if value is not None:
                byte_limit(value, 300)
        return super().to_wire(profile)


class DialogAction(UIModel):
    url: str
    context: dict[str, str] = Field(default_factory=dict, repr=False)

    _url = field_validator("url")(integration_url)


class DialogButton(FormField):
    type: Literal["action_button"] = "action_button"
    action_button: DialogAction

    def to_wire(self, profile: UIProfile) -> dict[str, Any]:
        profile.require("forms.action_button")
        return super().to_wire(profile)


class Collapsible(FormField):
    type: Literal["collapsible"] = "collapsible"
    elements: Annotated[tuple[FormElement, ...], BeforeValidator(sequence)] = Field(
        min_length=1, exclude=True
    )
    collapsed: bool = Field(default=False, exclude=True)
    borderless: bool = Field(default=False, exclude=True)

    def to_wire(self, profile: UIProfile) -> dict[str, Any]:
        profile.require("forms.collapsible")
        return {
            **super().to_wire(profile),
            "collapsible_config": {
                "elements": [e.to_wire(profile) for e in self.elements],
                "collapsed": self.collapsed,
                "borderless": self.borderless,
            },
        }


FormElement = (
    TextInput
    | TextArea
    | Select
    | MultiSelect
    | Checkbox
    | Radio
    | CheckboxGroup
    | CheckboxMatrix
    | DatePicker
    | DateTimePicker
    | FileInput
    | DialogButton
    | Collapsible
)
Collapsible.model_rebuild()


class Form(UIModel):
    callback_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    elements: Annotated[tuple[FormElement, ...], BeforeValidator(sequence)] = ()
    introduction_text: str | None = None
    icon_url: str | None = None
    submit_label: str | None = None
    notify_on_cancel: bool = False
    state: str = Field(default="", repr=False)
    source_url: str | None = None

    @model_validator(mode="after")
    def structure(self) -> Self:
        names: set[str] = set()

        def visit(elements: tuple[FormElement, ...], depth: int = 0) -> None:
            for element in elements:
                if element.name in names:
                    raise ValueError("Form field names must be unique across the entire form")
                names.add(element.name)
                if isinstance(element, Collapsible):
                    if depth >= 3:
                        raise ValueError("Form collapsibles cannot nest beyond three levels")
                    visit(element.elements, depth + 1)
                if isinstance(element, Select) and element.refresh and self.source_url is None:
                    raise ValueError("Refresh fields require a form source_url")

        visit(self.elements)
        if self.icon_url is not None:
            http_url(self.icon_url)
        if self.source_url is not None:
            integration_url(self.source_url)
        return self

    def to_wire(self, profile: UIProfile) -> dict[str, Any]:
        profile.require("forms.basic")
        byte_limit(self.title, 80 if profile.server_version == "12.0.2" else 24)
        result = self.model_dump(mode="json", by_alias=True, exclude_none=True, exclude={"elements"})
        result["elements"] = [e.to_wire(profile) for e in self.elements]
        return result
