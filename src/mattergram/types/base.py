"""Minimal native wire models. IDs stay strings; unknown wire fields are retained."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, field_validator

from mattergram.exceptions import BotBindingError, ProtocolError

if TYPE_CHECKING:
    from mattergram.bot import Bot

Identifier = Annotated[str, Field(min_length=1)]
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class WireModel(BaseModel):
    """Strict field types, additive wire compatibility, and a detached original payload."""

    model_config = ConfigDict(extra="allow", strict=True, frozen=True, populate_by_name=True)
    _raw: dict[str, Any] | None = PrivateAttr(default=None)

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> Self:
        if not isinstance(payload, Mapping) or not all(isinstance(key, str) for key in payload):
            raise ProtocolError(f"Invalid {cls.__name__} wire object")
        # Alias population is convenient for model constructors; wire parsing still
        # requires native keys instead of accepting a fabricated domain-only alias.
        for name, field in cls.model_fields.items():
            if field.is_required() and (field.alias or name) not in payload:
                raise ProtocolError(f"Missing required {cls.__name__} wire field")
        try:
            result = cls.model_validate(dict(payload))
        except ValidationError:
            raise ProtocolError(f"Invalid {cls.__name__} wire fields") from None
        result._raw = deepcopy(dict(payload))
        return result

    @property
    def raw(self) -> Mapping[str, Any]:
        """Return a detached copy so callers cannot mutate the original wire snapshot."""
        return deepcopy(self._raw if self._raw is not None else self.model_dump(by_alias=True))


class User(WireModel):
    id: Identifier
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    nickname: str | None = None
    is_bot: bool = False
    delete_at: Annotated[int, Field(ge=0)] = 0


class Channel(WireModel):
    id: Identifier
    team_id: str | None = None
    type: str | None = None


@dataclass(frozen=True)
class EventContext:
    instance_id: str
    bot_id: str
    source: str
    received_at: datetime
    correlation_id: str


class Event(WireModel):
    """Native event base; its local correlation identity is never a platform update ID."""

    _context: EventContext | None = PrivateAttr(default=None)

    @property
    def context(self) -> EventContext | None:
        return self._context


class RawEvent(Event):
    event_type: str
    payload: dict[str, Any]


class ErrorEvent(Event):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    event: Event
    exception: Exception = Field(repr=False)


class Message(Event):
    post_id: Identifier = Field(alias="id")
    channel_id: Identifier
    user_id: Identifier
    message: str
    create_at: Annotated[int, Field(ge=0)]
    root_id: Identifier | None = None
    _bot: Bot | None = PrivateAttr(default=None)
    _channel: Channel | None = PrivateAttr(default=None)
    _event_raw: dict[str, Any] | None = PrivateAttr(default=None)

    @field_validator("root_id", mode="before")
    @classmethod
    def empty_root(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator("create_at")
    @classmethod
    def valid_timestamp(cls, value: int) -> int:
        try:
            EPOCH + timedelta(milliseconds=value)
        except OverflowError:
            raise ValueError("Timestamp is outside the supported datetime range") from None
        return value

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any], *, bot: Bot | None = None) -> Self:
        result = super().from_wire(payload)
        result._bot = bot
        return result

    @property
    def message_id(self) -> str:
        return self.post_id

    @property
    def text(self) -> str:
        return self.message

    @property
    def date(self) -> datetime:
        return EPOCH + timedelta(milliseconds=self.create_at)

    @property
    def from_user(self) -> User:
        return User.from_wire({"id": self.user_id})

    @property
    def chat(self) -> Channel:
        return self._channel if self._channel is not None else Channel.from_wire({"id": self.channel_id})

    @property
    def event_raw(self) -> Mapping[str, Any] | None:
        """Original WebSocket envelope when normalized from posted, otherwise None."""
        return deepcopy(self._event_raw)

    def _bound_bot(self) -> Bot:
        if self._bot is None:
            raise BotBindingError("Message has no bound bot")
        return self._bot

    async def answer(self, text: str) -> Message:
        """Create a new channel post; do not inherit the incoming thread root."""
        return await self._bound_bot().send_message(self.channel_id, text)

    async def reply(self, text: str) -> Message:
        """Reply in the existing thread, or start one at this post's real ID."""
        return await self._bound_bot().send_message(
            self.channel_id, text, root_id=self.root_id or self.post_id
        )

    async def edit_text(self, text: str) -> Message:
        """Patch only this post's message field; preserve omitted props and files."""
        return await self._bound_bot().edit_message_text(self.channel_id, self.post_id, text)

    async def delete(self) -> None:
        """Soft-delete this real post subject to the bound bot's server permissions."""
        await self._bound_bot().delete_message(self.channel_id, self.post_id)


__all__ = ["Channel", "ErrorEvent", "Event", "EventContext", "Message", "RawEvent", "User"]
