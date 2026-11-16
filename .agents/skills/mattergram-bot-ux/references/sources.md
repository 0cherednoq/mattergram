# Sources and adaptation notes

This skill synthesizes interaction patterns. It does not copy source text or treat Telegram behavior as proof of Mattermost behavior.

## Priority of evidence

Use sources in this order:

1. the current mattergram and mattergram_dialog code, tests, and repository docs;
2. the target Mattermost server/client protocol and a live client check;
3. official aiogram or Telegram documentation when explaining an intentional analogy;
4. third-party skills and UX guides as design inspiration only.

If a pattern conflicts with the current checkout or Mattermost semantics, adapt or reject it.

## Reviewed material

### mattergram repository

Relevant local sources include `docs/dialog-ui-design.md`, `docs/dialog-navigation.md`, `docs/dialog-porting.md`, `docs/composer-keyboards.md`, `docs/game-admin-routing-refactor.md`, `src/mattergram/contrib/game_accounts_admin/jobs.py`, current exports in `src/mattergram_dialog`, and their behavioral tests.

Adopted directly: one-post local navigation, native forms, reusable pagination/filter/progress widgets, capability-aware rendering, ordinary-message keyboard boundary, service-level authorization, and the distinction between implemented and planned APIs.

### aiobot skill

- Repository: https://github.com/orihomie/aiobot-skill
- Skill: https://github.com/orihomie/aiobot-skill/blob/main/SKILL.md
- Widget reference: https://github.com/orihomie/aiobot-skill/blob/main/references/widgets.md
- Implementation reference: https://github.com/orihomie/aiobot-skill/blob/main/references/implementation.md

Useful ideas: task and screen maps, optional per-screen design artifacts, explicit navigation-stack semantics, reply-versus-inline separation, Back behavior, pagination and empty states, visible toggle state, validation recovery, progress feedback, UI-state separation, i18n, and a completion checklist.

Adaptations: mattergram uses grouped native forms instead of a one-message-per-field default, its built-in list/pager widgets instead of hand-rolled pagination, and Mattermost services/providers without requiring a separate HTTP backend.

Rejected as defaults: deleting user messages, Telegram callback acknowledgement rules, Telegram callback-data byte limits, polling/privacy-mode advice, and mandatory design files for trivial changes. The repository did not expose a clear license in the reviewed root, so only general ideas were synthesized.

### GramIO UX patterns

- Russian guide supplied for review: https://gramio.dev/ru/guides/ux-patterns
- Guide: https://gramio.dev/guides/ux-patterns
- Related dialogs package: https://github.com/gramiojs/dialogs

Useful ideas: button-first entry, concise home screens, breadcrumbs, stable Back/Home actions, explicit toggle state, confirmation for destructive actions, empty states, immediate acknowledgement for long work, concise labels, and keeping commands discoverable.

Adaptation: edit-in-place is limited to local navigation inside an owned post. In mattergram, a global reply-text command should remain visible and receive a fresh bot post so success and failure are observable.

### aiogram and aiogram-dialog ecosystem

- aiogram Router and Dispatcher: https://docs.aiogram.dev/en/latest/dispatcher/
- aiogram dependency injection: https://docs.aiogram.dev/en/latest/dispatcher/dependency_injection.html
- aiogram finite-state machine: https://docs.aiogram.dev/en/latest/dispatcher/finite_state_machine/index.html
- aiogram-dialog example tree: https://github.com/Tishka17/aiogram_dialog/tree/develop/example
- aiogram-dialog menus skill: https://github.com/brianmok888/aiogram-dialog-menus-skill/blob/main/SKILL.md

Useful ideas: composable routers, dependency injection instead of global service state, explicit dialog state, reusable windows/widgets, and separating render data from action handlers.

Adaptation: retain recognizable aiogram structure only where Mattermost has a real equivalent. Do not introduce Telegram-only IDs, lifecycle rules, or silent no-ops.

### Telegram bot UI skill

- Skill: https://github.com/pewpewgogo/telegram-bot-skill/blob/main/skills/telegram-bot-ui/SKILL.md
- Telegram Bot API: https://core.telegram.org/bots/api

Useful idea: reply buttons produce ordinary user text while inline buttons invoke local actions; local pagination commonly edits its message.

Adaptation: Mattermost composer-keyboard support is an optional UI adapter. Feature routers must accept the text without importing or detecting that adapter.

## Transfer matrix

| Pattern | Decision for mattergram |
|---|---|
| Button-first home | Adopt, with slash/text fallback |
| Reply keyboard sends plain text | Adopt as the decoupling boundary |
| Edit every response in place | Reject; edit local navigation only |
| Fresh post for reply command | Adopt for observable command handling |
| Delete user command messages | Reject by default |
| Back, Home, Cancel | Adopt with consistent semantics |
| Native grouped form | Prefer over message-by-message input |
| Manual pagination | Reject when a built-in pager fits |
| Callback ACK and byte budget | Telegram-specific; do not transfer |
| Immediate long-task acknowledgement | Adopt |
| Invent a percentage without a total | Reject; show stage/indeterminate progress |
| Separate UI and durable state | Adopt |
| Authorization in UI handlers only | Reject; enforce again in services |

