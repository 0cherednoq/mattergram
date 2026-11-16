# Access control

Use the library's familiar core mechanism. Do not invent a feature-specific policy DSL.

## Canonical handler gate

```python
from mattergram.security import REQUIRED_ROLES_FLAG, RBACMiddleware

router.message.middleware(RBACMiddleware(role_resolver))


@router.message(
    exact_text_filter,
    flags={REQUIRED_ROLES_FLAG: ("admin", "operator")},
)
async def open_feature(message, actor_role: str) -> None:
    ...
```

`RoleResolver.resolve_role(actor_id)` returns one enabled app-role or `None`. Repository failures
must propagate. Disabled, missing, or unknown-role actors resolve to `None` and are denied visibly.

`AdminUsersService` is a ready resolver. `AdminUsersPlugin` already wires middleware for its own
entries. Custom feature routers wire their own middleware explicitly.

## Boundaries

- Flags express a coarse OR-list of roles only.
- Exact permissions, ownership, tenant scope, last-admin invariants, and mutations stay in services.
- Dialog actions and background jobs call the same protected services.
- Hiding a button or omitting a keyboard row is UX, not authorization.
- Keyboard presses and manually typed text reach the same protected `Message` handler.
- Do not infer app roles from Mattermost system/team roles or usernames.
- Do not add an admin fallback when the repository is empty or unavailable.

See repository `docs/access-control.md` for the complete public API and decorator form.
