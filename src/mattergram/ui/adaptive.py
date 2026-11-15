"""Author-defined Adaptive Cards subset normalized to native post Blocks."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .actions import Action
from .base import http_url
from .blocks import Block, Button, Column, ColumnSet, Container, Image, Text
from .input_base import (
    Registry,
    UIConversionError,
    array,
    boolean,
    budget,
    choice,
    literal_text,
    native,
    obj,
    string,
)


class _Adaptive:
    def __init__(self, actions: Mapping[str, Action]) -> None:
        self.registry = Registry(actions)
        self.version = (1, 0)

    def introduced(self, data: dict[str, Any], path: str, fields: set[str], minor: int) -> None:
        if data.keys() & fields and self.version < (1, minor):
            raise UIConversionError(path, "Property is newer than the declared Adaptive Card version")

    def action(self, value: Any, path: str) -> Button:
        data = obj(
            value,
            path,
            {
                "type",
                "id",
                "title",
                "url",
                "data",
                "associatedInputs",
                "style",
                "tooltip",
                "isEnabled",
                "mode",
            },
        )
        kind = data.get("type")
        if kind not in ("Action.Submit", "Action.OpenUrl"):
            raise UIConversionError(
                path + ".type", "Unsupported card action; use an explicit native workflow"
            )
        self.introduced(data, path, {"style"}, 2)
        self.introduced(data, path, {"associatedInputs"}, 3)
        self.introduced(data, path, {"tooltip", "isEnabled", "mode"}, 5)
        if "mode" in data and data["mode"] != "primary":
            raise UIConversionError(path + ".mode", "Overflow actions have no native equivalent")
        url = None
        if kind == "Action.OpenUrl":
            if data.keys() & {"data", "associatedInputs"}:
                raise UIConversionError(path, "Submit properties do not apply to an OpenUrl action")
            url = string(data.get("url"), path + ".url")
            try:
                http_url(url)
            except ValueError:
                raise UIConversionError(path + ".url", "Expected an absolute HTTP(S) URL") from None
        else:
            if "url" in data:
                raise UIConversionError(
                    path + ".url", "Submit URL must be supplied through the explicit binding"
                )
            if "associatedInputs" in data:
                choice(data["associatedInputs"], path + ".associatedInputs", {"auto": "auto", "none": "none"})
        identifier, action = self.registry.bind(
            string(data["id"], path + ".id") if "id" in data else None,
            path + ".id",
            url=url,
            external=kind == "Action.Submit",
        )
        if "data" in data:
            self.registry.data(action, data["data"], path + ".data")
        return native(
            Button,
            path,
            text=literal_text(string(data.get("title"), path + ".title")),
            action_id=identifier,
            action=action,
            style=choice(
                data["style"],
                path + ".style",
                {"default": "default", "positive": "primary", "destructive": "danger"},
            )
            if "style" in data
            else None,
            tooltip=string(data["tooltip"], path + ".tooltip") if "tooltip" in data else None,
            disabled=not boolean(data["isEnabled"], path + ".isEnabled") if "isEnabled" in data else None,
        )

    def row(self, value: Any, path: str) -> Container:
        return Container(
            flow="horizontal",
            content=tuple(
                self.action(item, f"{path}[{i}]") for i, item in enumerate(array(value, path, minimum=1))
            ),
        )

    def items(self, value: Any, path: str, depth: int) -> tuple[Block, ...]:
        if depth > 32:
            raise UIConversionError(path, "Native block nesting exceeds 32 levels")
        return tuple(self.item(item, f"{path}[{i}]", depth) for i, item in enumerate(array(value, path)))

    def item(self, value: Any, path: str, depth: int) -> Block:
        data = obj(
            value,
            path,
            {
                "type",
                "text",
                "size",
                "isSubtle",
                "wrap",
                "items",
                "columns",
                "url",
                "altText",
                "width",
                "height",
                "style",
                "horizontalAlignment",
                "actions",
            },
        )
        kind = data.get("type")
        if kind == "TextBlock":
            obj(data, path, {"type", "text", "size", "isSubtle", "wrap"})
            if "wrap" not in data or boolean(data["wrap"], path + ".wrap") is not True:
                raise UIConversionError(
                    path + ".wrap", "TextBlock requires wrap=true; native text cannot retain truncation"
                )
            return native(
                Text,
                path,
                text=string(data.get("text"), path + ".text"),
                size=choice(data["size"], path + ".size", {"Small": "small", "Default": "default"})
                if "size" in data
                else None,
                is_subtle=boolean(data["isSubtle"], path + ".isSubtle") if "isSubtle" in data else None,
            )
        if kind == "Container":
            obj(data, path, {"type", "items"})
            return Container(content=self.items(data.get("items"), path + ".items", depth + 1))
        if kind == "ColumnSet":
            obj(data, path, {"type", "columns"})
            columns = []
            for index, item in enumerate(array(data.get("columns"), path + ".columns", minimum=1)):
                location = f"{path}.columns[{index}]"
                column = obj(item, location, {"type", "width", "items"})
                if column.get("type") != "Column":
                    raise UIConversionError(location + ".type", "ColumnSet only accepts Column elements")
                columns.append(
                    native(
                        Column,
                        location,
                        width=choice(
                            column.get("width", "stretch"),
                            location + ".width",
                            {"auto": "auto", "stretch": "stretch"},
                        ),
                        items=self.items(column.get("items"), location + ".items", depth + 2),
                    )
                )
            return ColumnSet(columns=tuple(columns))
        if kind == "Image":
            obj(
                data,
                path,
                {"type", "url", "altText", "size", "style", "horizontalAlignment", "width", "height"},
            )
            self.introduced(data, path, {"width", "height"}, 1)
            dimensions: dict[str, int] = {}
            for source, target in (("width", "max_width"), ("height", "max_height")):
                if source in data:
                    pixels = data[source]
                    if not isinstance(pixels, str) or not re.fullmatch(r"[1-9][0-9]{0,5}px", pixels):
                        raise UIConversionError(
                            path + "." + source, "Only positive integer pixel dimensions are supported"
                        )
                    dimensions[target] = int(pixels[:-2])
            return native(
                Image,
                path,
                url=string(data.get("url"), path + ".url"),
                alt_text=string(data["altText"], path + ".altText", empty=True)
                if "altText" in data
                else None,
                size=choice(
                    data.get("size", "Auto"),
                    path + ".size",
                    {
                        "Auto": "auto",
                        "Small": "small",
                        "Medium": "medium",
                        "Large": "large",
                        "Stretch": "stretch",
                    },
                ),
                image_style=choice(data["style"], path + ".style", {"default": "default", "person": "person"})
                if "style" in data
                else None,
                horizontal_alignment=choice(
                    data["horizontalAlignment"],
                    path + ".horizontalAlignment",
                    {"Left": "left", "Center": "center", "Right": "right"},
                )
                if "horizontalAlignment" in data
                else None,
                **dimensions,
            )
        if kind == "ActionSet":
            obj(data, path, {"type", "actions"})
            self.introduced({"actions": True}, path, {"actions"}, 2)
            return self.row(data.get("actions"), path + ".actions")
        raise UIConversionError(
            path + ".type", "Unsupported card element; post inputs are not native form fields"
        )

    def card(self, value: Any, path: str) -> tuple[Block, ...]:
        data = obj(value, path, {"$schema", "type", "version", "body", "actions"})
        if data.get("type") != "AdaptiveCard":
            raise UIConversionError(path + ".type", "Expected AdaptiveCard")
        version = string(data.get("version"), path + ".version")
        if not re.fullmatch(r"1\.[0-5]", version):
            raise UIConversionError(path + ".version", "Supported declaration versions are 1.0 through 1.5")
        self.version = (1, int(version[-1]))
        if "$schema" in data and data["$schema"] not in (
            "http://adaptivecards.io/schemas/adaptive-card.json",
            "https://adaptivecards.io/schemas/adaptive-card.json",
        ):
            raise UIConversionError(path + ".$schema", "Unknown schema identifier")
        content = self.items(data.get("body", []), path + ".body", 0)
        if "actions" in data:
            content += (self.row(data["actions"], path + ".actions"),)
        return content


def from_adaptive_cards(
    cards: Sequence[Mapping[str, Any]], *, actions: Mapping[str, Action] | None = None
) -> tuple[Block, ...]:
    """Normalize the documented subset; reject dropped data, fields and layout.

    Action.Submit is an explicitly bound External callback with no in-post inputs.
    Native Form declarations are required for actual data entry. The returned
    declarations still need an explicit UIProfile when compiled/sent.
    """
    budget(cards)
    adapter = _Adaptive(actions or {})
    result = tuple(
        block
        for i, card in enumerate(array(cards, "$.cards"))
        for block in adapter.card(card, f"$.cards[{i}]")
    )
    adapter.registry.finish()
    return result
