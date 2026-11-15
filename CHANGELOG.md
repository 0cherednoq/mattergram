# Changelog

## Unreleased — 0.1.0a1

- Pin donor and protocol references with per-file SHA-256 checks.
- Add read-only AST inventory and a reviewed routing/filter/middleware/FSM port manifest.
- Add migration-tool tests and an installable development scaffold.
- Add immutable staging extraction, token-preserving import codemods, pinned semantic patch replay and a strict static port audit.
- Verify real allowlist repeatability and unchanged maintained sources; report pending SDK adaptations explicitly.
- Serialize generated JSON with LF bytes on every OS so manifest hashes remain portable.
- Implement native Bot/User/Channel/Message, posted normalization, get_me/createPost and answer/reply with owned HTTP lifecycle and explicit ambiguous-write errors.
- Verify the native client on a pinned Mattermost 11.1.0 stand, a local HTTP test server and a Postman fault simulation.
- Port 18 reviewed routing/filter/middleware/DI/FSM modules with exact patch replay, native boundaries and a passing static audit.
- Implement authenticated WebSocket ingress, bounded backpressure, reconnect, error routing, cancellation/startup/shutdown cleanup and self-event suppression.
- Add native string FSM keys, MemoryStorage isolation and 13 adapted upstream behavioral suites with pinned provenance and explicit optional-profile exclusions.
- Verify SDK-owned WebSocket through Router/F/DI/handler/answer and error observers on real Mattermost; exercise local WS and installed-wheel consumers separately.

- Add optional RedisStorage/RedisEventIsolation with explicit pool ownership, TTL, native session keys, cancellation-aware bounded leases and strict stored data.
- Verify real Redis minimum/latest clients, concurrent FSM updates, lock cancellation and joined Mattermost/Redis state transitions; test base and extra installed artifacts separately.

- Add strict native Form/Blocks declarations, rich attachments, signed legacy actions and a shared tree/action-registry compiler connected to Bot.send_message.
- Pin exact 11.1/11.7/11.11/12.0.2 UI protocol references, versioned datetime serializers and explicit unknown client/feature-flag gates. Client rendering acceptance remains unfinished.

- Add managed native open_dialog, form events, immediate validation/lookup/refresh/continuation responses and schema-directed submission values.
- Add scoped opaque form capabilities, bounded Memory/optional Redis replay records, uncertain-outcome reconciliation and native modal-stack guards; verify real Mattermost API/actor-WS and independent Redis workers.

- Add authored Slack Block Kit and Adaptive Cards supported-subset adapters to native declarations, exact action/data bindings, explicit unsupported-property/version errors, resource limits and unchanged compiler capability/signing gates.
- Add an executable offline input-format example and installed-artifact checks for both adapters; actual client rendering remains a separate gate.

- Verify recorded Windows/Chrome 157 web scenarios on pinned Mattermost 12.0.2: all declared Form/Block types, source adapters, validation, wizard, lookup/refresh, real uploads and nested forms. Add reproducible isolated TLS/UI tooling and sanitized screenshot evidence; the full client matrix remains open.
- Fix native web continuation callback identity, opening-state lookup/cancel after transitions, lookup option-object parsing and refresh prefill. Preserve explicit defaults and field metadata across stored snapshots while retaining stale-write and scope checks.

- Verify additional 12.0.2 calendar bounds, alternate timezone, inline label positions and legacy controls; record separate 11.11 web forms/Blocks/imports/file/child/callback evidence. Add isolated pinned 11.7/11.11 stand profiles and explicit per-version development fixture selection. Native desktop/mobile acceptance remains open.

- Reject unroutable legacy action IDs before HTTP writes: 11.1/11.7 require alphanumeric IDs, while explicit 11.11/12 profiles permit underscores and hyphens. Preserve native-issued IDs and application IDs without rewriting; add legacy declaration schema and regressions from a real 11.7 web failure.

The bot SDK and dialog extension are under development. No release has been published.
