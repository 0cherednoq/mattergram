"""Carry compatible entered values into replacement forms without overriding intent."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from datetime import date, datetime
from typing import Any, TypeVar

from pydantic import ValidationError

from .base import UIModel
from .form_values import MISSING, FormValues, input_fields
from .forms import (
    CheckboxGroup,
    CheckboxMatrix,
    Collapsible,
    FileInput,
    Form,
    FormElement,
    MultiSelect,
    TextInput,
)

Model = TypeVar("Model", bound=UIModel)


def _copy_with(model: Model, updates: dict[str, Any]) -> Model:
    cls = type(model)
    values = {name: deepcopy(getattr(model, name)) for name in cls.model_fields}
    validated = cls.model_validate({**values, **updates})
    # Carrying a value does not make its default an explicit application choice.
    # Keep this metadata so reuse of event.form carries the next edited value.
    return cls.model_construct(
        _fields_set=model.model_fields_set.copy(),
        **{name: getattr(validated, name) for name in cls.model_fields},
    )


def preserve_form_values(replacement: Form, values: FormValues, *, previous_forms: Sequence[Form]) -> Form:
    """Prefill same-name/type fields whose defaults the application omitted.

    Explicit None/empty/false defaults win. Incompatible target options/config
    are not carried; no arbitrary choice or lossy conversion is made. Global
    file IDs cannot establish a field association and are never turned into a
    FileInput default here.
    """
    previous = {name: field for form in previous_forms for name, field in input_fields(form).items()}

    def visit(element: FormElement) -> FormElement:
        if isinstance(element, Collapsible):
            return _copy_with(element, {"elements": tuple(visit(child) for child in element.elements)})
        old = previous.get(element.name)
        if old is None or type(old) is not type(element) or isinstance(element, FileInput):
            return element.model_copy(deep=True)
        key = (
            "default_values"
            if isinstance(element, (MultiSelect, CheckboxGroup, CheckboxMatrix))
            else "default"
        )
        if key not in type(element).model_fields or key in element.model_fields_set:
            return element.model_copy(deep=True)
        value = values.get(element.name, MISSING)
        if value is MISSING or value is None:
            return element.model_copy(deep=True)
        if isinstance(element, TextInput):
            if not isinstance(old, TextInput) or old.subtype != element.subtype or not isinstance(value, str):
                return element.model_copy(deep=True)
            if not element.min_length <= len(value.encode("utf-16-le")) // 2 <= element.max_length:
                return element.model_copy(deep=True)
        if isinstance(value, (datetime, date)):
            value = value.isoformat()
        try:
            result = _copy_with(element, {key: value})
            return result
        except (ValidationError, ValueError, TypeError):
            return element.model_copy(deep=True)

    result = _copy_with(replacement, {"elements": tuple(visit(item) for item in replacement.elements)})
    if not isinstance(result, Form):
        raise TypeError("Expected a native replacement form")
    return result
