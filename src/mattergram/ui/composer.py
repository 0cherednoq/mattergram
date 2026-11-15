"""Declarative persistent composer keyboard backed by the Mattergram Menu plugin."""

from __future__ import annotations

import re
from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, field_validator, model_validator

from .base import UIModel, byte_limit, sequence


def _rows(value: object) -> object:
    if isinstance(value, list):
        return tuple(tuple(row) if isinstance(row, list) else row for row in value)
    return value


class ComposerButton(UIModel):
    """One button that posts ``message`` as the Mattermost user who clicks it."""

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    text: str = Field(min_length=1)
    message: str = Field(min_length=1)
    style: Literal["default", "primary", "danger"] = "default"
    disabled: bool = False

    @field_validator("text")
    @classmethod
    def valid_text(cls, value: str) -> str:
        if value.strip() != value or any(ord(character) < 32 for character in value):
            raise ValueError("Composer button text must be trimmed single-line text")
        return byte_limit(value, 120)

    @field_validator("message")
    @classmethod
    def valid_message(cls, value: str) -> str:
        if value.strip() != value or "\x00" in value:
            raise ValueError("Composer button message must be trimmed and cannot contain NUL")
        return byte_limit(value, 4000)


ComposerRow = Annotated[tuple[ComposerButton, ...], BeforeValidator(sequence)]


class ComposerKeyboard(UIModel):
    """Persistent web/desktop keyboard rendered above a channel composer."""

    schema_version: Literal[1] = 1
    title: str = Field(default="Choose an action", min_length=1)
    rows: Annotated[tuple[ComposerRow, ...], BeforeValidator(_rows)]

    @field_validator("title")
    @classmethod
    def valid_title(cls, value: str) -> str:
        if value.strip() != value or any(ord(character) < 32 for character in value):
            raise ValueError("Composer keyboard title must be trimmed single-line text")
        return byte_limit(value, 120)

    @model_validator(mode="after")
    def valid_layout(self) -> Self:
        if not 1 <= len(self.rows) <= 6:
            raise ValueError("Composer keyboard requires 1-6 rows")
        identifiers: list[str] = []
        for row in self.rows:
            if not 1 <= len(row) <= 4:
                raise ValueError("Each composer keyboard row requires 1-4 buttons")
            identifiers.extend(button.id for button in row)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Composer button IDs must be unique")
        return self

    def to_wire(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class ComposerKeyboardState(UIModel):
    """Plugin-confirmed keyboard state and its monotonically increasing revision."""

    channel_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    keyboard: ComposerKeyboard

    @field_validator("channel_id")
    @classmethod
    def valid_channel_id(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9]+", value):
            raise ValueError("Expected a native Mattermost channel ID")
        return value
