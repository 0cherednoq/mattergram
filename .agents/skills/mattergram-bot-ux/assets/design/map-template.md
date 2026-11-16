# <Bot or feature> UX map

## Scope

- Goal:
- Actors and permissions:
- Out of scope:

## Access model

Use app-scoped roles unless the feature explicitly administers Mattermost system/team roles. For a
new protected bot, start from `AdminUsersPlugin`; do not recreate user lists, role forms,
last-admin protection, or audit writes in feature code.

| Role | Entry surfaces | Allowed tasks | Service permissions | Hidden surfaces |
|---|---|---|---|---|
| admin |  |  |  |  |
| operator |  |  |  |  |
| viewer |  |  |  |  |

- User management: `AdminUsersPlugin` / existing project access system / not required
- Repository: `SQLAlchemyAdminUserRepository` / custom durable repository / memory for tests only
- Initial administrator IDs:
- Handler gate: `required_roles` values
- Write checks: service permission/ownership rules
- On disable: DM/keyboard/session behavior
- Audit destination:

## Entry points

| Entry | Actor | Context | Result |
|---|---|---|---|
|  |  | DM / channel / thread |  |

## Primary tasks

| Task | Start | Success | Action budget | Recovery |
|---|---|---|---:|---|
|  |  |  |  |  |

## Screen map

```text
home
└── <section>
    ├── <list>
    └── <detail>
```

## Surface policy

- Global reply text:
- Local inline actions:
- Native forms:
- Durable notifications:
- Accent containers: list only blocks that require deliberate emphasis; ordinary content stays plain

## Invariants

- Authorization: coarse `required_roles` on entry handlers; exact permission and resource checks in services
- Secrets:
- Send-new versus edit:
- Capability fallbacks:

## Open questions

- None.
