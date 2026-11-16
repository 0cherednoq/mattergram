"""SQLAlchemy 2.0 async persistence for the Admin Users plugin."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    insert,
    select,
    update,
)
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .models import AdminUser, AuditRecord

admin_users_metadata = MetaData()

_USERS = Table(
    "mattergram_admin_users",
    admin_users_metadata,
    Column("id", String(64), primary_key=True),
    Column("mattermost_user_id", String(64), nullable=False),
    Column("username", String(191), nullable=False),
    Column("role", String(128), nullable=False),
    Column("enabled", Boolean, nullable=False),
    UniqueConstraint("mattermost_user_id", name="uq_mattergram_admin_users_mattermost_user_id"),
)

_AUDIT = Table(
    "mattergram_admin_user_audit",
    admin_users_metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("created_at", String(64), nullable=False),
    Column("actor_id", String(64), nullable=False),
    Column("action", String(191), nullable=False),
    Column("target_user_id", String(64), nullable=False),
    Column("details", JSON, nullable=False),
)


class SQLAlchemyAdminUserRepository:
    """Durable async repository and audit sink backed by SQLAlchemy 2.0.

    A fresh ``AsyncSession`` is created for every operation. The application
    owns the engine lifecycle and production schema migrations.
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        if not isinstance(sessions, async_sessionmaker):
            raise TypeError("sessions must be a SQLAlchemy async_sessionmaker")
        self.sessions = sessions

    @classmethod
    def from_engine(cls, engine: AsyncEngine) -> SQLAlchemyAdminUserRepository:
        """Create a repository with one new, non-expiring session per operation."""

        if not isinstance(engine, AsyncEngine):
            raise TypeError("engine must be a SQLAlchemy AsyncEngine")
        return cls(async_sessionmaker(engine, expire_on_commit=False))

    @staticmethod
    async def create_schema(engine: AsyncEngine) -> None:
        """Create tables for local use; production should run a real migration."""

        if not isinstance(engine, AsyncEngine):
            raise TypeError("engine must be a SQLAlchemy AsyncEngine")
        async with engine.begin() as connection:
            await connection.run_sync(admin_users_metadata.create_all)

    async def list_users(self) -> tuple[AdminUser, ...]:
        async with self.sessions() as session:
            rows = (await session.execute(select(_USERS).order_by(_USERS.c.id))).mappings()
            return tuple(self._user(row) for row in rows)

    async def get_user(self, user_id: str) -> AdminUser | None:
        self._identity(user_id, "user_id")
        async with self.sessions() as session:
            row = (
                (await session.execute(select(_USERS).where(_USERS.c.id == user_id))).mappings().one_or_none()
            )
            return None if row is None else self._user(row)

    async def get_by_mattermost_user_id(self, mattermost_user_id: str) -> AdminUser | None:
        self._identity(mattermost_user_id, "mattermost_user_id")
        async with self.sessions() as session:
            row = (
                (
                    await session.execute(
                        select(_USERS).where(_USERS.c.mattermost_user_id == mattermost_user_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
            return None if row is None else self._user(row)

    async def save_user(self, user: AdminUser) -> None:
        if not isinstance(user, AdminUser):
            raise TypeError("user must be an AdminUser")
        values = {
            "id": user.id,
            "mattermost_user_id": user.mattermost_user_id,
            "username": user.username,
            "role": user.role,
            "enabled": user.enabled,
        }
        try:
            async with self.sessions.begin() as session:
                duplicate = await session.scalar(
                    select(_USERS.c.id).where(
                        _USERS.c.mattermost_user_id == user.mattermost_user_id,
                        _USERS.c.id != user.id,
                    )
                )
                if duplicate is not None:
                    raise ValueError("Mattermost user ID is already assigned")
                exists = await session.scalar(select(_USERS.c.id).where(_USERS.c.id == user.id))
                if exists is None:
                    await session.execute(insert(_USERS).values(**values))
                else:
                    await session.execute(update(_USERS).where(_USERS.c.id == user.id).values(**values))
        except IntegrityError:
            raise ValueError("Admin user ID or Mattermost user ID is already assigned") from None

    async def write(self, record: AuditRecord) -> None:
        """Persist one plugin audit record, satisfying the ``AuditSink`` protocol."""

        if not isinstance(record, AuditRecord):
            raise TypeError("record must be an AuditRecord")
        async with self.sessions.begin() as session:
            await session.execute(
                insert(_AUDIT).values(
                    created_at=record.created_at,
                    actor_id=record.actor_id,
                    action=record.action,
                    target_user_id=record.target_user_id,
                    details=[list(item) for item in record.details],
                )
            )

    async def list_audit_records(self) -> tuple[AuditRecord, ...]:
        """Return persisted audit records in commit order."""

        async with self.sessions() as session:
            rows = (await session.execute(select(_AUDIT).order_by(_AUDIT.c.sequence))).mappings()
            result: list[AuditRecord] = []
            for row in rows:
                raw_details = row["details"]
                if not isinstance(raw_details, Sequence) or isinstance(raw_details, (str, bytes)):
                    raise RuntimeError("Stored admin user audit details are invalid")
                details: list[tuple[str, str]] = []
                for item in raw_details:
                    if (
                        not isinstance(item, Sequence)
                        or isinstance(item, (str, bytes))
                        or len(item) != 2
                        or not all(isinstance(value, str) for value in item)
                    ):
                        raise RuntimeError("Stored admin user audit details are invalid")
                    details.append((item[0], item[1]))
                result.append(
                    AuditRecord(
                        created_at=row["created_at"],
                        actor_id=row["actor_id"],
                        action=row["action"],
                        target_user_id=row["target_user_id"],
                        details=tuple(details),
                    )
                )
            return tuple(result)

    @staticmethod
    def _identity(value: str, name: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a nonempty string")

    @staticmethod
    def _user(row: RowMapping) -> AdminUser:
        return AdminUser(
            id=row["id"],
            mattermost_user_id=row["mattermost_user_id"],
            username=row["username"],
            role=row["role"],
            enabled=row["enabled"],
        )


__all__ = ["SQLAlchemyAdminUserRepository", "admin_users_metadata"]
