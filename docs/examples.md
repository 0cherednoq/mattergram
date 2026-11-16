# Examples index

This generated index distinguishes runnable, declaration-only, offline, contract, Redis, and live verification levels.

| Example | Scenario | Minimum profile | Status | Guide |
| --- | --- | --- | --- | --- |
| [examples/game_accounts_admin.py](../examples/game_accounts_admin.py) | Run the game-account administration bot with app users, account workflows and a persistent composer keyboard | Mattermost 12.0.2 with Blocks, forms.file and the mattergram-menu plugin | `contract-verified` | [guide](game-accounts-admin.md) |

## `examples/game_accounts_admin.py`

Run the game-account admin dialog on an owned local Mattermost stand.

- Kind: `runnable`
- Dependencies: `core`, `sqlalchemy`
- Environment: none
- Permissions: registered /game-admin command; trusted callback and form ingress; bot can create and edit posts; bot can read submitted import files; bot can create direct channels for configured pushes
- Assertions: the keyboard exposes only Overview, Accounts and authorized Users entries; secondary operations remain contextual actions inside the editable post; account cards never render password or active cookie values; filters, sorting and pagination are backend-driven and user-scoped; role checks are enforced by declarative routing metadata
- Evidence: [docs/reports/game-accounts-admin.json](reports/game-accounts-admin.json)
