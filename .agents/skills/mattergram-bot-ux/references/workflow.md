# Workflow and design artifacts

Use this workflow for a new bot, a feature spanning three or more states, or a UX rework. For a narrow one-screen change, keep the same reasoning but do not create unnecessary files.

## Modes

### New

1. Identify actors, permissions, and the top tasks.
2. Inventory required entry points and Mattermost surfaces.
3. Create the screen map and task flows before implementation.
4. Choose components and error states.
5. Implement one vertical slice, including authorization and recovery.
6. Run contract checks and the applicable real-client smoke test.

Do not ask the user to design the screen hierarchy when their product goal is already clear. Make a reasonable first design and expose decisions that materially change scope.

### Review

1. Locate all entry handlers, dialog states, windows, forms, keyboard declarations, and services.
2. Reconstruct the current user journeys from code and live evidence.
3. Count actions and depth for primary tasks.
4. Find dead ends, detached actions, ambiguous labels, missing states, stale controls, and unauthorized paths.
5. Separate verified behavior from inference.
6. If the user requested review only, report findings without changing code.

### Improve

1. Read the affected design artifact when present.
2. State the behavior change and preserved invariants.
3. Update the artifact and code in the same task.
4. Test the changed flow, including at least one failure path.

## Optional project structure

For a material bot, keep UX intent next to the repository without mixing it into runtime code:

```text
.mattergram-design/
├── map.md
├── screens/
│   ├── home.md
│   ├── accounts.md
│   └── account-edit.md
├── components/
│   ├── account-table.md
│   └── full-pager.md
└── flows/
    ├── inspect-account.md
    └── import-accounts.md
```

- `map.md` lists actors, entry points, screens, and task budgets.
- `screens/` describes what one state shows and allows.
- `components/` describes reused visual/interaction patterns, not Python classes.
- `flows/` describes transitions, branches, success, cancellation, and recovery.

Use the templates in `assets/design/`. Copy only the files the project needs.

## Wireframe notation

Keep wireframes free of implementation details:

```text
surface: bot DM, editable post
entry: exact text "Аккаунты"

# Игровые аккаунты
< total {count} · invalid {invalid_count} >

[ filters ]
< account table >
[ pager ] [ select account ]
< selected account preview >

+++
[ Проверить все ]
[ Назад ]

empty -> explain why and show [ Импортировать ]
permission denied -> ephemeral explanation, no data render
```

Notation:

- `< ... >` is dynamic content.
- `[ ... ]` is an action or referenced component.
- `+++` separates content/local actions from global navigation.
- `condition -> result` documents a branch or failure state.

Do not put widget IDs, function names, repository calls, or secrets in a wireframe. After implementation, add a short `Implementation` section with paths and public symbol names when that improves navigation.

## Screen map questions

- What is the fastest entry for each actor?
- Which result deserves a durable new post, and which is local navigation?
- Where does Back go from each state?
- Can a deleted record, expired action, closed form, or lost permission strand the user?
- Does every long operation have queued, running, terminal, and retry states?
- Can the same task be completed if the optional composer keyboard is absent?

