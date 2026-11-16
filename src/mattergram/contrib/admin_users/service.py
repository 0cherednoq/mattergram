"""Authorized use cases and invariants for application-scoped users."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from .directory import TeamUserDirectory
from .events import AdminUserChange, AdminUserChangeSink, NullAdminUserChangeSink
from .models import (
    ASSIGN_ROLES,
    MANAGE_USERS,
    VIEW_USERS,
    AdminUser,
    AuditRecord,
    PermissionSpec,
    RoleSpec,
    TeamUser,
)
from .repository import AdminUserRepository, AuditSink


class AdminUsersInputError(ValueError):
    def __init__(self, field_errors: Mapping[str, str]) -> None:
        super().__init__("Administrator user input is invalid")
        self.field_errors = dict(field_errors)


class _NullAuditSink:
    async def write(self, record: AuditRecord) -> None:
        del record


class AdminUsersService:
    """Apply app permissions, team membership and the last-admin invariant."""

    def __init__(
        self,
        repository: AdminUserRepository,
        directory: TeamUserDirectory,
        *,
        permissions: Sequence[PermissionSpec],
        roles: Sequence[RoleSpec],
        initial_admin_ids: Sequence[str],
        audit_sink: AuditSink | None = None,
        change_sink: AdminUserChangeSink | None = None,
        default_role: str | None = None,
    ) -> None:
        if not permissions or len({permission.id for permission in permissions}) != len(permissions):
            raise ValueError("Permissions must be a nonempty sequence with unique IDs")
        if not roles or len({role.id for role in roles}) != len(roles):
            raise ValueError("Roles must be a nonempty sequence with unique IDs")
        permission_ids = {permission.id for permission in permissions}
        unknown = {
            permission
            for role in roles
            for permission in role.permissions
            if permission not in permission_ids
        }
        if unknown:
            raise ValueError("Roles reference unknown permissions: " + ", ".join(sorted(unknown)))
        protected = [role for role in roles if role.protects_last_member]
        if len(protected) != 1:
            raise ValueError("Exactly one role must protect its last enabled member")
        if not initial_admin_ids or any(not value for value in initial_admin_ids):
            raise ValueError("Initial administrator IDs must be explicit and nonempty")
        if len(set(initial_admin_ids)) != len(initial_admin_ids):
            raise ValueError("Initial administrator IDs must be unique")
        self.repository = repository
        self.directory = directory
        self.permissions = tuple(permissions)
        self.roles = tuple(roles)
        self.initial_admin_ids = tuple(initial_admin_ids)
        self.audit_sink = audit_sink if audit_sink is not None else _NullAuditSink()
        self.change_sink = change_sink if change_sink is not None else NullAdminUserChangeSink()
        self._roles = {role.id: role for role in roles}
        self._protected_role = protected[0]
        managed_default = default_role or next(
            (role.id for role in roles if not role.protects_last_member),
            None,
        )
        if managed_default is None or managed_default not in self._roles:
            raise ValueError("A non-protected default application role is required")
        if self._roles[managed_default].protects_last_member:
            raise ValueError("The default application role cannot be protected")
        self.default_role = managed_default
        self._write_lock = asyncio.Lock()

    async def bootstrap(self) -> None:
        """Idempotently ensure only explicitly configured initial administrators."""

        members = await self._directory_members()
        async with self._write_lock:
            for mattermost_id in self.initial_admin_ids:
                member = members.get(mattermost_id)
                if member is None or member.is_bot:
                    raise AdminUsersInputError(
                        {
                            "initial_admin_ids": (
                                f"Mattermost user {mattermost_id!r} is not an active human team member"
                            )
                        }
                    )
                current = await self.repository.get_by_mattermost_user_id(mattermost_id)
                desired = AdminUser(
                    id=current.id if current is not None else "usr_" + uuid4().hex,
                    mattermost_user_id=mattermost_id,
                    username=member.username,
                    role=self._protected_role.id,
                    enabled=True,
                )
                if current != desired:
                    await self.repository.save_user(desired)
                    await self._audit("bootstrap", "admin_users.bootstrap", desired)

    async def permissions_for(self, actor_id: str) -> frozenset[str]:
        actor = await self.repository.get_by_mattermost_user_id(actor_id)
        if actor is None or not actor.enabled:
            return frozenset()
        role = self._roles.get(actor.role)
        return frozenset() if role is None else role.permissions

    async def resolve_role(self, actor_id: str) -> str | None:
        """Resolve the enabled application role for core RBAC middleware."""

        actor = await self.repository.get_by_mattermost_user_id(actor_id)
        if actor is None or not actor.enabled or actor.role not in self._roles:
            return None
        return actor.role

    async def require_permission(self, actor_id: str, permission: str) -> AdminUser:
        if permission not in {item.id for item in self.permissions}:
            raise ValueError(f"Unknown permission {permission!r}")
        actor = await self.repository.get_by_mattermost_user_id(actor_id)
        if actor is None or not actor.enabled:
            raise PermissionError("An enabled application user is required")
        role = self._roles.get(actor.role)
        if role is None or permission not in role.permissions:
            raise PermissionError(f"Application permission {permission!r} is required")
        return actor

    async def list_users(self, actor_id: str) -> tuple[AdminUser, ...]:
        await self.require_permission(actor_id, VIEW_USERS)
        members = await self._directory_members()
        users = await self.repository.list_users()
        refreshed = [
            replace(user, username=members[user.mattermost_user_id].username)
            if user.mattermost_user_id in members
            else user
            for user in users
        ]
        return tuple(sorted(refreshed, key=lambda user: (user.username.casefold(), user.id)))

    async def get_user(self, actor_id: str, user_id: str) -> AdminUser:
        await self.require_permission(actor_id, VIEW_USERS)
        current = await self.repository.get_user(user_id)
        if current is None:
            raise AdminUsersInputError({"user": "Пользователь больше не существует"})
        members = await self._directory_members()
        member = members.get(current.mattermost_user_id)
        return replace(current, username=member.username) if member is not None else current

    async def list_available_team_users(self, actor_id: str) -> tuple[TeamUser, ...]:
        await self.require_permission(actor_id, MANAGE_USERS)
        assigned = {user.mattermost_user_id for user in await self.repository.list_users() if user.enabled}
        members = await self._directory_members()
        return tuple(
            sorted(
                (member for member in members.values() if not member.is_bot and member.id not in assigned),
                key=lambda member: (member.username.casefold(), member.id),
            )
        )

    async def add_user(
        self,
        actor_id: str,
        *,
        mattermost_user_id: str,
        role: str,
        enabled: bool,
    ) -> AdminUser:
        actor = await self.require_permission(actor_id, MANAGE_USERS)
        permissions = await self.permissions_for(actor_id)
        if ASSIGN_ROLES not in permissions and role != self.default_role:
            raise PermissionError("Application permission admin_users.assign_roles is required")
        role_spec = self._validate_write(mattermost_user_id, role, enabled)
        members = await self._directory_members()
        selected = members.get(mattermost_user_id)
        if selected is None or selected.is_bot:
            raise AdminUsersInputError(
                {"mattermost_user_id": ("Пользователь не входит в выбранную команду или отключён")}
            )
        async with self._write_lock:
            current = await self.repository.get_by_mattermost_user_id(mattermost_user_id)
            if current is not None and current.enabled:
                raise AdminUsersInputError({"mattermost_user_id": "Этот Mattermost пользователь уже активен"})
            user = AdminUser(
                id=current.id if current is not None else "usr_" + uuid4().hex,
                mattermost_user_id=mattermost_user_id,
                username=selected.username,
                role=role_spec.id,
                enabled=enabled,
            )
            await self.repository.save_user(user)
            action = "admin_users.reactivate" if current else "admin_users.add"
            await self._audit(actor.mattermost_user_id, action, user)
            await self.change_sink.write(
                AdminUserChange(
                    actor_id=actor.mattermost_user_id,
                    action=action,
                    previous=current,
                    current=user,
                )
            )
            return user

    async def update_user(
        self,
        actor_id: str,
        user_id: str,
        *,
        role: str,
        enabled: bool,
    ) -> AdminUser:
        actor = await self.require_permission(actor_id, MANAGE_USERS)
        role_spec = self._validate_write(user_id, role, enabled)
        async with self._write_lock:
            current = await self.repository.get_user(user_id)
            if current is None:
                raise AdminUsersInputError({"user": "Пользователь больше не существует"})
            if role_spec.id != current.role:
                await self.require_permission(actor_id, ASSIGN_ROLES)
            members = await self._directory_members()
            member = members.get(current.mattermost_user_id)
            if member is None or member.is_bot:
                raise AdminUsersInputError(
                    {"user": "Mattermost пользователь больше не является активным участником команды"}
                )
            if await self._removes_last_protected_member(current, role_spec, enabled):
                raise AdminUsersInputError(
                    {"enabled": "Нельзя отключить или понизить последнего администратора"}
                )
            updated = AdminUser(
                id=current.id,
                mattermost_user_id=current.mattermost_user_id,
                username=member.username,
                role=role_spec.id,
                enabled=enabled,
            )
            await self.repository.save_user(updated)
            await self._audit(actor.mattermost_user_id, "admin_users.update", updated)
            await self.change_sink.write(
                AdminUserChange(
                    actor_id=actor.mattermost_user_id,
                    action="admin_users.update",
                    previous=current,
                    current=updated,
                )
            )
            return updated

    def role(self, role_id: str) -> RoleSpec:
        try:
            return self._roles[role_id]
        except KeyError:
            raise AdminUsersInputError({"role": "Неизвестная роль приложения"}) from None

    def _validate_write(self, identity: str, role: str, enabled: bool) -> RoleSpec:
        errors: dict[str, str] = {}
        if not isinstance(identity, str) or not identity.strip():
            errors["mattermost_user_id"] = "Выберите пользователя команды Mattermost"
        if type(enabled) is not bool:
            errors["enabled"] = "Некорректное состояние пользователя"
        if errors:
            raise AdminUsersInputError(errors)
        return self.role(role)

    async def _removes_last_protected_member(
        self,
        current: AdminUser,
        target_role: RoleSpec,
        enabled: bool,
    ) -> bool:
        current_role = self._roles.get(current.role)
        if (
            current_role is None
            or not current.enabled
            or not current_role.protects_last_member
            or (enabled and target_role.protects_last_member)
        ):
            return False
        users = await self.repository.list_users()
        return not any(
            user.id != current.id
            and user.enabled
            and self._roles.get(user.role) is not None
            and self._roles[user.role].protects_last_member
            for user in users
        )

    async def _directory_members(self) -> dict[str, TeamUser]:
        users = tuple(await self.directory.list_users())
        if len({user.id for user in users}) != len(users):
            raise RuntimeError("Mattermost team user directory returned duplicate IDs")
        return {user.id: user for user in users}

    async def _audit(self, actor_id: str, action: str, user: AdminUser) -> None:
        await self.audit_sink.write(
            AuditRecord(
                created_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
                actor_id=actor_id,
                action=action,
                target_user_id=user.id,
                details=(
                    ("mattermost_user_id", user.mattermost_user_id),
                    ("role", user.role),
                    ("enabled", str(user.enabled).lower()),
                ),
            )
        )


__all__ = ["AdminUsersInputError", "AdminUsersService"]
