"""Small role-based access primitives for declarative handler authorization."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection
from typing import Any, Protocol, runtime_checkable

from mattergram.dispatcher.flags import get_flag
from mattergram.dispatcher.middlewares.base import BaseMiddleware
from mattergram.types import Event

REQUIRED_ROLES_FLAG = "required_roles"
DEFAULT_ACCESS_DENIED_TEXT = "Недостаточно прав для этого действия"


class AccessDenied(PermissionError):
    """Raised when an enabled application role does not authorize an action."""


@runtime_checkable
class RoleResolver(Protocol):
    """Resolve one enabled application role for a Mattermost actor."""

    async def resolve_role(self, actor_id: str) -> str | None: ...


def normalize_required_roles(value: object) -> tuple[str, ...]:
    """Validate and normalize the value of the ``required_roles`` handler flag."""

    if isinstance(value, str):
        roles = (value,)
    elif isinstance(value, Collection):
        roles = tuple(value)
    else:
        raise TypeError("required_roles must be a role string or a collection of role strings")
    if not roles:
        raise ValueError("required_roles must contain at least one role")
    if any(not isinstance(role, str) or not role.strip() for role in roles):
        raise TypeError("required_roles must contain only nonempty role strings")
    if len(set(roles)) != len(roles):
        raise ValueError("required_roles must not contain duplicates")
    return roles


class RBAC:
    """Resolve roles and enforce explicit role sets outside the dispatcher too."""

    def __init__(self, resolver: RoleResolver) -> None:
        if not isinstance(resolver, RoleResolver):
            raise TypeError("resolver must implement async resolve_role(actor_id)")
        self.resolver = resolver

    async def role_for(self, actor_id: str) -> str | None:
        if not isinstance(actor_id, str) or not actor_id:
            raise ValueError("actor_id must be a nonempty string")
        role = await self.resolver.resolve_role(actor_id)
        if role is not None and (not isinstance(role, str) or not role):
            raise TypeError("RoleResolver must return a nonempty string or None")
        return role

    async def require_role(self, actor_id: str, *roles: str) -> str:
        required = normalize_required_roles(roles)
        role = await self.role_for(actor_id)
        if role not in required:
            raise AccessDenied(
                f"One of the application roles {required!r} is required for actor {actor_id!r}"
            )
        return role


class _AnswerableEvent(Protocol):
    user_id: str

    async def answer(self, text: str) -> object: ...


class RBACMiddleware(BaseMiddleware):
    """Enforce ``required_roles`` flags on native Mattergram handlers."""

    def __init__(
        self,
        resolver: RoleResolver,
        *,
        denied_text: str = DEFAULT_ACCESS_DENIED_TEXT,
    ) -> None:
        if not isinstance(denied_text, str) or not denied_text.strip():
            raise ValueError("denied_text must be a nonempty string")
        self.rbac = RBAC(resolver)
        self.denied_text = denied_text

    async def __call__(
        self,
        handler: Callable[[Event, dict[str, Any]], Awaitable[Any]],
        event: Event,
        data: dict[str, Any],
    ) -> Any:
        value = get_flag(data, REQUIRED_ROLES_FLAG)
        if value is None:
            return await handler(event, data)

        required = normalize_required_roles(value)
        actor_id = getattr(event, "user_id", None)
        if not isinstance(actor_id, str) or not actor_id:
            raise RuntimeError("A handler with required_roles needs an event with user_id")
        try:
            role = await self.rbac.require_role(actor_id, *required)
        except AccessDenied:
            answer = getattr(event, "answer", None)
            if answer is None or not callable(answer):
                raise RuntimeError("A handler with required_roles needs an event with answer(text)") from None
            await answer(self.denied_text)
            return None

        data["actor_role"] = role
        return await handler(event, data)


__all__ = [
    "AccessDenied",
    "DEFAULT_ACCESS_DENIED_TEXT",
    "RBAC",
    "RBACMiddleware",
    "REQUIRED_ROLES_FLAG",
    "RoleResolver",
    "normalize_required_roles",
]
