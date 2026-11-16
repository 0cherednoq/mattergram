# Verification and review

Verify the contract that the user experiences, not only the Python functions behind it.

## Before implementation

Confirm these decisions are explicit:

- actor and permission level;
- top user tasks and their entry surfaces;
- expected number of actions for the primary task;
- whether each action edits the current post or creates a new post;
- loading, empty, error, stale, denied, and cancelled states;
- required Mattermost capability and fallback;
- owner of durable state and owner of presentation state.

If the change is narrow, record these decisions in the task or tests. For a multi-screen flow, use the design artifacts from `workflow.md`.

## Static and contract checks

Check the following where applicable:

- exact text filters do not consume unrelated messages;
- a composer-keyboard action still works when the same text is typed manually;
- authorization is enforced in the service on every read and mutation;
- IDs for buttons, windows, lists, and fields are stable and unique in their scope;
- rendered action rows respect target-client limits;
- list boundaries, page resets, and independent lists behave correctly;
- filters and toggles expose their current state;
- empty, missing, denied, and stale records have explicit recovery paths;
- form validation preserves entered data and Cancel returns predictably;
- destructive actions require confirmation and reject duplicate submission;
- progress updates use monotonic revisions and finish in a terminal state;
- terminal job history survives a stale or deleted progress post;
- queued and running cancellation follow the repository contract, and memory queues are confined to tests/local demos;
- secrets, cookies, tokens, and passwords are absent from posts, logs, errors, and fixtures.

Use the repository's existing focused tests first. Add regression tests at the narrowest layer that proves the behavior. Do not replace a protocol or client check with a permissive fake.

## Live-client checks

Run a live Mattermost check when changing client-dependent behavior such as Blocks layout, native dialogs, composer keyboards, stale actions, WebSocket refresh, or accessibility focus.

Record:

- Mattermost server version and client type;
- capability or plugin version;
- exact flow exercised and resulting post IDs when useful;
- whether Web, Desktop, and mobile were actually checked;
- any fallback that was observed rather than assumed.

A passing renderer unit test does not prove a Web/Desktop plugin interaction. Do not report an unrun stand as verified.

## UX acceptance checklist

- Preflight questions and the decision hierarchy in `ui-standards.md` were applied; any exception is
  explicit rather than silently omitted.
- User-facing text is Russian, internal enum/field names are not leaked, and dates use the required
  localized format and an explicit timezone where needed.
- The first screen states what the bot can do and exposes one obvious next action.
- The primary task is reachable without memorizing slash commands.
- Back and Home behave consistently; Cancel is available during data entry.
- Global reply text creates visible input and a fresh response; local inline navigation updates only its own post.
- The user receives acknowledgement quickly for work that can outlast a request.
- Empty and error states explain the next useful action.
- Dangerous actions name the affected object and are harder to trigger accidentally than the safe action.
- Controls remain in stable positions and do not rely on color or emoji alone.
- Collection controls follow filter-left/sort-right order; sorting is stable; backend filtering,
  counting, sorting, and paging preserve per-user state and use 5 rows by default unless justified.
- Related list, selected entity, and lifecycle state form one coherent view without duplicated parent
  identifiers or unnecessary accented child cards.
- Secrets and sensitive values are summarized or masked, never rendered verbatim.

## Review output

For an audit, report findings in this order:

1. blockers that can lose data, bypass authorization, or strand a user;
2. high-friction task-flow problems;
3. missing feedback and recovery states;
4. content, consistency, and accessibility issues;
5. optional polish.

For every finding include the affected task, evidence from the current implementation, the recommended behavior, and the smallest verification that would prove the fix. Distinguish implemented, statically tested, and live-verified behavior.

## Skill self-check scenarios

Use these as smoke prompts when the skill changes:

- Design an account catalog with filters, pagination, details, edit form, and import progress.
- Review a bot that edits one old post for every reply-keyboard action.
- Add an admin role flow without coupling feature handlers to the keyboard plugin.
- Design a long account check when the total number of stages is unknown.
- Wire a production queue consumer to feature-specific Game Admin jobs without inventing a generic `JobsPlugin`.

The guidance should route each prompt to the appropriate reference, preserve Mattermost semantics, and avoid inventing planned APIs.

