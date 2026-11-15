"""Reusable Mattergram application plugin for app-scoped user access."""

from __future__ import annotations

from collections.abc import Sequence

from mattergram.plugins import PluginBuilder, PluginContext, PluginManifest, ProviderKey
from mattergram.provisioning import CommandSpec

from .actions import AdminUsersActions
from .dialog import build_admin_users_dialog
from .directory import TeamUserDirectory
from .getters import AdminUsersGetters
from .models import DEFAULT_PERMISSIONS, DEFAULT_ROLES, PermissionSpec, RoleSpec
from .repository import AdminUserRepository, AuditSink
from .router import AdminUsersRuntime, build_admin_users_router
from .service import AdminUsersService

ADMIN_USERS_SERVICE = ProviderKey[AdminUsersService]("admin_users_service")
ADMIN_USERS_RUNTIME = ProviderKey[AdminUsersRuntime]("admin_users_runtime")


class AdminUsersPlugin:
    manifest = PluginManifest(id="mattergram.admin_users", version="1.0.0")

    def __init__(
        self,
        *,
        repository: AdminUserRepository,
        directory: TeamUserDirectory,
        initial_admin_ids: Sequence[str],
        roles: Sequence[RoleSpec] | None = None,
        permissions: Sequence[PermissionSpec] | None = None,
        command: str = "app-users",
        messages: Sequence[str] = ("Пользователи",),
        audit_sink: AuditSink | None = None,
    ) -> None:
        CommandSpec(command)
        if not messages or any(not isinstance(message, str) or not message for message in messages):
            raise ValueError("Admin Users entry messages must be nonempty strings")
        if len(set(messages)) != len(messages):
            raise ValueError("Admin Users entry messages must be unique")
        self.roles = tuple(DEFAULT_ROLES if roles is None else roles)
        self.service = AdminUsersService(
            repository,
            directory,
            permissions=DEFAULT_PERMISSIONS if permissions is None else permissions,
            roles=self.roles,
            initial_admin_ids=initial_admin_ids,
            audit_sink=audit_sink,
        )
        self.router = build_admin_users_router(command=command, messages=tuple(messages))
        self.dialog = build_admin_users_dialog(
            getters=AdminUsersGetters(self.service),
            actions=AdminUsersActions(self.service),
            roles=self.roles,
        )

    def setup(self, builder: PluginBuilder) -> None:
        builder.include_router(self.router)
        builder.include_dialog(self.dialog)
        builder.provide(ADMIN_USERS_SERVICE, self.service)

    async def startup(self, context: PluginContext) -> None:
        runtime = context.dispatcher.workflow_data.get(ADMIN_USERS_RUNTIME.name)
        if not isinstance(runtime, AdminUsersRuntime):
            raise RuntimeError(
                "AdminUsersPlugin requires admin_users_runtime to be bound after DialogRuntime creation"
            )
        await self.service.bootstrap()

    async def shutdown(self, context: PluginContext) -> None:
        del context


__all__ = ["ADMIN_USERS_RUNTIME", "ADMIN_USERS_SERVICE", "AdminUsersPlugin"]
