from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from mattergram.contrib.admin_users import (
    DEFAULT_PERMISSIONS,
    DEFAULT_ROLES,
    AdminUser,
    AdminUsersService,
    AuditRecord,
    TeamUser,
)
from mattergram.contrib.admin_users.sqlalchemy import SQLAlchemyAdminUserRepository


class Directory:
    async def list_users(self) -> tuple[TeamUser, ...]:
        return (
            TeamUser("mm_owner", "owner"),
            TeamUser("mm_operator", "operator"),
        )


@pytest.mark.asyncio
async def test_sqlalchemy_repository_persists_updates_and_audit(tmp_path) -> None:
    database = tmp_path / "admin-users.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    try:
        await SQLAlchemyAdminUserRepository.create_schema(engine)
        repository = SQLAlchemyAdminUserRepository.from_engine(engine)
        owner = AdminUser("usr_owner", "mm_owner", "owner", "admin")
        await repository.save_user(owner)
        await repository.save_user(AdminUser("usr_owner", "mm_owner", "renamed", "viewer", False))
        await repository.write(
            AuditRecord(
                created_at="2026-11-16T00:00:00+00:00",
                actor_id="mm_owner",
                action="admin_users.update",
                target_user_id="usr_owner",
                details=(("role", "viewer"),),
            )
        )

        assert await repository.get_user("usr_owner") == AdminUser(
            "usr_owner", "mm_owner", "renamed", "viewer", False
        )
        assert await repository.get_by_mattermost_user_id("mm_owner") == AdminUser(
            "usr_owner", "mm_owner", "renamed", "viewer", False
        )
        assert await repository.list_users() == (
            AdminUser("usr_owner", "mm_owner", "renamed", "viewer", False),
        )
        assert (await repository.list_audit_records())[0].details == (("role", "viewer"),)
    finally:
        await engine.dispose()

    reopened = create_async_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    try:
        repository = SQLAlchemyAdminUserRepository.from_engine(reopened)
        assert (await repository.get_user("usr_owner")) is not None
    finally:
        await reopened.dispose()


@pytest.mark.asyncio
async def test_sqlalchemy_repository_enforces_unique_mattermost_identity(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'race.sqlite3').as_posix()}")
    try:
        await SQLAlchemyAdminUserRepository.create_schema(engine)
        repository = SQLAlchemyAdminUserRepository.from_engine(engine)
        results = await asyncio.gather(
            repository.save_user(AdminUser("first", "same-mm-id", "first", "operator")),
            repository.save_user(AdminUser("second", "same-mm-id", "second", "viewer")),
            return_exceptions=True,
        )

        assert sum(result is None for result in results) == 1
        assert sum(isinstance(result, ValueError) for result in results) == 1
        assert len(await repository.list_users()) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlalchemy_repository_runs_complete_admin_users_service_flow(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'service.sqlite3').as_posix()}")
    try:
        await SQLAlchemyAdminUserRepository.create_schema(engine)
        repository = SQLAlchemyAdminUserRepository.from_engine(engine)
        service = AdminUsersService(
            repository,
            Directory(),
            permissions=DEFAULT_PERMISSIONS,
            roles=DEFAULT_ROLES,
            initial_admin_ids=("mm_owner",),
            audit_sink=repository,
        )

        await service.bootstrap()
        created = await service.add_user(
            "mm_owner",
            mattermost_user_id="mm_operator",
            role="operator",
            enabled=True,
        )

        assert await service.resolve_role("mm_owner") == "admin"
        assert await service.resolve_role("mm_operator") == "operator"
        assert await repository.get_user(created.id) == created
        assert [record.action for record in await repository.list_audit_records()] == [
            "admin_users.bootstrap",
            "admin_users.add",
        ]
    finally:
        await engine.dispose()
