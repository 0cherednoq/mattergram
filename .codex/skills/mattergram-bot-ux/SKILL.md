---
name: mattergram-bot-ux
description: "Design, implement, or audit end-to-end UX for Mattermost bots built with mattergram and mattergram_dialog. Use for bot flows, entry points, persistent composer keyboards, post navigation, lists, forms, admin panels, progress, feedback, error recovery, or requests to make a Mattergram bot convenient. Do not use for backend-only changes with no bot interaction surface."
---

# Mattergram Bot UX

Design the bot around user tasks and Mattermost semantics. Treat navigation depth, click count, recovery, authorization, and client capabilities as testable constraints.

## Source of truth

Before designing or editing:

1. Read the repository `AGENTS.md`, `README.md`, and status instructions.
2. Inspect the current routers, dialogs, services, tests, and applicable UI profile.
3. Prefer implemented mattergram APIs and repository guides over examples from Telegram frameworks.
4. Never present a planned API as available. Verify imports and signatures in the checked-out code.

External bot UX sources are inspiration only. Mattermost semantics win when Telegram behavior differs.

## Choose the work mode

- **New bot or feature:** read [references/workflow.md](references/workflow.md), create a task/flow map, then implement the smallest complete slice.
- **UX review:** inspect the running or declared flow, read [references/verification.md](references/verification.md), and report evidence before changing code unless the user also requested fixes.
- **Targeted improvement:** read only the relevant interaction, component, content, or architecture reference, update the affected design artifact when one exists, implement, and verify.

For a material multi-screen bot, use the `.mattergram-design/` structure described in [references/workflow.md](references/workflow.md). Do not create design files for a trivial one-message handler.

## Required design sequence

1. Name the actors and their permissions.
2. List the 3–7 primary tasks in frequency order.
3. Choose the Mattermost surface for each entry and result: DM, channel, thread, editable post, native form, or ephemeral response.
4. Draw the screen/flow map and count clicks for frequent tasks.
5. Choose components from [references/component-selection.md](references/component-selection.md).
6. Define success, empty, validation, stale, permission-denied, retry, and cancellation states.
7. Separate Router entry events, dialog navigation, service rules, repositories, and external adapters using [references/architecture.md](references/architecture.md).
8. Implement with stable IDs and explicit capability assumptions.
9. Verify contracts and, when visual/client behavior changed, the real target client.

## Mattermost interaction invariants

- A composer keyboard is an optional UI producer of ordinary user text. Feature handlers process `Message`; they do not depend on button IDs or on the keyboard plugin being installed.
- Manual entry of the same exact text must invoke the same authorized handler as a keyboard press.
- A top-level reply-text command creates a fresh bot post when the application needs a visible request/result pair. Local inline navigation updates only its own post.
- Keep filters, pagers, selection, edit, retry, and delete controls with the data object they affect.
- Use path-aware `Back`. Every non-root flow has a predictable exit; deep flows also expose a direct safe home or top-level entry when appropriate.
- Service-layer authorization is mandatory. Hidden buttons, DM scope, and possession of a callback do not grant permission.
- Destructive and irreversible actions require a dedicated confirmation state that names the object and consequence.
- Invalid input stays on the same step and explains the accepted format. Missing or stale data produces a recoverable screen or ephemeral explanation, never a silent no-op.
- Long work acknowledges immediately with queued/running state, then publishes monotonic progress and a terminal result. Never hold an interaction open for the full job.
- Native form cancel restores the source post state and leaves its remaining controls usable.
- Do not assume Web/Desktop behavior proves native mobile support. Do not claim exact button rows on legacy attachments.

## Interaction budgets

- Frequent primary task: 1–2 actions from its entry surface.
- Secondary task: no more than 3 actions without a domain justification.
- Back: 1 action.
- Normal depth: no more than 2 transitions below entry.
- One screen: one clear purpose, usually under about 1200 visible characters and no more than two dense tables.

These are design budgets, not transport limits. Record justified exceptions.

## Reference routing

- Entry points, message lifecycle, navigation, DM/channel/thread choice: [references/interaction-model.md](references/interaction-model.md)
- Tables, lists, buttons, forms, confirmations, empty states, progress: [references/component-selection.md](references/component-selection.md)
- Labels, feedback, accessibility, i18n, errors: [references/content-feedback.md](references/content-feedback.md)
- Router/dialog/service/plugin/provider boundaries and state: [references/architecture.md](references/architecture.md)
- Review checklist, tests, live-client proof, handoff format: [references/verification.md](references/verification.md)
- External sources and Mattermost adaptations: [references/sources.md](references/sources.md)

## Completion report

For a material implementation or review, report:

- primary tasks and click counts;
- entry surfaces and maximum depth;
- chosen components and why;
- send-new versus edit-existing behavior;
- authorization and secret boundaries;
- server/client capability assumptions;
- tests and live checks actually run;
- remaining unverified clients or failure modes.

