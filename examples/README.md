# Examples

`create_post.py` is a runnable native-client example checked against the owned
Mattermost 11.1.0 stand. It reads MM_URL/MM_BOT_TOKEN from environment and accepts
channel_id/text plus an optional --root-id. It prints the real returned post ID.

`routing_bot.py`, `redis_bot.py`, `http_bot.py`, `form_bot.py` and the UI
examples cover the implemented Router/Dispatcher, persistence, HTTP, forms and
native UI paths. Their required environment variables are declared in each
file. See `docs/native-client.md` and the feature-specific guides for verified
scope.

`bootstrap.py` performs declared bot/team/channel/membership/command
reconciliation when `MM_SETUP_TOKEN` is present. With the same persistent state
and no setup token, it loads only the saved non-admin runtime and command
credentials. See `docs/bootstrap.md` for ownership and recovery rules.

`native_api.py` uses the reviewed generated operation registry for read-only
identity and channel-post calls. It owns a separate low-level client and does
not bypass the overlay with arbitrary paths. See `docs/native-api.md`.

`upload_document.py CHANNEL_ID PATH [--text TEXT]` streams one local file through
the high-level facade and prints only the returned post ID. It reads MM_URL and
MM_BOT_TOKEN from the environment. See `docs/high-level-api.md` for ownership
and uncertain-write rules.

`showcase_bot.py` is a full interactive example: one multi-page post contains
two independently paginated and filtered Markdown tables, three action groups,
three native form workflows, and live updates triggered by WebSocket events and
written through REST. See `docs/showcase.md` for configuration, lifecycle rules,
and the production storage boundary.

`dialog_navigation.py` is the focused declarative example for one editable
post. It shows branch-aware Back, a shared destination window, two independently
filtered and paginated tables in one card, object-local action grouping, and a
declarative `ButtonGrid(ActionRow(...), ...)` whose exact rows activate on a
proven Mattermost Blocks profile.
See `docs/dialog-navigation.md` for runtime wiring and Redis behavior.

`dialog_navigation_bot.py` is the runnable local stand for Mattermost 11.11+
with `MmBlocksEnabled`. Its orders screen renders six native Blocks rows with
text columns and local Edit/Delete buttons; edit opens a typed form and delete
requires confirmation. The summary keeps its Markdown tables and independent
filter/pager state, while global navigation remains separate. The demo also
covers an opaque-cursor feed and a WebSocket-triggered edit of the same post.
See `docs/dialog-ui-design.md` for the component-selection rules.

The generated [examples index](../docs/examples.md) records every example's
minimum profile, permissions, optional dependencies, environment variables,
assertions, evidence, and actual verification level.
