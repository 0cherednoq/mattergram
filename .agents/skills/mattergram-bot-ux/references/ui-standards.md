# Mattermost UI/UX standards

Read this reference for every Mattermost UI design, implementation, or review. It is the mandatory
baseline; the other references explain how to implement particular interactions. When a user gives
more specific product requirements, preserve them unless they violate authorization, secret safety,
the verified Mattermost capability profile, or another explicit repository contract.

## Decision order

When several designs are valid, decide in this order:

1. correctness;
2. clarity;
3. minimum number of user actions;
4. preservation of user context;
5. reuse of standard Mattermost components;
6. compactness;
7. visual polish.

Do not add architecture for abstract flexibility when a simpler, reliable implementation solves the
actual task. Design around the user's workflow, with few actions, preserved context, and maximum use
of verified native Mattermost capabilities.

## Preflight questions

Before choosing components, answer these questions from the current code, data, and target client:

1. What user workflow is being implemented?
2. What is the primary entity and what context does the user need?
3. Which data must be visible immediately?
4. Which details can use progressive disclosure?
5. Are filtering, sorting, selection, or pagination required?
6. Which related components belong in one coherent view?
7. Which actions are available to this actor, and where are they authorized?
8. Which standard Mattermost components fit the task?
9. Does the result stay within verified Blocks, action, depth, and text limits?
10. Does the structure add avoidable actions or navigation depth?
11. Will filtering, paging, selection, and navigation context survive interaction and refresh?

For a narrow change, keep the answers in the implementation notes or tests. For a material flow, put
them in the design artifacts described in `workflow.md`.

## Core presentation rules

- Prefer standard Mattermost Blocks, Interactive Dialogs, and implemented `mattergram_dialog`
  components. Do not build a custom substitute when a verified native component solves the task.
- Keep the interface compact, predictable, and visually consistent across the application.
- Model the user workflow rather than mirroring database tables or service boundaries.
- Combine related content when that reduces navigation and preserves context. Do not combine content
  merely because records have a database relation.
- Avoid decorative containers, repeated accents, excess color, and repeated information.
- Show essential data immediately. Put long, secondary, technical, or historical details behind
  progressive disclosure only when the extra click is justified.
- Color never carries meaning alone. Pair important color or accent state with text.

## Language and naming

- User-facing UI is Russian unless the product explicitly supports another locale for that actor.
- Translate enum values, codes, and internal statuses in the presentation layer only. Keep canonical
  values unchanged in domain logic, persistence, filters, and action payloads.
- Prefer one-word table headers. Avoid slashes, dashes, implementation abbreviations, and compound
  labels unless they remove real ambiguity. Use `Логин`, not `Аккаунт / логин`.
- Do not expose variable names, database field names, raw enum members, or technical state names.
- Preserve the exact content, case, and format of identifiers and values for which precision matters.
- Use the same term for the same entity or action in keyboards, titles, forms, tables, and help.

## Collections and tables

Place collection controls and content in this order:

1. filters on the left and sorting on the right, above the table;
2. the collection;
3. total count, pager, and optional page-local selection directly below it;
4. a selected entity detail in the same logical view when it remains reasonably compact.

Rules:

- Use plain Russian headings `Фильтрация` and `Сортировка`, without emoji.
- For 2–4 exclusive filters, use horizontal one-click buttons. The active option is `primary`; the
  others are `default`. For many values, use `static_select`/`Select`.
- Default sorting is newest first unless the domain needs another order. Make every sort stable by
  adding a unique deterministic tie-breaker such as the entity ID.
- Default page size is 5 records. Change it only for a documented density, client, or domain reason.
- Execute filtering, sorting, counting, and pagination on the backend. Do not load the full collection
  into a post or user presentation state.
- A filter change resets only that list to page 1. A sort change preserves its current filter. Paging
  preserves filter and sort.
- Show only comparison fields that help the current task; move secondary data to selected detail.
- Bound long cells and expose the exact value in detail when truncation is necessary.
- Show a useful empty state (`Записи не найдены`) and explain whether a filter caused it.

Use the stable pager shape `«  ‹  1 / N  ›  »`: first, previous, next, last. Keep all positions and
disable unavailable directions instead of removing them. Show the number of matching records. Add a
direct page or page-item selector only when it meaningfully reduces work.

## Entity detail pattern

Use the **EntityDetail** pattern for one selected entity:

- the heading identifies the entity unambiguously;
- primary fields use a compact layout, normally two columns on capable clients;
- information already established by the parent context is not repeated;
- important facts stay visible, while long or secondary details use `collapsible`;
- missing values use one consistent presentation;
- local actions remain physically inside the entity card;
- selection from a collection reveals detail in the same coherent view when practical rather than
  creating a separate post solely for extra properties;
- lifecycle state such as check progress, retry, and cancellation is nested in the entity card and
  does not repeat the parent ID when containment already identifies the entity.

`EntityDetail` is a semantic pattern, not permission to invent an API. Verify the checked-out public
exports before importing a component with that name. In the current baseline there is no public
`EntityDetail` class; compose the pattern from implemented `DataBlock`, `PostContainer`, native
`container`/`column_set`, and `collapsible` primitives.

## One workflow, one coherent view

A coherent view may contain a primary entity, summary, related collection, selected record, local
actions, and related operation state:

