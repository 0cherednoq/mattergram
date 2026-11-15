# Content, feedback, and accessibility

## Write for decisions

- Put the task or current object in the title.
- Follow with one short sentence that explains the state or required decision.
- Prefer concrete labels such as `Проверить 12 аккаунтов` over `Продолжить`.
- Keep user-facing errors specific and actionable without exposing internal details.
- Avoid walls of text. Move long history or help into a dedicated view.
- Do not repeat the same status in title, body, and every button.

## Feedback hierarchy

| Feedback | Use |
| --- | --- |
| Same-post rebuild | Navigation, filters, toggle state, successful local edit |
| Short ephemeral response | Permission denied, stale action, quick validation, retry hint |
| Native field errors | Correctable form validation |
| New durable post | Notification, receipt, export, terminal result worth retaining |
| Dedicated confirmation state | Destructive or irreversible decision |
| Progress card | Long-running work |

Every accepted interaction needs observable feedback. That feedback can be a changed post, opened form, ephemeral response, or newly created result. A silent no-op is not acceptable.

Do not copy Telegram's blanket `answerCallbackQuery()` rule into application handlers. Mattergram runtime owns callback ACK semantics. Application code should produce the intended state transition or explicit error without double-answering.

## Labels

- Use a verb plus object for actions: `Добавить пользователя`, `Повторить импорт`.
- Use a noun for destinations: `Аккаунты`, `История проверок`.
- Use the same term in keyboard text, screen title, and help.
- Keep labels short enough to scan, but never replace meaning with an emoji.
- Emoji can reinforce meaning; it cannot be the only label.
- Do not use `OK`, `Да`, `Нет`, or `Назад` where the consequence is ambiguous. In destructive confirmation, name the destructive action.

## State visibility

The user should be able to answer:

- Where am I?
- What object am I changing?
- What is selected or enabled?
- What will this button do?
- Did the action complete?
- How do I go back or recover?

Use text plus styling for important state. Color alone is insufficient. A selected filter may use primary color, while the screen summary also names the active filter.

## Internationalization

- Keep user-facing text in a translation or content layer when the application supports multiple locales.
- Treat exact composer-keyboard messages as input contracts. Configure localized text and handlers together in the composition root.
- Never derive authorization or domain identifiers from translated labels.
- Format times with an explicit timezone and name that timezone when users can reasonably differ.
- Keep variable interpolation typed and escaped for Mattermost Markdown.

## Errors

Split errors into user categories:

- invalid input: explain accepted format and keep current state;
- permission denied: explain access boundary without object disclosure;
- stale action: explain expiration and offer re-entry;
- missing object: clear stale selection and return to list;
- conflict: show current state and offer refresh/retry;
- external failure: preserve job/request identity, show safe retry status;
- internal failure: log correlation data, show a neutral message without exception text or secrets.

Do not catch every exception and return `Something went wrong`. Expected domain failures need typed errors. Unexpected errors should remain visible to observability while the user receives a safe response.

## Secret-safe presentation

Never render or log passwords, tokens, cookie values, signing keys, raw imported credentials, or full secret-bearing exception messages.

Show only safe metadata such as:

- configured/not configured;
- last rotation time;
- cookie count and unique domains;
- masked identifier only when the domain permits it;
- aggregate import/check counters.

## Accessibility and stable layout

- Keep controls in a predictable order across refreshes.
- Disable unavailable pager positions instead of removing them.
- Use text labels, not color or emoji alone.
- Keep tables narrow and bound cell lengths.
- Avoid instructions that depend on visual position such as `click the green button on the right`.
- Ensure cancel and Back work with keyboard-only and screen-reader navigation in the target client when accessibility is in scope.
- Verify contrast and focus behavior in the actual Web/Desktop client when custom Blocks or webapp plugins are used.

