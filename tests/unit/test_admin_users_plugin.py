from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

import pytest

from mattergram import Bot, Dispatcher
from mattergram.contrib.admin_users import (
    ADMIN_USERS_RUNTIME,
    DEFAULT_PERMISSIONS,
    DEFAULT_ROLES,
    AdminUser,
    AdminUserChange,
    AdminUsersInputError,
    AdminUsersPanel,
    AdminUsersPlugin,
    AdminUsersService,
    AuditRecord,
    MemoryAdminUserRepository,
    TeamUser,
)
from mattergram.fsm.storage.base import StorageKey
from mattergram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from mattergram.plugins import PluginManager
from mattergram.security import REQUIRED_ROLES_FLAG, RBACMiddleware
from mattergram.types import Channel, Message
from mattergram_dialog import DialogManager, DialogRegistry, DialogStorage, ShowMode
from mattergram_dialog.rendering import FormRender, PostRender, RenderPlan


class Directory:
    def __init__(self) -> None:
        self.users = (
            TeamUser("owner-id", "owner", "Owner"),
            TeamUser("alice-id", "alice", "Alice Example"),
            TeamUser("bob-id", "bob"),
            TeamUser("bot-id", "helper", is_bot=True),
        )
        self.calls = 0

    async def list_users(self) -> tuple[TeamUser, ...]:
        self.calls += 1
        return self.users


class Audit:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    async def write(self, record: AuditRecord) -> None:
        self.records.append(record)


class Changes:
    def __init__(self) -> None:
        self.records: list[AdminUserChange] = []

    async def write(self, change: AdminUserChange) -> None:
        self.records.append(change)


class Runtime:
    def __init__(self) -> None:
        self.responses: list[tuple[str, object]] = []

    async def start(self, event: Any, state: object, data: object = None, **kwargs: Any) -> None:
        del data, kwargs
        self.responses.append((event.channel_id, state))

    async def respond(self, event: Any, state: object, data: object = None) -> None:
        del data
        self.responses.append((event.channel_id, state))


@dataclass
class SpyRenderer:
    calls: list[tuple[RenderPlan, ShowMode]]

    async def render(self, plan: RenderPlan, stack: Any, mode: ShowMode) -> None:
        self.calls.append((plan, mode))
        if isinstance(plan, PostRender):
            stack.last_post_id = stack.last_post_id or "admin-users-post"


def service(
    *,
    users: tuple[AdminUser, ...] = (),
) -> tuple[AdminUsersService, MemoryAdminUserRepository, Directory, Audit]:
    repository = MemoryAdminUserRepository(users=users)
    directory = Directory()
    audit = Audit()
    value = AdminUsersService(
        repository,
        directory,
        permissions=DEFAULT_PERMISSIONS,
        roles=DEFAULT_ROLES,
        initial_admin_ids=("owner-id",),
        audit_sink=audit,
    )
    return value, repository, directory, audit


@pytest.mark.asyncio
async def test_bootstrap_is_explicit_idempotent_and_authorization_is_server_side() -> None:
    users, repository, _, audit = service()

    with pytest.raises(PermissionError):
        await users.list_users("owner-id")

    await users.bootstrap()
    await users.bootstrap()
    stored = await repository.list_users()
    assert len(stored) == 1
    assert stored[0].mattermost_user_id == "owner-id"
    assert stored[0].role == "admin"
    assert len(audit.records) == 1

    with pytest.raises(PermissionError):
        await users.list_users("outsider")
    assert [user.username for user in await users.list_users("owner-id")] == ["owner"]


@pytest.mark.asyncio
async def test_add_revalidates_team_member_and_reactivates_same_record() -> None:
    users, repository, _, audit = service()
    await users.bootstrap()

    created = await users.add_user(
        "owner-id",
        mattermost_user_id="alice-id",
        role="operator",
        enabled=True,
    )
    for unavailable in ("bot-id", "outside-id"):
        with pytest.raises(AdminUsersInputError, match="invalid"):
            await users.add_user(
                "owner-id",
                mattermost_user_id=unavailable,
                role="viewer",
                enabled=True,
            )

    disabled = await users.update_user(
        "owner-id",
        created.id,
        role="operator",
        enabled=False,
    )
    assert not disabled.enabled
    reactivated = await users.add_user(
        "owner-id",
        mattermost_user_id="alice-id",
        role="viewer",
        enabled=True,
    )

    assert reactivated.id == created.id
    assert reactivated.role == "viewer"
    assert len(await repository.list_users()) == 2
    assert [record.action for record in audit.records][-3:] == [
        "admin_users.add",
        "admin_users.update",
        "admin_users.reactivate",
    ]


