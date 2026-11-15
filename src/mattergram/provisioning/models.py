"""Native setup responses; secret-bearing responses have no raw payload export."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from mattergram.exceptions import ProtocolError

NativeID = Annotated[str, Field(min_length=1)]


class SetupModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="ignore")

    @classmethod
    def from_wire(cls, value: Any) -> Self:
        if not isinstance(value, dict):
            raise ProtocolError("Invalid native setup response object")
        try:
            return cls.model_validate(value)
        except ValidationError:
            raise ProtocolError("Invalid native setup response fields") from None


class BotAccount(SetupModel):
    user_id: NativeID
    username: str
    owner_id: NativeID
    display_name: str = ""
    description: str = ""
    delete_at: int = 0


class AccessToken(SetupModel):
    id: NativeID
    user_id: NativeID
    token: SecretStr = Field(repr=False, exclude=True)
    description: str = ""
    is_active: bool

    @field_validator("token")
    @classmethod
    def nonempty_token(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("Missing issued secret")
        return value


class NativeCommand(SetupModel):
    # Built-in definitions may have no persisted command/team ID or token.
    id: str = ""
    team_id: str = ""
    trigger: NativeID
    creator_id: str = ""
    token: SecretStr | None = Field(default=None, repr=False, exclude=True)
    url: str = ""
    method: str = ""
    description: str = ""
    display_name: str = ""
    auto_complete: bool = False
    auto_complete_desc: str = ""
    auto_complete_hint: str = ""

    @field_validator("token", mode="before")
    @classmethod
    def sanitized_token(cls, value: Any) -> Any:
        return None if value == "" else value


class NativeTeam(SetupModel):
    id: NativeID
    name: str
    display_name: str
    type: Literal["O", "I"]
    delete_at: int = 0


class NativeChannel(SetupModel):
    id: NativeID
    team_id: NativeID
    name: str
    display_name: str
    type: Literal["O", "P"]
    purpose: str = ""
    header: str = ""
    delete_at: int = 0


class TeamMember(SetupModel):
    team_id: NativeID
    user_id: NativeID
    roles: str
    delete_at: int = 0


class ChannelMember(SetupModel):
    channel_id: NativeID
    user_id: NativeID
    roles: str


class NativeRole(SetupModel):
    name: str
    permissions: list[str]
    delete_at: int = 0


class ServerFeatures(SetupModel):
    version: str
    commands: bool | None = None
    bot_creation: bool | None = None
    access_tokens: bool | None = None
