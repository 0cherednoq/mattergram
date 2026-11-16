# Mattermost interaction model

## Entry surfaces

| Entry | Use | Expected result |
| --- | --- | --- |
| Native slash command | Discoverable channel entry and explicit parameters | Start or respond, then acknowledge HTTP exactly once |
| Exact text in bot DM | Persistent keyboard or manual top-level navigation | Create a fresh bot post and reset that actor/DM flow when configured as top-level |
| Inline post action | Local navigation or action on the rendered object | ACK and rebuild only the source post |
| Native form | Structured related input | Validate fields, persist through service, return to the owning post |
| WebSocket event | Data changed outside current interaction | Refresh through stored dialog context and rotate controls |
| Notification/event post | Durable event the user may need later | Send a new post, optionally with local actions |

Do not treat all entries as equivalent. Slash HTTP has a response deadline, composer text is a visible user post, and inline actions belong to one existing post.

## Composer keyboard boundary

The Mattergram Menu plugin is a UI adapter. It renders buttons and publishes configured ordinary text as the current user.

Required boundary:

```text
keyboard click ─┐
manual typing ──┼─> Message ─> Router filter ─> application service
other UI ───────┘
```

- Feature code accepts `Message`, not a keyboard button callback.
- Button IDs are UI schema and never authorize or route a feature.
- The composition root may share one text constant between the keyboard declaration and exact-text filter.
- Restrict broad labels such as `Пользователи` to the intended bot DM and actor policy.
- If the keyboard is missing, manual text and slash entry still work.

## Send a new post or edit an existing post

Use a new post for:

- a top-level visible text command whose delivery should be obvious;
- a notification, receipt, export, or terminal result worth keeping;
- an explicit user request to open a separate view;
- recovery when no owned editable post remains and the protocol permits a fresh response.

Edit the existing post for:

- filters, paging, row selection, tabs, and local detail;
- toggles and quick state changes whose updated label is the feedback;
- form return, validation summary, and path-aware Back;
- progress snapshots tied to the same job card.

Never edit an arbitrary historical post as the only response to a new visible text command. Never send a new post for every local pager click.

## Navigation

- Top-level lateral entries reset the active stack for that actor and conversation.
- Detail and edit flows preserve a path back to their actual source.
- Tabs and filters switch local view without adding artificial history.
- Linear form steps advance only after successful validation.
- A deep flow exposes Home or a top-level entry when repeated Back clicks would exceed the interaction budget.
- After a destructive success, show the terminal state or return to a safe list. Do not leave the deleted object editable.

## DM, channel, and thread choice

Use bot DM for private administration, personal settings, credentials metadata, and per-user flows. A DM is still a Mattermost direct channel, so state must include actor and channel identity.

Use a team channel when the result is shared and all channel members may see it. Do not expose administrative controls merely because the bot is present in the channel.

Use a thread when actions and results belong to one discussion. Keep root/thread identity stable across edits and background refresh.

Use ephemeral feedback for short permission, stale-action, validation, or retry guidance that does not need history. Do not use ephemeral output for durable receipts or state the user must revisit.

## Interaction budgets

- Frequent task: 1–2 actions from entry.
- Secondary task: at most 3 actions.
- Back: exactly 1 action.
- Normal depth: two transitions below entry.
- More than 3 actions for a frequent task requires redesign or written domain justification.

Count opening and submitting a form. Do not hide frequent work behind a generic `More` screen merely to make the entry card shorter.

## Stable post ownership

An inline action is valid only for its actor, channel, post, intent, state, and action token. When any identity is stale, explain expiration and offer a safe re-entry. Never silently replay an old write.

Form cancellation is a transition. Closing the modal must restore the source window and keep the source post controls usable.

