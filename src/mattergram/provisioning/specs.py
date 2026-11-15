"""Explicit deployment declarations; strings remain references, never create policies."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator

from .store_models import StoreModel


class BotSpec(StoreModel):
    username: str = Field(min_length=1)
    display_name: str = ""
    description: str = ""
    existing_id: str | None = Field(default=None, min_length=1)
    existing_token: SecretStr | None = Field(default=None, exclude=True, repr=False)

    @model_validator(mode="after")
    def explicit_existing(self) -> Self:
        if (self.existing_id is None) != (self.existing_token is None):
            raise ValueError("An existing bot requires both its explicit native ID and credential")
        return self


class TeamSpec(StoreModel):
    key: str = Field(min_length=1)
    name: str | None = Field(default=None, min_length=1)
    team_id: str | None = Field(default=None, min_length=1)
    display_name: str | None = None
    visibility: Literal["open", "invite"] | None = None
    create_if_missing: bool = False
    existing_policy: Literal["error", "reuse"] = "error"

    @model_validator(mode="after")
    def identification(self) -> Self:
        if (self.name is None) == (self.team_id is None):
            raise ValueError("Identify a team by name or native ID")
        if self.create_if_missing and (
            self.team_id is not None or not self.display_name or self.visibility is None
        ):
            raise ValueError("Creating a team requires name, display_name and explicit visibility")
        return self


class MemberSpec(StoreModel):
    """One existing native user, identified without display-name inference."""

    user_id: str | None = Field(default=None, min_length=1)
    username: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def identification(self) -> Self:
        if (self.user_id is None) == (self.username is None):
            raise ValueError("Identify a member by native user ID or exact username")
        return self


class ChannelSpec(StoreModel):
    key: str = Field(min_length=1)
    name: str | None = Field(default=None, min_length=1)
    channel_id: str | None = Field(default=None, min_length=1)
    team: str | None = None
    display_name: str | None = None
    visibility: Literal["public", "private"] | None = None
    purpose: str | None = None
    header: str | None = None
    create_if_missing: bool = False
    existing_policy: Literal["error", "reuse"] = "error"
    add_bot: bool = True
    members: tuple[str | MemberSpec, ...] = ()  # Strings are existing native IDs.

    @field_validator("members", mode="before")
    @classmethod
    def member_sequence(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def identification(self) -> Self:
        if (self.name is None) == (self.channel_id is None):
            raise ValueError("Identify a channel by name or native ID")
        if self.create_if_missing and (
            self.channel_id is not None or not self.display_name or self.visibility is None
        ):
            raise ValueError("Creating a channel requires name, display_name and explicit visibility")
        identities = tuple(
            ("id", member)
            if isinstance(member, str)
            else ("id", member.user_id)
            if member.user_id is not None
            else ("username", member.username)
            for member in self.members
        )
        if len(set(identities)) != len(identities) or any(not identity[1] for identity in identities):
            raise ValueError("Channel members must be unique existing native IDs or exact usernames")
        return self