@pytest.mark.asyncio
async def test_last_protected_admin_and_role_permissions_are_enforced() -> None:
    users, repository, _, _ = service()
    await users.bootstrap()
    owner = await repository.get_by_mattermost_user_id("owner-id")
    assert owner is not None

    with pytest.raises(AdminUsersInputError) as error:
        await users.update_user("owner-id", owner.id, role="operator", enabled=True)
    assert "последнего администратора" in error.value.field_errors["enabled"]

    alice = await users.add_user(
        "owner-id",
        mattermost_user_id="alice-id",
        role="admin",
        enabled=True,
    )
    updated = await users.update_user("owner-id", owner.id, role="operator", enabled=True)
    assert updated.role == "operator"
    assert alice.enabled

    with pytest.raises(PermissionError, match="admin_users.manage"):
        await users.add_user(
            "owner-id",
            mattermost_user_id="bob-id",
            role="viewer",
            enabled=True,
        )


@pytest.mark.asyncio
async def test_operator_has_no_application_user_administration_permissions() -> None:
    users, repository, _, _ = service()
    await users.bootstrap()
    operator = await users.add_user(
        "owner-id",
        mattermost_user_id="alice-id",
        role="operator",
        enabled=True,
    )

    assert await users.permissions_for("alice-id") == frozenset()
    with pytest.raises(PermissionError, match="admin_users.view"):
        await users.list_users("alice-id")
    with pytest.raises(PermissionError, match="admin_users.manage"):
        await users.add_user(
            "alice-id",
            mattermost_user_id="bob-id",
            role=users.default_role,
            enabled=True,
        )
    with pytest.raises(PermissionError, match="admin_users.manage"):
        await users.update_user("alice-id", operator.id, role="admin", enabled=True)


@pytest.mark.asyncio
async def test_committed_user_changes_are_emitted_without_ui_dependency() -> None:
    repository = MemoryAdminUserRepository()
    directory = Directory()
    changes = Changes()
    users = AdminUsersService(
        repository,
        directory,
        permissions=DEFAULT_PERMISSIONS,
        roles=DEFAULT_ROLES,
        initial_admin_ids=("owner-id",),
        change_sink=changes,
    )
    await users.bootstrap()

    created = await users.add_user(
        "owner-id",
        mattermost_user_id="alice-id",
        role="operator",
        enabled=True,
    )
    await users.update_user("owner-id", created.id, role="viewer", enabled=True)

    assert [change.action for change in changes.records] == [
        "admin_users.add",
        "admin_users.update",
    ]
    assert changes.records[0].previous is None
    assert changes.records[1].previous == created


@pytest.mark.asyncio
async def test_list_uses_current_directory_username() -> None:
    users, _, directory, _ = service()
    await users.bootstrap()
    directory.users = (
        TeamUser("owner-id", "renamed-owner"),
        *directory.users[1:],
    )

    listed = await users.list_users("owner-id")
    assert listed[0].username == "renamed-owner"


