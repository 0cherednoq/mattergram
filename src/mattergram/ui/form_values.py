"""Schema-directed native submission parsing; defaults never replace missing input."""

from __future__ import annotations

import math
import re
from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal

from .forms import (
    Checkbox,
    CheckboxGroup,
    CheckboxMatrix,
    Collapsible,
    DatePicker,
    DateTimePicker,
    DialogButton,
    FileInput,
    Form,
    FormElement,
    MultiSelect,
    Radio,
    Select,
    TextArea,
    TextInput,
)


class Missing(Enum):
    VALUE = "missing"


MISSING = Missing.VALUE


def input_fields(form: Form) -> dict[str, FormElement]:
    result: dict[str, FormElement] = {}

    def visit(elements: Sequence[FormElement]) -> None:
        for element in elements:
            if isinstance(element, Collapsible):
                visit(element.elements)
            elif not isinstance(element, DialogButton):
                result[element.name] = element

    visit(form.elements)
    return result


@dataclass(frozen=True, repr=False)
class FormValues(Mapping[str, Any]):
    _values: dict[str, Any] = field(repr=False)
    _raw: dict[str, Any] = field(repr=False)
    errors: Mapping[str, str] = field(repr=False)
    file_ids: tuple[str, ...] = field(default=(), repr=False)

    def __getitem__(self, key: str) -> Any:
        return deepcopy(self._values[key])

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    @property
    def raw(self) -> Mapping[str, Any]:
        """Sensitive wire values; explicitly accessed, never included in repr."""
        return deepcopy(self._raw)

    def __repr__(self) -> str:
        return f"FormValues(fields={len(self)}, errors={len(self.errors)})"


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ValueError("Expected an array of selected values")
    if len(set(value)) != len(value):
        raise ValueError("Repeated selected value")
    return value.copy()


def _lookup_option(value: Any) -> Any:
    # Native lookup conversion can retain Apps Form option objects. Labels are
    # display metadata, never authoritative values. Do not enable this shape
    # for ordinary submissions or accept arbitrary objects with a value key.
    if isinstance(value, dict):
        if set(value) != {"label", "value"} or not all(isinstance(v, str) for v in value.values()):
            raise ValueError("Invalid native lookup option object")
        return value["value"]
    return value


def _parse(element: FormElement, value: Any) -> Any:
    if isinstance(element, (TextInput, TextArea)):
        if element.subtype == "number" and type(value) in (int, float):
            if not math.isfinite(value):
                raise ValueError("Expected finite numeric text")
            value = str(value)
        if not isinstance(value, str):
            raise ValueError("Expected text")
        # The client counts JavaScript UTF-16 code units for input length.
        length = len(value.encode("utf-16-le")) // 2
        if not element.min_length <= length <= element.max_length:
            raise ValueError("Text length is outside the declared range")
        return value
    if isinstance(element, Checkbox):
        if type(value) is bool:
            return value
        if value in ("true", "false"):
            return value == "true"
        raise ValueError("Expected a boolean or an explicit true/false string")
    if isinstance(element, (MultiSelect, CheckboxGroup)):
        selected = _strings(value)
        if element.options and set(selected) - {o.value for o in element.options}:
            raise ValueError("Unknown selected option")
        return selected
    if isinstance(element, (Select, Radio)):
        if not isinstance(value, str):
            raise ValueError("Expected a single selected value")
        if element.options and value not in {o.value for o in element.options}:
            raise ValueError("Unknown selected option")
        return value
    if isinstance(element, CheckboxMatrix):
        rows: dict[str, tuple[str, ...]] = {}
        for entry in _strings(value):
            row, separator, columns = entry.partition(":")
            if not separator or not row or row in rows:
                raise ValueError("Invalid or repeated matrix row")
            rows[row] = tuple(column.strip() for column in columns.split(","))
        element.matrix_config.validate_selection(rows)
        return {row: list(columns) for row, columns in rows.items()}
    if isinstance(element, DateTimePicker):
        if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value
        ):
            raise ValueError("Expected a timezone-aware RFC3339 datetime")
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError("Datetime offset is required")
        return result
    if isinstance(element, DatePicker):
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Expected an ISO date")
        return date.fromisoformat(value)
    if isinstance(element, FileInput):
        if not isinstance(value, str):
            raise ValueError("Expected the native file field string")
        # Keep field wire data. Top-level file_ids remains the confirmed global
        # source; do not invent an association to an individual file field.
        return value
    raise ValueError("This element is not a native input")


def parse_form_values(
    form: Form,
    submission: Mapping[str, Any],
    *,
    file_ids: Sequence[str] = (),
    prior_forms: Sequence[Form] = (),
    partial: bool = False,
    source: Literal["submission", "lookup", "refresh"] = "submission",
) -> FormValues:
    if source not in ("submission", "lookup", "refresh"):
        raise ValueError("Unknown native form value source")
    if not isinstance(submission, Mapping) or not all(isinstance(k, str) for k in submission):
        raise TypeError("Submission must be a string-keyed mapping")
    fields: dict[str, FormElement] = {}
    for source_form in (*prior_forms, form):
        fields.update(input_fields(source_form))
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name, element in fields.items():
        value = submission.get(name, MISSING)
        values[name] = value
        if value is MISSING or value is None or value == "" or value == []:
            if not partial and not element.optional:
                if not isinstance(element, FileInput) or not file_ids:
                    errors[name] = "This field is required"
            continue
        try:
            if source == "lookup" and isinstance(element, Select):
                value = (
                    [_lookup_option(v) for v in value] if isinstance(value, list) else _lookup_option(value)
                )
            values[name] = _parse(element, value)
        except (ValueError, TypeError, OverflowError, UnicodeError):
            errors[name] = "Invalid value for this field"
    return FormValues(deepcopy(values), deepcopy(dict(submission)), MappingProxyType(errors), tuple(file_ids))
