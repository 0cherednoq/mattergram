# Admin users and access management

Read this reference when a bot needs an administrator-managed list of application users, roles,
role-specific menus, or protected feature entries.

## Default decision

Use `mattergram.contrib.admin_users.AdminUsersPlugin` for app-scoped access instead of copying user
tables, role forms, pagination, last-admin protection, or audit code into each bot. It manages access
to the application only. It does not create Mattermost accounts or change Mattermost system/team
roles.

For a new implementation, copy and adapt
[`assets/code/admin-users-plugin-template.py`](../assets/code/admin-users-plugin-template.py).

Use:

- `MemoryAdminUserRepository` only in tests and process-local demos;
- `SQLAlchemyAdminUserRepository` for ready durable SQLAlchemy 2.0 storage;
- a custom `AdminUserRepository` only when the project already owns a repository/unit-of-work layer.

## Declare permissions and roles

Keep stable lowercase IDs. Include the plugin's built-in permissions in any custom declaration:

```python
from mattergram.contrib.admin_users import (
    ASSIGN_ROLES,
    DEFAULT_PERMISSIONS,
    MANAGE_USERS,
    VIEW_USERS,
    PermissionSpec,
    RoleSpec,
)

ACCOUNTS_VIEW = "accounts.view"
ACCOUNTS_MANAGE = "accounts.manage"

permissions = (
    *DEFAULT_PERMISSIONS,
    PermissionSpec(ACCOUNTS_VIEW, "Просмотр аккаунтов"),
    PermissionSpec(ACCOUNTS_MANAGE, "Управление аккаунтами"),
)

roles = (
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
```

`admin` can manage application users because its role contains `admin_users.view`,
`admin_users.manage`, and `admin_users.assign_roles`. An operator without these permissions cannot
open or mutate the user-management dialog. `protects_last_member=True` belongs to exactly one role.

## Configure durable storage

Install the optional adapter and let the application own engine lifecycle:

```sh
pip install "mattergram[sqlalchemy]"
```

```python
from sqlalchemy.ext.asyncio import create_async_engine
from mattergram.contrib.admin_users.sqlalchemy import SQLAlchemyAdminUserRepository

engine = create_async_engine(DATABASE_URL)
repository = SQLAlchemyAdminUserRepository.from_engine(engine)
```

Use `SQLAlchemyAdminUserRepository.create_schema(engine)` only for local/test bootstrap. Production
deployments should migrate the exported `admin_users_metadata`. Passing the repository as
`audit_sink` stores user-management audit records in the same database.

The built-in service lock protects the last-admin invariant within one process. A multi-writer
deployment must serialize access-management writes through its database transaction or queue.

## Assemble the plugin

```python
admin_users = AdminUsersPlugin(
    repository=repository,
    audit_sink=repository,
    directory=team_directory,
    initial_admin_ids=(OWNER_MATTERMOST_USER_ID,),
    permissions=permissions,
    roles=roles,
    default_role="operator",
    command="app-users",
    messages=("Пользователи",),
)
```

Pass exact Mattermost user IDs for initial administrators. Never infer an admin from username,
system-admin status, or an empty repository. Bind `ADMIN_USERS_RUNTIME` after constructing the shared
`DialogRuntime`, as shown in the repository `docs/application-plugins.md` guide.

## Protect feature handlers

Use the plugin service as the core `RoleResolver` and standard handler flags:

```python
from mattergram.security import REQUIRED_ROLES_FLAG, RBACMiddleware

router.message.middleware(RBACMiddleware(admin_users.service))


@router.message(
    open_accounts_filter,
    flags={REQUIRED_ROLES_FLAG: ("admin", "operator", "viewer")},
)
async def open_accounts(message, actor_role: str) -> None:
    ...
```

Flags are a coarse role OR-list. Every service method still checks the exact permission, ownership,
tenant, and mutation invariant. Dialog callbacks and background workers call those same protected
services. Read [access-control.md](access-control.md) for the core RBAC contract.

## Role-specific keyboard and DM access

`AdminUsersPlugin` must remain independent from the keyboard plugin. Keyboard buttons only post
ordinary text and do not prove authorization. If the product needs automatic DM creation and
role-specific keyboards, compose `AdminUserOnboardingPlugin` beside `AdminUsersPlugin` and map each
role to a keyboard. Disabling a user should remove the owned keyboard; manual text must still reach
the same protected handler and receive a visible denial.

## Verification

Prove at least:

- admin can list, add, change role, and disable users;
- operator/viewer behavior matches the declared permissions;
- the last enabled protected administrator cannot be disabled or demoted;
- disabled and unknown users resolve to no role;
- exact text entry is protected even without the keyboard plugin;
- SQL storage survives engine/process recreation when selected;
- role-specific buttons are hidden, but direct handler/service calls are denied independently;
- audit records contain actor/action/target and no secrets.
