"""Domain declarations for application-scoped users, roles and permissions."""

from __future__ import annotations

import re
from dataclasses import dataclass

_DECLARATION_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")

VIEW_USERS = "admin_users.view"
MANAGE_USERS = "admin_users.manage"
ASSIGN_ROLES = "admin_users.assign_roles"


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    id: str
    title: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or _DECLARATION_ID.fullmatch(self.id) is None:
            raise ValueError("Permission id must be a stable lowercase identifier")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("Permission title must be nonempty")


@dataclass(frozen=True, slots=True)
class RoleSpec:
    id: str
    title: str
    permissions: frozenset[str] = frozenset()
    protects_last_member: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or _DECLARATION_ID.fullmatch(self.id) is None:
            raise ValueError("Role id must be a stable lowercase identifier")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("Role title must be nonempty")
        if not isinstance(self.permissions, frozenset) or not all(
            isinstance(permission, str) and permission for permission in self.permissions
        ):
            raise TypeError("Role permissions must be a frozenset of nonempty IDs")
        if type(self.protects_last_member) is not bool:
            raise TypeError("protects_last_member must be a boolean")


@dataclass(frozen=True, slots=True)
class AdminUser:
    id: str
    mattermost_user_id: str
    username: str
    role: str
    enabled: bool = True

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value
            for value in (self.id, self.mattermost_user_id, self.username, self.role)
        ):
            raise ValueError("Admin user identifiers, username and role must be nonempty")
        if type(self.enabled) is not bool:
            raise TypeError("Admin user enabled state must be a boolean")


@dataclass(frozen=True, slots=True)
class TeamUser:
    """One active Mattermost account returned by the configured team directory."""

    id: str
    username: str
    display_name: str = ""
    is_bot: bool = False

    def __post_init__(self) -> None:
        if not self.id or not self.username:
            raise ValueError("Team users require nonempty Mattermost IDs and usernames")
        if type(self.is_bot) is not bool:
            raise TypeError("Team user bot state must be a boolean")

    @property
    def option_label(self) -> str:
        name = self.display_name.strip()
        return (
            f"@{self.username} — {name}"
            if name and name.casefold() != self.username.casefold()
            else f"@{self.username}"
        )


@dataclass(frozen=True, slots=True)
class AuditRecord:
    created_at: str
    actor_id: str
    action: str
    target_user_id: str
    details: tuple[tuple[str, str], ...] = ()


DEFAULT_PERMISSIONS = (
    PermissionSpec(VIEW_USERS, "Просмотр пользователей"),
    PermissionSpec(MANAGE_USERS, "Управление пользователями"),
    PermissionSpec(ASSIGN_ROLES, "Назначение ролей"),
)

DEFAULT_ROLES = (
    RoleSpec(
        id="admin",
        title="Администратор",
        permissions=frozenset({VIEW_USERS, MANAGE_USERS, ASSIGN_ROLES}),
        protects_last_member=True,
    ),
    RoleSpec(
        id="operator",
        title="Оператор",
        permissions=frozenset(),
    ),
    RoleSpec(id="viewer", title="Наблюдатель", permissions=frozenset({VIEW_USERS})),
)


__all__ = [
    "ASSIGN_ROLES",
    "AdminUser",
    "AuditRecord",
    "DEFAULT_PERMISSIONS",
    "DEFAULT_ROLES",
    "MANAGE_USERS",
    "PermissionSpec",
    "RoleSpec",
    "TeamUser",
    "VIEW_USERS",
]