```text
Workspace
├── Primary entity
├── Aggregate summary
├── Related collection
│   ├── Filtering
│   ├── Sorting
│   └── Pagination
├── Selected entity
└── Contextual actions and operation state
```

- Use one outer `PostContainer`/native root `container` to group one post's related surfaces.
- Keep routine inner groups borderless. Preserve their own structure and independent state.
- Use nested `collapsible` sections for independently expandable details, not a new accented card for
  every subsection.
- Keep an independent process separate only when it has its own meaningful lifecycle, ownership, or
  history. Otherwise nest its state with the entity it affects.
- Split an overgrown view into separate states only after it exceeds a reasonable reading or action
  budget; preserve the navigation and selected context across that split.

## Copyable values

- Use a Markdown fenced code block for a frequently copied non-secret value when the extra height is
  justified. Give independently copied values separate fences. Use inline code for short values.
- Prefer the client's native copy behavior after verifying it on every required client. Do not add a
  custom copy action when native copying meets the requirement.
- Preserve exact content, case, and formatting. Choose a fence longer than any backtick run in the
  rendered value so user data cannot close the block.
- These rules never relax the secret boundary: do not render passwords, tokens, active cookie values,
  or other prohibited secrets merely to make them copyable.

## Actions

Use one visual and textual convention for the same action throughout the application:

| Action | Compact symbol |
| --- | --- |
| Settings | `⚙️` |
| Edit | `✏️` |
| Delete | `🗑️` |
| Refresh | `↻` |
| Check | `✓` |
| Back | `←` |
| Details | `ⓘ` |

- Primary action: `primary`. Secondary actions: `default`. Dangerous actions: `danger`.
- Do not mark every available action as primary.
- Disable unavailable actions and prevent duplicate submission.
- Keep navigation, filters, paging, and object mutations in separate purposeful groups.
- An icon alone is allowed only for the conventional dense-card actions permitted by the main skill,
  with a tooltip/accessibility name and full-text legacy fallback. Otherwise use text.
- An irreversible action requires a dedicated confirmation that names the object and consequence,
  followed by a fresh service authorization check.
- Do not add a button without a concrete user need and observable result.

## Status, progress, and results

- Map internal states to one consistent set of Russian display labels.
- Distinguish an operation's current state from its terminal result.
- Important results may use an accented container, but include explicit text and do not rely on color.
- Show actionable, secret-safe errors in plain language.
- Use a factual completed/total scale only when total work is known. Never invent a percentage.
- Keep critical failures visible; details and safe technical context may live in a nested section.

## Dates and time

- Date: `ДД.ММ.ГГГГ`.
- Date and time: `ДД.ММ.ГГГГ · ЧЧ:ММ`.
- Use the actor's timezone or an explicitly configured application timezone. Name the timezone when
  users may reasonably differ.
- Never mix UTC and local values without a label, and do not expose raw timestamps unless the task
  specifically requires them.
- Show last-updated time when freshness affects the user's decision.
- Preserve original timestamp precision in data even when the presentation is shorter.

## Collapsible sections

Use `collapsible` for long text, structured payloads, technical details, logs, additional parameters,
or history. Keep essential facts outside it. Its heading must name the content. Avoid deep nesting,
and do not hide critical errors exclusively inside a closed section. Do not add a collapsible around a
small amount of content when it only adds a click.

## UI state and message lifecycle

- Preserve filter, sort, page, selection, and navigation context after actions and refreshes.
- Derive the rendered view from current backend data; presentation state is not domain truth.
- Keep only identifiers and view choices in user state, not full collections.
- Preserve a selected entity after refresh when it remains accessible; otherwise explain the change
  and clear the stale selection safely.
- Local navigation edits the owned post. Top-level visible commands, durable receipts, and explicit
  separate results may create a new post as defined in `interaction-model.md`.
- One actor's action must not unexpectedly change another actor's personal view. Bind state to actor,
  conversation, post, intent, and current authorization as required by the runtime.
- Define loading, empty, error, success, stale, denied, and cancellation states where applicable.

## Component purpose

| Component | Intended use |
| --- | --- |
| `container` / `PostContainer` | Group one coherent surface |
| `column_set` | Compact responsive multi-column layout |
| `text` | User-facing content |
| `divider` | Separate semantic regions sparingly |
| `collapsible` | Progressive disclosure |
| `button` | Execute an action |
| `static_select` / `Select` | Select one value from many |
| Interactive Dialog / `FormWindow` | Enter or edit related data |
| Playbook | A human-owned regulated process, not ordinary job progress |

Do not use a component outside its purpose merely for decoration. Verify the target `UIProfile`
before relying on Blocks-only geometry or a newer input type.

## Final implementation gate

Before declaring a UI task complete, verify and report applicable items:

- the primary task and click/depth budget;
- Russian labels and display mappings without leaked internal names;
- filter-left/sort-right order, backend query behavior, stable sort, default page size, and boundary
  pager behavior;
- one coherent container and no duplicated parent information;
- object-local actions and destructive confirmation;
- date/time formatting and timezone;
- copy behavior and secret boundary;
- loading, empty, error, success, stale, denied, and cancellation behavior;
- per-user state preservation and same-post versus new-post semantics;
- verified Mattermost capability limits and actual target-client checks.

If a requirement is intentionally not applicable or cannot be verified, say so instead of silently
omitting it.
