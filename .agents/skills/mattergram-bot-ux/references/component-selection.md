# Component selection

Choose the component from the user's task and data shape. Do not recreate an existing mattergram_dialog component with manual callback code.

## Screen composition

Use this order:

1. one clear title and one-sentence context;
2. primary data, task, or form;
3. controls local to that data;
4. global navigation.

Use `Section` for screen context, `DataBlock` for one data object plus its actions, and `ButtonRow` or `ButtonGrid` for window-level navigation.

### Accent containers

In the Blocks profile, the blue strip on the left belongs to a bordered Mattermost `Container`.
`DataBlock`, `ButtonRow`, and `ButtonGrid` can request that presentation; it is emphasis, not a
default wrapper for every part of a screen.

Use a bordered accent container only when the block must read as a distinct card, for example:

- an important live status or warning;
- a destructive confirmation with the affected object and consequence;
- a standalone summary that must remain visually distinct from nearby data.

Use `border=False` for ordinary tables, filters, pagination, navigation, and routine CRUD actions.
Do not create a separate accented `ButtonRow` titled `Management` merely to hold one create
button. Put that primary action before the related table so it remains visible when the list grows,
while keeping edit/filter/pager actions with the table they affect.

```python
ButtonRow(add_user, border=False)
DataBlock("Пользователи", users_table, filters, pager_and_select, edit_user, border=False)
```

Color must not be the only carrier of meaning. Legacy attachments may not reproduce the exact
plain/bordered Blocks presentation, so preserve clear headings and labels.

### One container for one post

When a single post contains several related surfaces — title/context, list, selected record,
background-operation state, and navigation — wrap them in one declarative `PostContainer`:

```python
Window(
    context,
    account_list,
    account_card,
    account_check,
    navigation,
    state=Accounts.list,
    container=PostContainer(
        border=True,
        background="gray",
        accent_color="primary",
        gap="medium",
    ),
)
```

Use only one outer border/accent. Render routine inner groups with `border=False`; use
`collapsible` for selected details and progress that must expand independently. This is a Blocks
presentation rule. Preserve readable headings and ordinary message/attachments fallback on legacy
clients instead of simulating the outer container with several accented attachments.

## Buttons and rows

- One row has one purpose: destinations, filters, paging, secondary actions, or destructive actions.
- Prefer 2–4 ordinary controls per row. A five-control `PagePager` is a standard exception.
- Put the dominant action first or on its own row.
- For an ordinary data block, put destructive actions in the last separate row. For a dense selected
  object card, a proven Blocks profile may instead use a trailing action cluster opposite the primary
  action; delete remains the last trailing control.
- Prefer labels that name the result: `Удалить пользователя`, not `Да`. The conventional compact
  actions `✏️` (edit), `⚙️` (settings), and `🗑️` (delete) may be icon-only only with a native tooltip,
  a full text legacy fallback, and an explicit confirmation for delete.
- Preserve boundary pager positions with disabled controls so the layout does not jump.
- Exact row geometry requires a proven Blocks profile. Legacy attachments may wrap.

For intentional multi-row navigation:

```python
ButtonGrid(
    ActionRow(accounts, imports),
    ActionRow(users, notifications),
    ActionRow(help_button),
    title="Разделы",
)
```

Do not split one object's controls into separate attachments to fake rows.

## Lists, tables, and details

| Need | Use |
| --- | --- |
| Compare repeated records across 2–4 short fields | `PagedTable` with bounded `MarkdownColumn` |
| Heterogeneous items or 1–3 descriptive lines | `PagedList` |
| Reliable total and first/last navigation | `PagePager` |
| Reliable total and compact navigation | `CompactPager` or deliberate `InlinePager` |
| Opaque remote cursor and unknown total | `CursorList` + `CursorPager` |
| Select one visible row | `PageItemSelect` beside the pager |
| Per-row actions with proven Blocks support | `ActionTable` with bounded action count |

