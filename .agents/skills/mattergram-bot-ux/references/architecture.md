# Application architecture for usable bots

UX degrades when transport, navigation, business rules, and persistence are mixed. Keep these boundaries explicit.

## Event ownership

| Concern | Owner |
| --- | --- |
| Slash command, ordinary message, external callback | `Router` handler |
| Dialog action matching, state transitions, form lifecycle | `mattergram_dialog` runtime and widgets |
| Render data | Getter |
| Button/form orchestration | Action/controller |
| Authorization and business invariants | Service |
| Persistence | Repository interface and implementation |
| Mattermost/S3/AI/queue calls | Typed adapter/provider |
| Application assembly | Composition root |

Do not register every dialog button as a global callback handler. Declarative widgets own local actions; Routers own external entry events.

## Feature shape

For a reusable multi-screen feature, prefer:

```text
feature/
├── models.py
├── repository.py
├── service.py
├── states.py
├── getters.py
├── actions.py
├── dialog.py
└── router.py
```

Use fewer files when the feature is small. Split by responsibility, not by one class per file.

`dialog.py` declares windows and widgets. It must not implement persistence or external API clients. `register_feature(...)` or the implemented plugin API binds dependencies and includes the router/dialog once.

## Dependency injection

Use Dispatcher workflow data, middleware data, or an implemented typed plugin/provider contract. Do not hide services, bot tokens, or repositories in mutable module globals.

Module-level routers are acceptable for one Dispatcher per process. If the same feature must attach to multiple Dispatchers, use a router factory only after that requirement is real.

## Keyboard decoupling

Feature handlers never import composer-keyboard types. The composition root owns both declarations:

```python
OPEN_USERS = "Пользователи"

register_admin_users(messages=(OPEN_USERS,), service=users_service)
keyboard = ComposerKeyboard(... message=OPEN_USERS ...)
```

The feature accepts manual text with the same authorization. The keyboard plugin is optional and replaceable.

## State boundaries

- `dialog_data`: actor-scoped mutable UI state such as filters, page, selection, and form draft.
- start data: minimal identifiers and mode flags, never full mutable domain objects.
- repository/service: durable domain state.
- widget data: internal widget state and one-shot action tokens.
- `DialogRef`: durable reference for controlled background refresh, validated against current intent and post.

Namespace independent lists, filters, and selections. Re-fetch domain records before writes. UI state is not a source of truth for permission or object existence.

## Authorization

Authorize every service use case with the actual actor ID. UI visibility is a convenience, not enforcement.

For administration:

- roles are app-scoped unless the task explicitly manages Mattermost roles;
- never infer admin from username, empty repository, or system-admin status without an explicit contract;
- protect the last active administrator in the service transaction;
- revalidate team membership and current permissions at submit/write time;
- separate setup credentials from runtime bot credentials.

## Writes and retries

- Use idempotency keys or application intents for retryable writes.
- Do not automatically repeat an uncertain write unless the protocol proves it is safe.
- Disable or consume one-shot actions before executing a side effect.
- Return a clear conflict when state changed concurrently.
- Keep request/job IDs for recovery and audit.

## Background work

The interaction handler validates, creates a job, captures a safe `DialogRef`, and returns. A worker performs work and publishes monotonic snapshots. The service stores terminal history independently from UI delivery.

Do not redirect a user's active flow from a worker without an explicit product requirement. Prefer updating the owned progress post or sending a terminal notification.

For the implemented Game Admin contracts, production adapters, cancellation, and stale-post behavior, read [background-jobs.md](background-jobs.md).

## Current versus planned APIs

Inspect the checkout before using plugin or callback conveniences. In the current baseline, Routers, Dispatcher DI, `PluginManager`, dialog registry/runtime, `ComposerKeyboard`, manager-only `Button` callbacks, `DialogRef`, and progress update APIs exist. `Button` supports both `callback(manager)` and `callback(event, button, manager)` when the signature is unambiguous.

Game Admin exposes feature-specific job repository/queue ports and a runner. A generic `JobsPlugin`, durable account repository, notifications plugin, settings plugin, S3 provider, and AI provider are not implemented by that feature.

Never generate imports from an architecture plan alone.

