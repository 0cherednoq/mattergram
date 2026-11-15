"""Read live native role permissions; do not infer authorization from role names."""

from __future__ import annotations

from mattergram.exceptions import APIError, PermissionDeniedError, ProtocolError

from .client import SetupClient


class Permissions:
    def __init__(self, client: SetupClient, user_id: str, roles: str) -> None:
        self.client, self.user_id, self.roles = client, user_id, roles
        self._roles: dict[str, frozenset[str]] = {}

    async def role_permissions(self, roles: str) -> frozenset[str]:
        result: set[str] = set()
        for name in roles.split():
            if name not in self._roles:
                role = await self.client.get_role(name)
                if role.name != name:
                    raise ProtocolError("Native role identity differs")
                self._roles[name] = frozenset(role.permissions) if role.delete_at == 0 else frozenset()
            result.update(self._roles[name])
        return frozenset(result)

    async def global_permissions(self) -> frozenset[str]:
        return await self.role_permissions(self.roles)

    async def team_permissions(self, team_id: str) -> frozenset[str]:
        permissions = await self.global_permissions()
        try:
            member = await self.client.get_team_member(team_id, self.user_id)
        except APIError as error:
            if error.status != 404:
                raise
        else:
            if member.team_id != team_id or member.user_id != self.user_id:
                raise ProtocolError("Native team membership identity differs")
            if member.delete_at == 0:
                permissions |= await self.role_permissions(member.roles)
        return permissions

    async def require(
        self, permission: str, *, team_id: str | None = None, channel_id: str | None = None
    ) -> None:
        permissions = await self.team_permissions(team_id) if team_id else await self.global_permissions()
        if channel_id is not None:
            if "manage_system" in await self.global_permissions():
                return  # Native channel permission shortcut, not a team/global shortcut.
            try:
                member = await self.client.get_channel_member(channel_id, self.user_id)
            except APIError as error:
                if error.status != 404:
                    raise
            else:
                if member.channel_id != channel_id or member.user_id != self.user_id:
                    raise ProtocolError("Native channel membership identity differs")
                permissions |= await self.role_permissions(member.roles)
        if permission not in permissions:
            raise PermissionDeniedError(403, error_id="mattergram.provisioning.permission." + permission)