@pytest.mark.asyncio
async def test_plugin_routes_manual_exact_dm_text_without_keyboard_dependency() -> None:
    repository = MemoryAdminUserRepository()
    directory = Directory()
    plugin = AdminUsersPlugin(
        repository=repository,
        directory=directory,
        initial_admin_ids=("owner-id",),
        messages=("Пользователи",),
    )
    dispatcher = Dispatcher()
    plugins = PluginManager(
        bot=Bot(token="test-token", base_url="https://mattermost.test"),
        dispatcher=dispatcher,
        dialog_registry=DialogRegistry(),
        plugins=(plugin,),
    )
    context = plugins.setup()
    runtime = Runtime()
    dispatcher.workflow_data[ADMIN_USERS_RUNTIME.name] = runtime
    await plugins.startup()

    message = Message.from_wire(
        {
            "id": "post123",
            "channel_id": "direct123",
            "user_id": "owner-id",
            "message": "Пользователи",
            "create_at": 1,
        }
    )
    message._channel = Channel.from_wire({"id": "direct123", "type": "D"})
    handler = plugin.router.message.handlers[0]
    assert handler.flags[REQUIRED_ROLES_FLAG] == ("admin", "viewer")
    assert plugin.router.slash_command.handlers[0].flags[REQUIRED_ROLES_FLAG] == (
        "admin",
        "viewer",
    )
    assert len(plugin.router.message.middleware) == 1
    assert isinstance(plugin.router.message.middleware[0], RBACMiddleware)
    assert await plugin.service.resolve_role("owner-id") == "admin"
    assert await plugin.service.resolve_role("missing-id") is None
    accepted, _ = await handler.check(message)
    assert accepted
    await handler.call(
        message,
        admin_users_service=plugin.service,
        admin_users_runtime=runtime,
    )
    assert runtime.responses == [("direct123", AdminUsersPanel.users)]
    assert [spec.trigger for spec in context.command_manifest.commands] == ["app-users"]

    message._channel = Channel.from_wire({"id": "public123", "type": "O"})
    rejected, _ = await handler.check(message)
    assert not rejected
    assert "composer" not in inspect.getsource(plugin.__class__)
    await plugins.shutdown()


@pytest.mark.asyncio
async def test_dialog_rows_forms_and_depth_are_declarative() -> None:
    repository = MemoryAdminUserRepository()
    directory = Directory()
    plugin = AdminUsersPlugin(
        repository=repository,
        directory=directory,
        initial_admin_ids=("owner-id",),
    )
    await plugin.service.bootstrap()
    storage = MemoryStorage()
    isolation = SimpleEventIsolation()
    renderer = SpyRenderer([])
    key = StorageKey(
        instance_id="https://instance.test",
        bot_id="bot",
        channel_id="direct",
        user_id="owner-id",
        destiny="mattergram_dialog",
    )
    try:
        async with DialogManager(
            DialogStorage(storage, isolation),
            key,
            DialogRegistry(plugin.dialog),
            renderer,
            actor_id="owner-id",
        ) as manager:
            await manager.start(AdminUsersPanel.users)
            users_post = renderer.calls[-1][0]
            assert isinstance(users_post, PostRender)
            assert [control.id for control in users_post.control_groups[0].controls] == [
                "adminUsersPluginAdd"
            ]
            assert users_post.control_groups[0].border is False
            assert [len(row) for row in users_post.control_groups[1].control_rows] == [3, 6]
            assert users_post.control_groups[1].border is False

            await manager.switch_to(AdminUsersPanel.add)
            add_form = renderer.calls[-1][0]
            assert isinstance(add_form, FormRender)
            assert [field.name for field in add_form.form.elements] == [
                "mattermost_user_id",
                "role",
                "enabled",
            ]
            assert add_form.form.elements[1].default == "operator"
            assert add_form.form.notify_on_cancel

            await manager.back()
            selector = manager.find("adminUsersPluginSelect")
            owner = (await repository.list_users())[0]
            selector.apply(owner.id, manager)
            await manager.switch_to(AdminUsersPanel.edit)
            edit_form = renderer.calls[-1][0]
            assert isinstance(edit_form, FormRender)
            assert [field.name for field in edit_form.form.elements] == ["role", "enabled"]
            assert edit_form.form.notify_on_cancel
    finally:
        await storage.close()
        await isolation.close()


@pytest.mark.asyncio
async def test_operator_cannot_open_application_user_dialog() -> None:
    repository = MemoryAdminUserRepository(
        users=(
            AdminUser("owner", "owner-id", "owner", "admin"),
            AdminUser("alice", "alice-id", "alice", "operator"),
        )
    )
    plugin = AdminUsersPlugin(
        repository=repository,
        directory=Directory(),
        initial_admin_ids=("owner-id",),
    )
    await plugin.service.bootstrap()

    with pytest.raises(PermissionError, match="admin_users.view"):
        await plugin.service.list_users("alice-id")
    assert await plugin.service.permissions_for("alice-id") == frozenset()
