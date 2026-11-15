"""One action definition shared by native blocks and legacy attachments."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Self

from pydantic import BeforeValidator, Field, field_validator, model_validator

from mattergram.exceptions import UnsupportedCapabilityError
from mattergram.http.auth import CallbackSigner

from .base import UIModel, http_url, integration_url, sequence
from .capabilities import UIProfile
from .forms import Option, Options, unique_options


@dataclass(frozen=True)
class ActionContext:
    signer: CallbackSigner
    channel_id: str
    post_id: str | None = None
    user_id: str | None = None
    team_id: str | None = None
    thread_id: str | None = None
    ttl: float = 900


class External(UIModel):
    type: Literal["external"] = "external"
    url: str
    context: dict[str, Any] = Field(default_factory=dict, repr=False)
    data: str | None = Field(default=None, exclude=True, repr=False)
    query: dict[str, str] = Field(default_factory=dict)

    _url = field_validator("url")(integration_url)

    @model_validator(mode="after")
    def context_or_data(self) -> Self:
        if self.data is not None and self.context:
            raise ValueError("Use signed callback data or explicit native context, not both")
        return self

    def to_wire(self, binding: ActionContext | None, options: tuple[Option, ...] = ()) -> dict[str, Any]:
        result = self.model_dump(mode="json", exclude={"data"})
        if self.data is not None:
            if binding is None:
                raise ValueError("Application callback data requires an explicit signing ActionContext")
            result["context"] = binding.signer.seal(
                self.data,
                channel_id=binding.channel_id,
                post_id=binding.post_id,
                user_id=binding.user_id,
                team_id=binding.team_id,
                thread_id=binding.thread_id,
                ttl=binding.ttl,
                allowed_options=[o.value for o in options] if options else None,
            )
        return result


class OpenURL(UIModel):
    type: Literal["openURL"] = "openURL"
    url: str = Field(min_length=1)
    query: dict[str, str] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def destination(cls, value: str) -> str:
        if value.startswith("/") and not value.startswith("//") and not any(ord(c) <= 32 for c in value):
            return value
        return integration_url(value)


Action = External | OpenURL


def action_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("Action ID must be 1-64 ASCII letters, digits, underscores or hyphens")
    return value


def button_style(value: str | None) -> str | None:
    if (
        value is not None
        and value not in ("default", "primary", "success", "good", "warning", "danger")
        and not re.fullmatch(r"#[0-9a-fA-F]{6}", value)
    ):
        raise ValueError("Expected a native semantic style or #RRGGBB")
    return value


def _require_legacy_route(identifier: str | None, profile: UIProfile | None) -> None:
    # The 11.1/11.7 route is [A-Za-z0-9]+ even though their model accepts more.
    # Never rename an application ID or infer a newer server from the payload.
    if identifier is not None and ("_" in identifier or "-" in identifier):
        if profile is None or profile.server_version not in ("11.11.0", "12.0.2"):
            raise UnsupportedCapabilityError(
                "Legacy action IDs with '_' or '-' require an explicit referenced 11.11/12 server profile; "
                "use an alphanumeric ID or let Mattermost issue one"
            )


class Button(UIModel):
    type: Literal["button"] = "button"
    id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1)
    disabled: bool = False
    style: str | None = None
    action: External = Field(exclude=True, repr=False)

    _style = field_validator("style")(button_style)

    def to_wire(
        self, binding: ActionContext | None = None, *, profile: UIProfile | None = None
    ) -> dict[str, Any]:
        _require_legacy_route(self.id, profile)
        integration = self.action.to_wire(binding)
        if integration.pop("query", {}):
            raise ValueError("Legacy actions cannot preserve a Blocks query map")
        integration.pop("type")
        return {**self.model_dump(exclude_none=True), "integration": integration}


class Select(UIModel):
    type: Literal["select"] = "select"
    id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1)
    disabled: bool = False
    style: str | None = None
    options: Options = ()
    data_source: Literal["users", "channels"] | None = None
    default_option: str | None = None
    action: External = Field(exclude=True, repr=False)

    _style = field_validator("style")(button_style)

    @model_validator(mode="after")
    def choices(self) -> Self:
        if self.data_source is not None and self.options:
            raise ValueError("Choose static options or a native data source")
        if self.data_source is None:
            unique_options(self.options)
            if self.default_option is not None and self.default_option not in {o.value for o in self.options}:
                raise ValueError("Default option is not in the static options")
        return self

    def to_wire(
        self, binding: ActionContext | None = None, *, profile: UIProfile | None = None
    ) -> dict[str, Any]:
        _require_legacy_route(self.id, profile)
        integration = self.action.to_wire(binding, self.options)
        if integration.pop("query", {}):
            raise ValueError("Legacy actions cannot preserve a Blocks query map")
        integration.pop("type")
        return {**self.model_dump(mode="json", exclude_none=True), "integration": integration}


class Actions(UIModel):
    items: Annotated[tuple[Button | Select, ...], BeforeValidator(sequence)] = ()

    @model_validator(mode="after")
    def identifiers(self) -> Self:
        ids = [a.id for a in self.items if a.id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("Legacy action IDs must be unique")
        return self


class AttachmentField(UIModel):
    title: str
    value: str | int | None
    short: bool = False


class Attachment(UIModel):
    id: int | None = None
    fallback: str | None = None
    color: str | None = None
    pretext: str | None = None
    author_name: str | None = None
    author_link: str | None = None
    author_icon: str | None = None
    title: str | None = None
    title_link: str | None = None
    text: str | None = None
    fields: Annotated[tuple[AttachmentField, ...], BeforeValidator(sequence)] = ()
    image_url: str | None = None
    thumb_url: str | None = None
    footer: str | None = None
    footer_icon: str | None = None
    ts: str | int | None = None
    actions: Actions | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def links(self) -> Self:
        if self.author_link is not None and not self.author_name:
            raise ValueError("author_link requires author_name")
        if self.title_link is not None and not self.title:
            raise ValueError("title_link requires title")
        for value in (
            self.author_link,
            self.author_icon,
            self.title_link,
            self.image_url,
            self.thumb_url,
            self.footer_icon,
        ):
            if value is not None:
                http_url(value)
        if (
            self.color is not None
            and self.color not in ("good", "warning", "danger")
            and not re.fullmatch(r"#[0-9a-fA-F]{6}", self.color)
        ):
            raise ValueError("Invalid attachment color")
        return self

    def to_wire(
        self, binding: ActionContext | None = None, *, profile: UIProfile | None = None
    ) -> dict[str, Any]:
        result = self.model_dump(mode="json", exclude_none=True)
        if self.actions is not None:
            result["actions"] = [a.to_wire(binding, profile=profile) for a in self.actions.items]
        return result
