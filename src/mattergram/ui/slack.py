"""Author-defined Slack Block Kit subset normalized to native declarations.

This is not a Slack interaction endpoint or a vendor-source port. Native actions
are explicitly bound by the application and compiled by the ordinary UI compiler.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .actions import Action
from .base import http_url
from .blocks import Block, Button, Column, ColumnSet, Container, Divider, Image, StaticSelect, Text
from .forms import Option
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


def _entities(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _mrkdwn(text: str, path: str, depth: int = 0) -> str:
    """Common explicit formatting; workspace-dependent tokens require a renderer."""
    if depth > 32 or len(text) > 16000:
        raise UIConversionError(path, "mrkdwn exceeds the 32-level or 16000-character limit")
    tokens = re.compile(r"```[\s\S]*?```|`[^`\n]+`|<[^>]*>|https?://[^\s<>]+|([*_~])([^\n]+?)\1")
    result = []
    offset = 0

    def plain(value: str) -> str:
        if re.search(r"(?<!\w)[@#][A-Za-z]", value):
            raise UIConversionError(path, "Workspace references require an explicit mrkdwn renderer")
        if "`" in value or "<" in value:
            raise UIConversionError(path, "Unsupported or unclosed mrkdwn token")
        return "\n".join(
            "> " + literal_text(_entities(line[2:]))
            if line.startswith("> ")
            else literal_text(_entities(line))
            for line in value.split("\n")
        )

    for match in tokens.finditer(text):
        result.append(plain(text[offset : match.start()]))
        token = match.group()
        if token.startswith("`"):
            result.append(_entities(token))
        elif token.startswith(("<", "http://", "https://")):
            destination, _, label = (token[1:-1] if token.startswith("<") else token).partition("|")
            destination, label = _entities(destination), _entities(label)
            try:
                http_url(destination)
            except ValueError:
                raise UIConversionError(
                    path, "Slack IDs, dates and non-HTTP links require a renderer"
                ) from None
            if any(char in destination for char in "<>"):
                raise UIConversionError(path, "Unsupported link destination")
            result.append(f"[{literal_text(label or destination)}](<{destination}>)")
        else:
            marker = {"*": "**", "_": "_", "~": "~~"}[token[0]]
            result.append(marker + _mrkdwn(token[1:-1], path, depth + 1) + marker)
        offset = match.end()
    result.append(plain(text[offset:]))
    return "".join(result)


class _Slack:
    def __init__(self, actions: Mapping[str, Action], renderer: Callable[[str], str] | None) -> None:
        self.registry = Registry(actions)
        self.renderer = renderer

    def text(self, value: Any, path: str, *, formatted: bool = True) -> str:
        data = obj(value, path, {"type", "text", "emoji", "verbatim"})
        kind = choice(data.get("type"), path + ".type", {"plain_text": "plain", "mrkdwn": "markdown"})
        text = string(data.get("text"), path + ".text")
        if kind == "plain":
            text = _entities(text)
            if "verbatim" in data:
                raise UIConversionError(path, "verbatim only applies to mrkdwn")
            if "emoji" in data:
                boolean(data["emoji"], path + ".emoji")
            # Different emoji catalogues cannot be inferred from a server version.
            if re.search(r":[A-Za-z0-9_+-]+:", text):
                raise UIConversionError(path, "Emoji shortcode semantics require native text")
            return literal_text(text) if formatted else text
        if not formatted or "emoji" in data:
            raise UIConversionError(path, "This source position requires plain_text")
        if "verbatim" in data:
            boolean(data["verbatim"], path + ".verbatim")
        if self.renderer is not None:
            try:
                rendered = self.renderer(text)
            except Exception:
                raise UIConversionError(path, "Application mrkdwn renderer failed") from None
            return string(rendered, path)
        return _mrkdwn(text, path)

    def option(self, value: Any, path: str) -> Option:
        data = obj(value, path, {"text", "value"})
        return native(
            Option,
            path,
            text=self.text(data.get("text"), path + ".text", formatted=False),
            value=string(data.get("value"), path + ".value"),
        )

    def image(self, value: Any, path: str, *, size: str) -> Image:
        data = obj(value, path, {"type", "image_url", "alt_text", "title"})
        return native(
            Image,
            path,
            url=string(data.get("image_url"), path + ".image_url"),
            alt_text=string(data.get("alt_text"), path + ".alt_text"),
            title=self.text(data["title"], path + ".title", formatted=False) if "title" in data else None,
            size=size,
        )

    def interactive(self, value: Any, path: str) -> Button | StaticSelect:
        data = obj(
            value,
            path,
            {
                "type",
                "action_id",
                "text",
                "style",
                "url",
                "value",
                "options",
                "placeholder",
                "initial_option",
            },
        )
        kind = data.get("type")
        if kind == "button":
            obj(data, path, {"type", "action_id", "text", "style", "url", "value"})
            url = string(data["url"], path + ".url") if "url" in data else None
            if url is not None:
                try:
                    http_url(url)
                except ValueError:
                    raise UIConversionError(path + ".url", "Expected an absolute HTTP(S) URL") from None
            identifier, action = self.registry.bind(
                string(data["action_id"], path + ".action_id") if "action_id" in data else None,
                path + ".action_id",
                url=url,
                external=url is None,
            )
            if "value" in data:
                self.registry.data(action, string(data["value"], path + ".value"), path + ".value")
            return native(
                Button,
                path,
                text=literal_text(self.text(data.get("text"), path + ".text", formatted=False)),
                action_id=identifier,
                action=action,
                style=choice(data["style"], path + ".style", {"primary": "primary", "danger": "danger"})
                if "style" in data
                else None,
            )
        if kind != "static_select":
            raise UIConversionError(path + ".type", "Unsupported interactive element")
        obj(data, path, {"type", "action_id", "options", "placeholder", "initial_option"})
        identifier, action = self.registry.bind(data.get("action_id"), path + ".action_id", external=True)
        options = tuple(
            self.option(item, f"{path}.options[{i}]")
            for i, item in enumerate(array(data.get("options"), path + ".options", minimum=1))
        )
        initial = (
            self.option(data["initial_option"], path + ".initial_option")
            if "initial_option" in data
            else None
        )
        if initial is not None and initial not in options:
            raise UIConversionError(
                path + ".initial_option", "Initial option must match an entire declared option"
            )
        return native(
            StaticSelect,
            path,
            action_id=identifier,
            action=action,
            options=options,
            placeholder=self.text(data.get("placeholder"), path + ".placeholder", formatted=False),
            initial_option=initial.value if initial is not None else None,
        )

    def block(self, value: Any, path: str) -> Block:
        data = obj(
            value, path, {"type", "text", "fields", "accessory", "elements", "image_url", "alt_text", "title"}
        )
        kind = data.get("type")
        if kind == "divider":
            obj(data, path, {"type"})
            return Divider()
        if kind == "image":
            return self.image(data, path, size="large")
        if kind == "header":
            obj(data, path, {"type", "text"})
            text = self.text(data.get("text"), path + ".text", formatted=False)
            if "\n" in text:
                raise UIConversionError(path + ".text", "Multiline header is not supported")
            return Text(text="# " + literal_text(text))
        if kind == "markdown":
            obj(data, path, {"type", "text"})
            return Text(text=self.text({"type": "mrkdwn", "text": data.get("text")}, path + ".text"))
        if kind == "actions":
            obj(data, path, {"type", "elements"})
            return Container(
                flow="horizontal",
                content=tuple(
                    self.interactive(item, f"{path}.elements[{i}]")
                    for i, item in enumerate(
                        array(data.get("elements"), path + ".elements", minimum=1, maximum=25)
                    )
                ),
            )
        if kind != "section":
            raise UIConversionError(path + ".type", "Unsupported Block Kit block")
        obj(data, path, {"type", "text", "fields", "accessory"})
        content: list[Block] = []
        if "text" in data:
            content.append(Text(text=self.text(data["text"], path + ".text")))
        if "fields" in data:
            fields = [
                Text(text=self.text(item, f"{path}.fields[{i}]"))
                for i, item in enumerate(array(data["fields"], path + ".fields", minimum=1, maximum=10))
            ]
            for index in range(0, len(fields), 2):
                pair = fields[index : index + 2]
                content.append(
                    ColumnSet(columns=tuple(Column(width="stretch", items=(item,)) for item in pair))
                    if len(pair) == 2
                    else pair[0]
                )
        if not content:
            raise UIConversionError(path, "Section requires text or fields")
        if "accessory" in data:
            accessory = data["accessory"]
            block = (
                self.image(accessory, path + ".accessory", size="small")
                if isinstance(accessory, dict) and accessory.get("type") == "image"
                else self.interactive(accessory, path + ".accessory")
            )
            content = [
                ColumnSet(
                    columns=(
                        Column(width="stretch", items=tuple(content)),
                        Column(width="auto", items=(block,)),
                    )
                )
            ]
        return Container(content=tuple(content))


def from_slack_blocks(
    blocks: Sequence[Mapping[str, Any]],
    *,
    actions: Mapping[str, Action] | None = None,
    mrkdwn_renderer: Callable[[str], str] | None = None,
) -> tuple[Block, ...]:
    """Convert the documented subset; unknown properties fail before HTTP.

    Pass the returned native declarations to ``Bot.send_message(blocks=...)``.
    A UIProfile is still required by the native compiler. Explicit renderers own
    workspace-ID, mention, date and any additional Slack text translation.
    """
    budget(blocks)
    adapter = _Slack(actions or {}, mrkdwn_renderer)
    result = tuple(adapter.block(item, f"$.blocks[{i}]") for i, item in enumerate(array(blocks, "$.blocks")))
    adapter.registry.finish()
    return result
