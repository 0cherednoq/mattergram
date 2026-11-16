"""Copy into an application's composition layer and replace project constants."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from mattergram import Bot, Router
from mattergram.contrib.admin_users import (
    ASSIGN_ROLES,
    DEFAULT_PERMISSIONS,
    MANAGE_USERS,
    VIEW_USERS,
    AdminUsersPlugin,
    MattermostTeamDirectory,
    PermissionSpec,
    RoleSpec,
)
from mattergram.contrib.admin_users.sqlalchemy import SQLAlchemyAdminUserRepository
from mattergram.security import REQUIRED_ROLES_FLAG, RBACMiddleware

ACCOUNTS_VIEW = "accounts.view"
ACCOUNTS_MANAGE = "accounts.manage"

PERMISSIONS = (
    *DEFAULT_PERMISSIONS,
    PermissionSpec(ACCOUNTS_VIEW, "Просмотр аккаунтов"),
    PermissionSpec(ACCOUNTS_MANAGE, "Управление аккаунтами"),
)

ROLES = (
    RoleSpec(
        "admin",
        "Администратор",
        frozenset(
            {
                VIEW_USERS,
                MANAGE_USERS,
                ASSIGN_ROLES,
                ACCOUNTS_VIEW,
                ACCOUNTS_MANAGE,
            }
        ),
        protects_last_member=True,
    ),
    RoleSpec(
        "operator",
        "Оператор",
        frozenset({ACCOUNTS_VIEW, ACCOUNTS_MANAGE}),
    ),
    RoleSpec("viewer", "Наблюдатель", frozenset({ACCOUNTS_VIEW})),
)

GAME_MANAGEMENT_FLAGS = {REQUIRED_ROLES_FLAG: ("admin", "operator")}


def build_admin_users(
    *,
    database_url: str,
    bot: Bot,
    team_id: str,
    owner_user_id: str,
) -> tuple[AsyncEngine, AdminUsersPlugin]:
    engine = create_async_engine(database_url)
    repository = SQLAlchemyAdminUserRepository.from_engine(engine)
    directory = MattermostTeamDirectory(bot, team_id=team_id)
    plugin = AdminUsersPlugin(
        repository=repository,
        audit_sink=repository,
        directory=directory,
        initial_admin_ids=(owner_user_id,),
        permissions=PERMISSIONS,
        roles=ROLES,
        default_role="operator",
        command="app-users",
        messages=("Пользователи",),
    )
    return engine, plugin


def protect_feature_router(router: Router, admin_users: AdminUsersPlugin) -> None:
    access = RBACMiddleware(admin_users.service)
    router.message.middleware(access)
    router.slash_command.middleware(access)
