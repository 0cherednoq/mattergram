"""Explicit setup operations. Ownership/planning must be enforced by the caller."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar
from urllib.parse import quote, urlencode

from mattergram._http_client import _HTTPClient
from mattergram.exceptions import ProtocolError, RequestOutcomeUnknown
from mattergram.types import User

from .commands import CommandSpec
from .models import (
    AccessToken,
    BotAccount,
    ChannelMember,
    NativeChannel,
    NativeCommand,
    NativeRole,
    NativeTeam,
    ServerFeatures,
    SetupModel,
    TeamMember,
)

M = TypeVar("M", bound=SetupModel)


def _segment(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or any(c in "/\\?#%" or c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValueError("Expected one nonempty native ID or name path segment")
    return quote(value, safe="")


class SetupClient(_HTTPClient):
    """Dedicated credential and pool; never a runtime Bot or automatic reconciler."""

    async def get_role(self, name: str) -> NativeRole:
        return await self._read("roles/name/" + _segment(name), NativeRole)

    async def get_features(self) -> ServerFeatures:
        data = await self._request("GET", "config/client?format=old", 200)
        return ServerFeatures.from_wire(
            {
                "version": data.get("Version"),
                **{
                    field: {"true": True, "false": False}.get(str(data.get(key)))
                    for field, key in (
                        ("commands", "EnableCommands"),
                        ("bot_creation", "EnableBotAccountCreation"),
                        ("access_tokens", "EnableUserAccessTokens"),
                    )
                },
            }
        )

    async def enable_settings(self, *names: str) -> ServerFeatures:
        if not names or set(names) - {"EnableCommands", "EnableBotAccountCreation", "EnableUserAccessTokens"}:
            raise ValueError("Only the explicit provisioning feature allowlist can be enabled")
        await self._request("PUT", "config/patch", 200, {"ServiceSettings": {name: True for name in names}})
        return await self.get_features()

    async def patch_channel(
        self,
        channel_id: str,
        *,
        display_name: str | None = None,
        purpose: str | None = None,
        header: str | None = None,
    ) -> NativeChannel:
        payload = {
            key: value
            for key, value in (("display_name", display_name), ("purpose", purpose), ("header", header))
            if value is not None
        }
        if not payload:
            raise ValueError("Declare at least one mutable channel property")
        return await self._write(
            "PUT", "channels/" + _segment(channel_id) + "/patch", 200, NativeChannel, payload
        )

    async def _read(self, path: str, model: type[M]) -> M:
        return model.from_wire(await self._request("GET", path, 200))

    async def _write(
        self, method: str, path: str, status: int, model: type[M], payload: Mapping[str, Any] | None = None
    ) -> M:
        try:
            return model.from_wire(await self._request(method, path, status, payload))
        except ProtocolError:
            raise RequestOutcomeUnknown(
                "Native setup write response is invalid; reconcile before retrying"
            ) from None

    async def _status(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> None:
        try:
            response = await self._request(method, path, 200, payload)
            if response.get("status") != "OK":
                raise ProtocolError("Missing native status OK")
        except ProtocolError:
            raise RequestOutcomeUnknown(
                "Native setup write was not confirmed; reconcile before retrying"
            ) from None

    async def get_user(self, user_id: str) -> User:
        return User.from_wire(await self._request("GET", "users/" + _segment(user_id), 200))

    async def get_user_by_username(self, username: str) -> User:
        return User.from_wire(await self._request("GET", "users/username/" + _segment(username), 200))

    async def get_bot(self, user_id: str) -> BotAccount:
        return await self._read("bots/" + _segment(user_id), BotAccount)

    async def create_bot(self, *, username: str, display_name: str = "", description: str = "") -> BotAccount:
        _segment(username)
        return await self._write(
            "POST",
            "bots",
            201,
            BotAccount,
            {
                "username": username,
                "display_name": display_name,
                "description": description,
            },
        )

    async def issue_access_token(self, user_id: str, *, description: str) -> AccessToken:
        if not isinstance(description, str) or not 1 <= len(description.encode("utf-8")) <= 255:
            raise ValueError("Access token description must be 1-255 UTF-8 bytes")
        # This native operation returns 200, not 201.
        result = await self._write(
            "POST", "users/" + _segment(user_id) + "/tokens", 200, AccessToken, {"description": description}
        )
        if result.user_id != user_id or not result.is_active:
            raise RequestOutcomeUnknown(
                "Issued token identity/active state differs; reconcile before retrying"
            )
        return result

    async def revoke_access_token(self, token_id: str) -> None:
        _segment(token_id)
        await self._status("POST", "users/tokens/revoke", {"token_id": token_id})

    async def get_team(self, team_id: str) -> NativeTeam:
        return await self._read("teams/" + _segment(team_id), NativeTeam)

    async def get_team_by_name(self, name: str) -> NativeTeam:
        return await self._read("teams/name/" + _segment(name), NativeTeam)

    async def patch_team(self, team_id: str, *, display_name: str) -> NativeTeam:
        return await self._write(
            "PUT", "teams/" + _segment(team_id) + "/patch", 200, NativeTeam, {"display_name": display_name}
        )

    async def create_team(self, *, name: str, display_name: str, type: Literal["O", "I"]) -> NativeTeam:
        _segment(name)
        if type not in {"O", "I"}:
            raise ValueError("Native team type must be O or I")
        return await self._write(
            "POST", "teams", 201, NativeTeam, {"name": name, "display_name": display_name, "type": type}
        )

    async def get_channel(self, channel_id: str) -> NativeChannel:
        return await self._read("channels/" + _segment(channel_id), NativeChannel)

    async def get_channel_by_name(self, team_id: str, name: str) -> NativeChannel:
        return await self._read(
            "teams/" + _segment(team_id) + "/channels/name/" + _segment(name) + "?include_deleted=true",
            NativeChannel,
        )

    async def create_channel(
        self,
        *,
        team_id: str,
        name: str,
        display_name: str,
        type: Literal["O", "P"],
        purpose: str = "",
        header: str = "",
    ) -> NativeChannel:
        _segment(team_id)
        _segment(name)
        if type not in {"O", "P"}:
            raise ValueError("Native channel type must be O or P")
        return await self._write(
            "POST",
            "channels",
            201,
            NativeChannel,
            {
                "team_id": team_id,
                "name": name,
                "display_name": display_name,
                "type": type,
                "purpose": purpose,
                "header": header,
            },
        )

    async def get_team_member(self, team_id: str, user_id: str) -> TeamMember:
        return await self._read("teams/" + _segment(team_id) + "/members/" + _segment(user_id), TeamMember)

    async def add_team_member(self, team_id: str, user_id: str) -> TeamMember:
        _segment(user_id)
        return await self._write(
            "POST",
            "teams/" + _segment(team_id) + "/members",
            201,
            TeamMember,
            {"team_id": team_id, "user_id": user_id},
        )

    async def get_channel_member(self, channel_id: str, user_id: str) -> ChannelMember:
        return await self._read(
            "channels/" + _segment(channel_id) + "/members/" + _segment(user_id), ChannelMember
        )

    async def add_channel_member(self, channel_id: str, user_id: str) -> ChannelMember:
        _segment(user_id)
        return await self._write(
            "POST", "channels/" + _segment(channel_id) + "/members", 201, ChannelMember, {"user_id": user_id}
        )

    async def list_commands(self, team_id: str) -> tuple[NativeCommand, ...]:
        _segment(team_id)
        data = await self._request_json(
            "GET", "commands?" + urlencode({"team_id": team_id, "custom_only": "false"}), 200
        )
        if not isinstance(data, list):
            raise ProtocolError("Native command list must be a JSON array")
        return tuple(NativeCommand.from_wire(entry) for entry in data)

    async def get_command(self, command_id: str) -> NativeCommand:
        command = await self._read("commands/" + _segment(command_id), NativeCommand)
        if command.id != command_id:
            raise ProtocolError("Native command identity differs from the requested resource")
        return command

    async def create_command(self, spec: CommandSpec, *, team_id: str, url: str) -> NativeCommand:
        command = await self._write(
            "POST", "commands", 201, NativeCommand, spec.to_payload(team_id=team_id, url=url)
        )
        if (
            not command.id
            or command.token is None
            or command.team_id != team_id
            or command.trigger != spec.trigger
        ):
            raise RequestOutcomeUnknown("Created command identity/secret differs; reconcile before retrying")
        return command

    async def update_command(
        self, command_id: str, spec: CommandSpec, *, team_id: str, url: str
    ) -> NativeCommand:
        payload = spec.to_payload(team_id=team_id, url=url) | {"id": command_id}
        command = await self._write("PUT", "commands/" + _segment(command_id), 200, NativeCommand, payload)
        if command.id != command_id or command.team_id != team_id or command.trigger != spec.trigger:
            raise RequestOutcomeUnknown("Updated command identity differs; reconcile before retrying")
        return command

    async def delete_command(self, command_id: str) -> None:
        await self._status("DELETE", "commands/" + _segment(command_id))
