# <Component name>

## Purpose

- Used by:
- User decision it supports:
- Required capability:
- Fallback:
- Russian display terms and internal-value mappings:

## Layout and states

- Post container: none / one shared `PostContainer` for related list + detail + status + navigation
- Accent container: none / important status / warning / destructive confirmation / standalone summary
- If accented, why plain post content is insufficient:
- Inner groups: borderless / collapsible / independently accented with justification
- Object actions: leading primary / trailing `✏️` edit, `⚙️` settings, `🗑️` delete; record tooltip and
  full text legacy fallback for every icon-only action
- Entity detail: parent context not repeated / two-column primary fields / nested lifecycle state

## Collection contract (if applicable)

- Filter-left / sort-right controls:
- Default and stable tie-breaker sort:
- Backend query/count boundary:
- Page size: 5 / justified override:
- Pager: `«  ‹  1 / N  ›  »`
- Filter, sort, page, and selection preservation:
- Empty state:

| State | Visible content | Actions |
|---|---|---|
| Default |  |  |
| Disabled |  |  |
| Loading |  |  |
| Error |  |  |

## Interaction contract

- Input:
- Output:
- Edit or new post:
- Stable IDs:

## Access contract

- Visible to roles:
- Handler gate: `flags={REQUIRED_ROLES_FLAG: (...)}` / inherited protected dialog entry
- Service permission or ownership check:
- Disabled/missing-user result:
- Lost-permission/stale-action result:
- Audit event for writes:
- Keyboard visibility rule: UX only; never authorization

## Content rules

- Label pattern:
- Length limits:
- Accessibility:
- Sensitive data:
- Copyable values and client proof:
- Date/time format and timezone:

## Verification

- Unit/contract:
- Live client:
