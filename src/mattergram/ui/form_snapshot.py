"""Lossless JSON snapshots of form declarations, including non-wire defaults."""

from __future__ import annotations

import json
from typing import Any

from mattergram.exceptions import StorageDataError

from . import forms
from .base import UIModel

_CLASSES = {
    cls.__name__: cls
    for cls in (
        forms.Form,
        forms.Option,
        forms.TextInput,
        forms.TextArea,
        forms.Select,
        forms.MultiSelect,
        forms.Checkbox,
        forms.Radio,
        forms.CheckboxGroup,
        forms.CheckboxMatrix,
        forms.MatrixConfig,
        forms.DatePicker,
        forms.DateTimePicker,
        forms.FileInput,
        forms.DialogButton,
        forms.DialogAction,
        forms.Collapsible,
    )
}


def snapshot_form(form: forms.Form) -> str:
    def encode(value: Any) -> Any:
        if isinstance(value, UIModel):
            return {
                "$ui": type(value).__name__,
                "fields": {name: encode(getattr(value, name)) for name in type(value).model_fields},
                "fields_set": sorted(value.model_fields_set),
            }
        if isinstance(value, (tuple, list)):
            return [encode(v) for v in value]
        if isinstance(value, dict):
            return {key: encode(v) for key, v in value.items()}
        return value

    return json.dumps(encode(form), ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def restore_form(snapshot: str) -> forms.Form:
    def decode(value: Any) -> Any:
        if isinstance(value, dict):
            if "$ui" in value and isinstance(value.get("fields"), dict):
                cls = _CLASSES[value["$ui"]]
                if value.keys() - {"$ui", "fields", "fields_set"}:
                    raise ValueError("Unknown snapshot metadata")
                restored = cls.model_validate({name: decode(v) for name, v in value["fields"].items()})
                field_set = value.get("fields_set")
                if field_set is None:  # Previous snapshots conservatively retain all explicit defaults.
                    return restored
                if not isinstance(field_set, list) or any(not isinstance(v, str) for v in field_set):
                    raise ValueError("Invalid snapshot field metadata")
                if len(set(field_set)) != len(field_set) or set(field_set) - cls.model_fields.keys():
                    raise ValueError("Invalid snapshot field metadata")
                return cls.model_construct(
                    _fields_set=set(field_set), **{name: getattr(restored, name) for name in cls.model_fields}
                )
            return {key: decode(v) for key, v in value.items()}
        if isinstance(value, list):
            return [decode(v) for v in value]
        return value

    try:
        restored = decode(json.loads(snapshot))
        if not isinstance(restored, forms.Form):
            raise ValueError
        return restored
    except (ValueError, TypeError, KeyError, RecursionError):
        raise StorageDataError("Invalid stored form declaration") from None
