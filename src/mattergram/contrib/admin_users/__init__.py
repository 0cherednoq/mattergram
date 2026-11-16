"""Reusable app-scoped user, role and permission administration."""

from .actions import AdminUsersActions
from .dialog import build_admin_users_dialog
from .directory import MattermostTeamDirectory, TeamUserDirectory
from .events import AdminUserChange, AdminUserChangeSink
from .getters import AdminUsersGetters
from .models import (
    ASSIGN_ROLES,
    DEFAULT_PERMISSIONS,
    DEFAULT_ROLES,
    MANAGE_USERS,
    VIEW_USERS,
    AdminUser,
    AuditRecord,
    PermissionSpec,
    RoleSpec,
    TeamUser,
)
from .plugin import ADMIN_USERS_RUNTIME, ADMIN_USERS_SERVICE, AdminUsersPlugin
from .repository import AdminUserRepository, AuditSink, MemoryAdminUserRepository
from .router import AdminUsersRuntime
from .service import AdminUsersInputError, AdminUsersService
from .states import AdminUsersPanel

__all__ = [
    "ADMIN_USERS_RUNTIME",
    "ADMIN_USERS_SERVICE",
    "ASSIGN_ROLES",
    "AdminUser",
    "AdminUserChange",
    "AdminUserChangeSink",
    "AdminUserRepository",
    "AdminUsersActions",
    "AdminUsersGetters",
    "AdminUsersInputError",
    "AdminUsersPanel",
    "AdminUsersPlugin",
    "AdminUsersRuntime",
    "AdminUsersService",
    "AuditRecord",
    "AuditSink",
    "DEFAULT_PERMISSIONS",
    "DEFAULT_ROLES",
    "MANAGE_USERS",
    "MattermostTeamDirectory",
    "MemoryAdminUserRepository",
    "PermissionSpec",
    "RoleSpec",
    "TeamUser",
    "TeamUserDirectory",
    "VIEW_USERS",
    "build_admin_users_dialog",
]
