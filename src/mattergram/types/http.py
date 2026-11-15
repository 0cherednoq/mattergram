from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from pydantic import ConfigDict, Field, PrivateAttr, SecretStr, field_validator

from mattergram.exceptions import ResponseStateError

from .base import Event, Identifier, User, WireModel

if TYPE_CHECKING:
    from mattergram.http.dialogs import DialogHandle
    from mattergram.http.responses import ResponseController
    from mattergram.ui.forms import Form


class PostRef(WireModel):
    """Confirmed post/channel identities only; missing text/root are not fabricated."""

    post_id: Identifier = Field(alias="id")
    channel_id: Identifier


class HTTPEvent(Event):
    model_config = ConfigDict(extra="ignore", strict=True, frozen=True, populate_by_name=True)
    _response: ResponseController | None = PrivateAttr(default=None)

    def _controller(self) -> ResponseController:
        if self._response is None:
            raise ResponseStateError("HTTP event has no bound response controller")
        return self._response

    @property
    def raw(self) -> Mapping[str, Any]:
        """Redacted diagnostic snapshot. Full HTTP data requires explicit unsafe_raw()."""
        data = dict(super().raw)
        for name in (
            "token",
            "response_url",
            "trigger_id",
            "text",
            "context",
            "selected_option",
            "data",
            "state",
            "submission",
            "file_ids",
        ):
            if name in data:
                data[name] = "[REDACTED]"
        return data

    def unsafe_raw(self) -> Mapping[str, Any]:
        """Explicit sensitive-payload access for controlled storage/debugging, never normal logs."""
        return deepcopy(self._raw if self._raw is not None else self.model_dump(by_alias=True))


class SlashCommand(HTTPEvent):
    user_id: Identifier
    channel_id: Identifier
    team_id: Identifier | None = None
    command: Identifier
    text: str = Field(default="", repr=False, exclude=True)
    token: SecretStr = Field(repr=False, exclude=True)
    response_url: SecretStr | None = Field(default=None, repr=False, exclude=True)
    trigger_id: SecretStr | None = Field(default=None, repr=False, exclude=True)
    root_id: Identifier | None = None
    user_name: str | None = None
    channel_name: str | None = None
    team_domain: str | None = None

    @field_validator("team_id", "root_id", mode="before")
    @classmethod
    def empty_scope(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator("command")
    @classmethod
    def native_command(cls, value: str) -> str:
        if not value.startswith("/") or len(value) < 2 or any(char.isspace() for char in value):
            raise ValueError("Slash command must contain its native slash trigger")
        return value

    @property
    def name(self) -> str:
        return self.command[1:]

    @property
    def from_user(self) -> User:
        return User(id=self.user_id, username=self.user_name)

    async def answer(self, text: str | None = None, *, ephemeral: bool = True) -> None:
        self._controller().answer(text, ephemeral=ephemeral)

    async def defer(self, text: str | None = None, *, ephemeral: bool = True) -> None:
        self._controller().defer(text, ephemeral=ephemeral)

    async def followup(self, text: str, *, ephemeral: bool = True) -> None:
        await self._controller().followup(text, ephemeral=ephemeral)

    async def navigate(self, url: str) -> None:
        self._controller().navigate(url)

    async def open_dialog(self, form: Form) -> DialogHandle:
        return await self._controller().open_dialog(form)


class CallbackQuery(HTTPEvent):
    user_id: Identifier
    post_id: Identifier
    channel_id: Identifier
    team_id: Identifier | None = None
    user_name: str | None = None
    channel_name: str | None = None
    team_domain: str | None = None
    type: str | None = None
    data_source: str | None = None
    data: str | None = Field(default=None, repr=False, exclude=True)
    selected_option: str | None = Field(default=None, repr=False, exclude=True)
    thread_id: Identifier | None = None
    trigger_id: SecretStr | None = Field(default=None, repr=False, exclude=True)
    action_context: dict[str, Any] = Field(default_factory=dict, alias="context", repr=False, exclude=True)

    @field_validator("team_id", mode="before")
    @classmethod
    def empty_team(cls, value: Any) -> Any:
        return None if value == "" else value

    @property
    def from_user(self) -> User:
        return User(id=self.user_id, username=self.user_name)

    @property
    def message(self) -> PostRef:
        return PostRef.from_wire({"id": self.post_id, "channel_id": self.channel_id})

    async def answer(self, text: str | None = None) -> None:
        self._controller().answer(text)

    async def update_message(self, text: str, *, props: Mapping[str, Any] | None = None) -> None:
        self._controller().update(text, props)

    async def error(self, text: str) -> None:
        self._controller().error(text)

    async def navigate(self, url: str) -> None:
        self._controller().navigate(url)

    async def open_dialog(self, form: Form) -> DialogHandle:
        return await self._controller().open_dialog(form)