Put filters above the table and pager/selection below it in the same `DataBlock`. Put a plain
`Фильтрация` group on the left and `Сортировка` on the right when the profile can render the responsive
row; let mobile stack them without changing their semantic order. If the selected record only needs a
short inspection view, reveal it below the pager instead of opening another state.

Default to 5 records per page. Use newest-first sorting unless the domain requires another default,
and always add a stable unique tie-breaker. Filtering, sorting, counting, and pagination belong to the
backend; user presentation state stores query choices and identifiers, not the full collection.

Every list must define:

- loading or initial state when data is remote;
- empty state with explanation and useful next action;
- page boundary behavior;
- response to a deleted or changed selected record;
- unique stable IDs for list, filters, pager, and selection.

## Filters and toggles

For 2–4 exclusive values, use `Filter`. Show the current value with native selected/primary styling
and a stable label. Use `Select` for a larger set. For accessibility, the surrounding text or summary
must also state the current value when color alone would be ambiguous. A filter change resets that
list to page 1; a sort change preserves the filter.

For booleans, make the label expose state:

```text
[ Уведомления: включены ]
[ Уведомления: выключены ]
```

One click flips state and rebuilds the same post. Do not mix a toggle with unrelated navigation in the same row.

## Forms

| Data | Widget |
| --- | --- |
| Short text, email, number, URL | `TextInput` with subtype and maximum length |
| Long comment | `TextArea` |
| One value among many | `Select` |
| Several values | `MultiSelect` |
| 2–4 visible exclusive values | `Radio` |
| Several independent flags | `CheckboxGroup` |
| One boolean | `Checkbox` |
| Date | `DatePicker` |
| Date and time | `DateTimePicker` |
| File input | `FileInput` after capability proof |

Mattermost native forms can collect a related group in one modal. Prefer 5–7 related fields per step. Split eight or more fields into 2–4 task-based steps. Do not copy Telegram's one-message-per-field pattern when a native form is clearer.

Validation rules:

- validate format at the UI boundary and business rules in the service;
- keep the form open on correctable field errors;
- preserve non-secret valid values after correction;
- never prefill passwords, tokens, or cookie values;
- represent an existing secret as `configured`, with explicit replace/clear actions;
- on success return to the owning post with a concise confirmation.

## Destructive actions

Every irreversible or high-impact action uses a dedicated confirmation state:

```text
# Удалить пользователя @alice?
Доступ к приложению будет отключён. История аудита сохранится.

[ Отмена ] [ Удалить пользователя ]
```

- Put the safe action first.
- Name the object and consequence.
- Re-fetch and re-authorize before the write.
- Disable double submission and make the write idempotent where possible.
- Show terminal success or a recoverable conflict, not a generic error.

## Empty, missing, and permission states

Empty state structure:

1. say what is empty;
2. explain whether filters caused it;
3. offer one useful action such as create, import, clear filter, or refresh;
4. hide meaningless pagination and selection.

If data disappeared or changed, re-fetch, explain the conflict, clear stale selection, and give a path to the list. Do not render an empty detail card.

Permission denied should not leak whether a protected object exists. Show a short explanation and safe exit.

## Progress and background work

For work that can exceed an interaction deadline:

1. validate and authorize;
2. create a durable job and return queued state immediately;
3. show `Progress` with stage, completed/total when known, and cancel only when cancellation is real;
4. publish coalesced snapshots through `DialogRef` and monotonic revision;
5. finish with succeeded, failed, partial, or cancelled state;
6. store terminal history even if the original post became stale.

Do not invent a percentage when total work is unknown. Use stage text and an indeterminate state instead.

Use the shared `CoalescingDialogUpdater` for frequent presentation snapshots; it is not a queue or durable store. Game Admin's implemented job types and production boundary are documented in [background-jobs.md](background-jobs.md). Never default a production application to an ephemeral memory queue.

## Notifications and durable results

Navigation stays in the owning editable post. A notification, receipt, import report, or export result may be a new durable post. Include one clear next action and an optional dismiss/archive action only when the platform and audit requirements allow it.

