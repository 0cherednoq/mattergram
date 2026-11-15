"""Typed native mm_blocks tree and one reconciled action registry compiler."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from hashlib import sha256
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import BeforeValidator, Field, field_validator, model_validator

from .actions import Action, ActionContext, Actions, Attachment, External, action_id, button_style
from .base import UIModel, http_url, sequence
from .capabilities import UIProfile
from .forms import Options, unique_options

Gap = Literal["none", "small", "medium", "large", "xlarge"]


class Text(UIModel):
    type: Literal["text"] = "text"
    text: str
    is_subtle: bool | None = None
    size: Literal["small", "default"] | None = None


class Image(UIModel):
    type: Literal["image"] = "image"
    url: str
    alt_text: str | None = None
    title: str | None = None
    size: Literal["auto", "xsmall", "small", "medium", "large", "stretch"] | None = None
    max_width: int | None = Field(default=None, gt=0)
    max_height: int | None = Field(default=None, gt=0)
    image_style: Literal["default", "person"] | None = None
    horizontal_alignment: Literal["left", "center", "right"] | None = None

    _url = field_validator("url")(http_url)


class Divider(UIModel):
    type: Literal["divider"] = "divider"


class Button(UIModel):
    type: Literal["button"] = "button"
    text: str = Field(min_length=1)
    action: Action = Field(exclude=True, repr=False)
    action_id: str | None = None
    style: str | None = None
    tooltip: str | None = None
    disabled: bool | None = None
    query: dict[str, str] = Field(default_factory=dict)

    _style = field_validator("style")(button_style)


class StaticSelect(UIModel):
    type: Literal["static_select"] = "static_select"
    action: Action = Field(exclude=True, repr=False)
    action_id: str | None = None
    placeholder: str
    options: Options = ()
    initial_option: str | None = None
    disabled: bool | None = None
    data_source: Literal["users", "channels"] | None = None
    query: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def choices(self) -> Self:
        if self.data_source is not None and self.options:
            raise ValueError("Choose a native data source or static options")
        if self.data_source is None:
            unique_options(self.options)
            if self.initial_option is not None and self.initial_option not in {o.value for o in self.options}:
                raise ValueError("Initial option is not in the static options")
        return self


class Container(UIModel):
    type: Literal["container"] = "container"
    content: Annotated[tuple[Block, ...], BeforeValidator(sequence)]
    border: bool | None = None
    accent_color: str | None = None
    flow: Literal["horizontal", "vertical"] | None = None
    gap: Gap | None = None
    background: Literal["none", "gray"] | None = None
    max_height: Literal["none", "small", "medium", "large"] | None = None


class Collapsible(UIModel):
    type: Literal["collapsible"] = "collapsible"
    header: Annotated[tuple[Block, ...], BeforeValidator(sequence)]
    content: Annotated[tuple[Block, ...], BeforeValidator(sequence)]
    collapsed: bool | None = None


class Column(UIModel):
    type: Literal["column"] = "column"
    items: Annotated[tuple[Block, ...], BeforeValidator(sequence)]
    width: Literal["auto", "stretch"] | None = None
    gap: Gap | None = None


class ColumnSet(UIModel):
    type: Literal["column_set"] = "column_set"
    columns: Annotated[tuple[Column, ...], BeforeValidator(sequence)]
    gap: Gap | None = None


Block = Text | Image | Divider | Button | StaticSelect | Container | Collapsible | Column | ColumnSet
for _model in (Container, Collapsible, Column, ColumnSet):
    _model.model_rebuild()


def _query(values: dict[str, str]) -> None:
    if len(values) > 50 or any(len(k) > 128 or len(v) > 2048 for k, v in values.items()):
        raise ValueError("Blocks query exceeds native size limits")


def compile_post_ui(
    *,
    blocks: Sequence[Block] | None = None,
    actions: Actions | None = None,
    attachments: Sequence[Attachment] | None = None,
    profile: UIProfile | None = None,
    context: ActionContext | None = None,
    markdown_actions: dict[str, Action] | None = None,
    message: str = "",
) -> dict[str, Any]:
    """Compile one tree/registry; no silent conversion that loses UI semantics."""
    props: dict[str, Any] = {}
    legacy_ids = [
        a.id
        for group in ([attachment.actions for attachment in attachments or ()] + [actions])
        if group is not None
        for a in group.items
        if a.id is not None
    ]
    if len(legacy_ids) != len(set(legacy_ids)):
        raise ValueError("Legacy action IDs must be unique across the entire post")
    if attachments is not None:
        props["attachments"] = [a.to_wire(context, profile=profile) for a in attachments]
    if actions is not None:
        props.setdefault("attachments", []).append(
            {"actions": [a.to_wire(context, profile=profile) for a in actions.items]}
        )
    if blocks is None and not markdown_actions:
        json.dumps(props, allow_nan=False)
        return props
    if profile is None:
        raise ValueError("Blocks require an explicit server/client UIProfile")
    profile.require("blocks")
    registry: dict[str, dict[str, Any]] = {}
    declared = dict(markdown_actions or {})
    referenced: set[str] = set()
    block_count = 0
    text_count = 0

    def register(identifier: str, action: Action, options: Options | tuple[()] = ()) -> None:
        action_id(identifier)
        _query(action.query)
        rendered = (
            action.to_wire(context, options)
            if isinstance(action, External)
            else action.model_dump(mode="json")
        )
        if identifier in registry:
            raise ValueError("Duplicate block/markdown action ID")
        registry[identifier] = rendered

    def markdown(text: str) -> None:
        for destination in re.findall(r"mmaction://[^\s)<>]+", text):
            identifier = urlsplit(destination).netloc
            action_id(identifier)
            referenced.add(identifier)

    markdown(message)

    def render(
        items: Sequence[Block], depth: int = 0, *, column_set: bool = False, path: str = "b"
    ) -> list[dict[str, Any]]:
        nonlocal block_count, text_count
        if depth > 32:
            raise ValueError("Block nesting exceeds 32 levels")
        result = []
        for index, item in enumerate(items):
            if not isinstance(
                item, (Text, Image, Divider, Button, StaticSelect, Container, Collapsible, Column, ColumnSet)
            ):
                raise TypeError("Expected a native Block declaration")
            block_count += 1
            if block_count > 100:
                raise ValueError("A post cannot contain more than 100 nested blocks")
            location = f"{path}_{index}"
            wire = item.model_dump(
                mode="json",
                exclude_none=True,
                exclude={"action_id", "content", "header", "items", "columns"},
            )
            if isinstance(item, (Text, Button)):
                text_count += len(item.text)
                markdown(item.text)
            if text_count > 16000:
                raise ValueError("Combined block/button text exceeds 16000 characters")
            if isinstance(item, (Button, StaticSelect)):
                _query(item.query)
                identifier = item.action_id or "a_" + sha256(location.encode()).hexdigest()[:24]
                register(identifier, item.action, item.options if isinstance(item, StaticSelect) else ())
                wire["action_id"] = identifier
            if isinstance(item, (Container, Collapsible)):
                wire["content"] = render(item.content, depth + 1, path=location + "c")
            if isinstance(item, Collapsible):
                wire["header"] = render(item.header, depth + 1, path=location + "h")
            if isinstance(item, ColumnSet):
                wire["columns"] = render(item.columns, depth + 1, column_set=True, path=location + "s")
            if isinstance(item, Column):
                if not column_set:
                    raise ValueError("Column is only valid directly inside ColumnSet")
                wire["items"] = render(item.items, depth + 1, path=location + "i")
            result.append(wire)
        return result

    props["mm_blocks"] = render(blocks or ())
    for identifier, action in declared.items():
        register(identifier, action)
    if len(registry) > 50:
        raise ValueError("Mattermost Blocks cannot register more than 50 actions")
    missing = referenced - set(registry)
    unused = set(declared) - referenced
    if missing or unused:
        raise ValueError("Missing or unused markdown action registry entries")
    props["mm_blocks_actions"] = registry
    json.dumps(props, allow_nan=False)
    return props
