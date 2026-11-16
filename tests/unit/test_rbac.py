from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from mattergram import flags
from mattergram.dispatcher.flags import extract_flags_from_object
from mattergram.security import RBAC, REQUIRED_ROLES_FLAG, AccessDenied, RBACMiddleware


class Resolver:
    def __init__(self, roles: dict[str, str | None]) -> None:
        self.roles = roles

    async def resolve_role(self, actor_id: str) -> str | None:
        return self.roles.get(actor_id)


@dataclass
class Handler:
    flags: dict[str, Any]


class Event:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


def test_public_flags_decorator_uses_handler_metadata() -> None:
    @flags.required_roles(("admin", "operator"))
    async def protected() -> None:
        pass

    assert extract_flags_from_object(protected) == {REQUIRED_ROLES_FLAG: ("admin", "operator")}


@pytest.mark.asyncio
async def test_rbac_resolves_and_requires_explicit_roles() -> None:
    rbac = RBAC(Resolver({"owner": "admin", "disabled": None}))

    assert await rbac.role_for("owner") == "admin"
    assert await rbac.require_role("owner", "admin", "operator") == "admin"
    with pytest.raises(AccessDenied):
        await rbac.require_role("owner", "viewer")
    with pytest.raises(AccessDenied):
        await rbac.require_role("disabled", "admin")


@pytest.mark.asyncio
async def test_rbac_middleware_allows_unflagged_and_injects_actor_role() -> None:
    middleware = RBACMiddleware(Resolver({"owner": "admin"}))
    calls: list[tuple[str, str | None]] = []

    async def target(event: Any, data: dict[str, Any]) -> str:
        calls.append((event.user_id, data.get("actor_role")))
        return "ok"

    event = Event("owner")
    assert await middleware(target, event, {"handler": Handler({})}) == "ok"  # type: ignore[arg-type]
    assert (
        await middleware(
            target,
            event,  # type: ignore[arg-type]
            {"handler": Handler({REQUIRED_ROLES_FLAG: ("admin", "operator")})},
        )
        == "ok"
    )
    assert calls == [("owner", None), ("owner", "admin")]
    assert event.answers == []


@pytest.mark.asyncio
async def test_rbac_middleware_denies_visibly_without_calling_handler() -> None:
    middleware = RBACMiddleware(Resolver({"viewer": "viewer"}), denied_text="Нет доступа")

    async def target(event: Any, data: dict[str, Any]) -> None:
        del event, data
        raise AssertionError("denied handler must not run")

    event = Event("viewer")
    result = await middleware(
        target,
        event,  # type: ignore[arg-type]
        {"handler": Handler({REQUIRED_ROLES_FLAG: ("admin", "operator")})},
    )

    assert result is None
    assert event.answers == ["Нет доступа"]


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ((), ValueError),
        (("admin", "admin"), ValueError),
        (("admin", ""), TypeError),
        (42, TypeError),
    ],
)
@pytest.mark.asyncio
async def test_rbac_middleware_rejects_malformed_required_roles(
    value: object,
    error: type[Exception],
) -> None:
    middleware = RBACMiddleware(Resolver({"owner": "admin"}))

    async def target(event: Any, data: dict[str, Any]) -> None:
        del event, data

    with pytest.raises(error):
        await middleware(
            target,
            Event("owner"),  # type: ignore[arg-type]
            {"handler": Handler({REQUIRED_ROLES_FLAG: value})},
        )
