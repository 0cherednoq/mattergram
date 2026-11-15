# Examples index

This generated index distinguishes runnable, declaration-only, offline, contract, Redis, and live verification levels.

| Example | Scenario | Minimum profile | Status | Guide |
| --- | --- | --- | --- | --- |
| [examples/bootstrap.py](../examples/bootstrap.py) | Provision resources and load runtime credentials | Mattermost 12.0.2 provisioning profile | `live-verified` | [guide](bootstrap.md) |
| [examples/command_manifest.py](../examples/command_manifest.py) | Build slash command registration intent offline | offline | `offline-verified` | [guide](provisioning-commands.md) |
| [examples/create_post.py](../examples/create_post.py) | Create one native channel post | Mattermost 11.1 REST | `live-verified` | [guide](native-client.md) |
| [examples/dialog_wizard.py](../examples/dialog_wizard.py) | Declare post and form windows | mattergram_dialog runtime | `declaration-only` | [guide](dialog-porting.md) |
| [examples/dialog_navigation.py](../examples/dialog_navigation.py) | Navigate one editable post with path-aware Back, declarative action rows, and object-local filters and pagers | Mattermost legacy post actions plus mattergram_dialog runtime; exact rows require proven Mattermost Blocks | `contract-verified` | [guide](dialog-navigation.md) |
| [examples/dialog_progress.py](../examples/dialog_progress.py) | Declare and refresh one durable progress post with a safe Cancel action | Mattermost 11.1 legacy post actions plus mattergram_dialog runtime | `contract-verified` | [guide](dialog-ui-design.md) |
| [examples/dialog_navigation_bot.py](../examples/dialog_navigation_bot.py) | Run a one-post application with native Blocks order rows, local edit/delete forms, cursor pagination, and live refresh | Mattermost 11.11+ with MmBlocksEnabled, HTTP/WS/forms, and Redis | `contract-verified` | [guide](dialog-ui-design.md) |
| [examples/form_bot.py](../examples/form_bot.py) | Open and validate a managed native form | Mattermost 11.1 forms.basic | `contract-verified` | [guide](native-forms.md) |
| [examples/form_declaration.py](../examples/form_declaration.py) | Create a reusable typed form declaration | Mattermost 11.7 form profile | `contract-verified` | [guide](native-forms.md) |
| [examples/http_bot.py](../examples/http_bot.py) | Run combined slash HTTP and WebSocket ingress | Mattermost 11.1 HTTP/WS | `contract-verified` | [guide](native-http.md) |
| [examples/native_api.py](../examples/native_api.py) | Read identity and channel posts through reviewed operations | Mattermost 11.1 REST | `live-verified` | [guide](native-api.md) |
| [examples/redis_bot.py](../examples/redis_bot.py) | Persist FSM state in Redis | Mattermost WebSocket plus Redis | `real-redis-verified` | [guide](native-redis.md) |
| [examples/routing_bot.py](../examples/routing_bot.py) | Route a WebSocket message and reply | Mattermost 11.1 WebSocket | `live-verified` | [guide](native-routing.md) |
| [examples/showcase_bot.py](../examples/showcase_bot.py) | Run a multi-page card with tables, forms, and live updates | Mattermost 11.1 HTTP/WS/forms | `live-verified` | [guide](showcase.md) |
| [examples/ui_input_formats.py](../examples/ui_input_formats.py) | Convert supported Slack and Adaptive declarations offline | offline conversion | `offline-verified` | [guide](ui-input-formats.md) |
| [examples/ui_menu_bot.py](../examples/ui_menu_bot.py) | Send a signed button and handle its callback | Mattermost 11.1 legacy actions | `live-verified` | [guide](native-ui.md) |
| [examples/upload_document.py](../examples/upload_document.py) | Upload and attach one local file | Mattermost 11.1 files API | `live-verified` | [guide](high-level-api.md) |
| [examples/game_accounts_admin.py](../examples/game_accounts_admin.py) | Administer bot users, push routes, account imports and checks in one editable post | Mattermost 11.1 legacy post actions plus forms.file; exact rows require proven Blocks | `contract-verified` | [guide](game-accounts-admin.md) |
| [examples/game_accounts_admin_bot.py](../examples/game_accounts_admin_bot.py) | Run the administrator panel on an owned local Mattermost 12.0.2 stand | Owned Mattermost 12.0.2 with Blocks, forms.file and host.docker.internal callback access | `contract-verified` | [guide](game-accounts-admin.md) |

## `examples/bootstrap.py`

Provision once with a setup token, then run from persisted runtime secrets.

- Kind: `runnable`
- Dependencies: `provisioning`
- Environment: `MM_APPLICATION`, `MM_CALLBACK_URL`, `MM_HTTP_TRUSTED_PEERS`, `MM_OPERATOR_USERNAME`, `MM_PROVISIONING_KEY`, `MM_SETUP_TOKEN`, `MM_STATE_DIR`, `MM_TEAM`, `MM_URL`
- Permissions: documented setup permissions; ordinary runtime bot permissions
- Assertions: repeat reconciliation does not duplicate owned resources; runtime receives no setup credential
- Evidence: [docs/reports/bootstrap.json](reports/bootstrap.json)

## `examples/command_manifest.py`

Run offline to inspect registration intent; this does not provision a server.

- Kind: `runnable`
- Dependencies: `core`
- Environment: none
- Permissions: none
- Assertions: manifest contains literal triggers and immutable metadata
- Evidence: [docs/reports/provisioning-commands.json](reports/provisioning-commands.json)

## `examples/create_post.py`

Create one real post with MM_URL and MM_BOT_TOKEN in the environment.

- Kind: `runnable`
- Dependencies: `core`
- Environment: `MM_BOT_TOKEN`, `MM_URL`
- Permissions: bot can post in the target channel
- Assertions: prints the confirmed native post ID
- Evidence: [docs/reports/native-client.json](reports/native-client.json)

## `examples/dialog_wizard.py`

Native mattergram_dialog declaration; wire it to slash/callback handlers as documented.

- Kind: `declaration`
- Dependencies: `core`
- Environment: none
- Permissions: none
- Assertions: declaration imports and type-checks
- Evidence: [docs/reports/dialog-runtime.json](reports/dialog-runtime.json)

## `examples/dialog_navigation.py`

Declare one editable post with branch-aware Back, filters, and independent pagers.

- Kind: `declaration`
- Dependencies: `core`
- Environment: none
- Permissions: trusted callback ingress; bot can create and edit the post
- Assertions: Back restores the actual source branch; ButtonGrid preserves declared row shapes and compiles them to Blocks columns when supported; each table and its local actions compile into one object block; multiple tables retain independent pagination and filters; post actions update one post and reject stale repeats
- Evidence: [docs/reports/dialog-navigation.json](reports/dialog-navigation.json)

## `examples/dialog_progress.py`

Declare a durable one-post job progress card with a safe Cancel action.

- Kind: `declaration`
- Dependencies: `core`
- Environment: none
- Permissions: trusted callback ingress; bot can create and edit the post
- Assertions: determinate and indeterminate progress render with numeric text; read-only DataBlock remains visible without actions; used Button callbacks are rejected as outdated; DialogRuntime.update rejects stale revisions while editing the same post; CoalescingDialogUpdater retains the newest pending worker snapshot
- Evidence: [docs/reports/dialog-declarative-progress.json](reports/dialog-declarative-progress.json)

## `examples/dialog_navigation_bot.py`

Run the persistent local mattergram_dialog navigation demo.

- Kind: `runnable`
- Dependencies: `core`, `redis`
- Environment: `LOG_LEVEL`, `MM_ACTION_SIGNING_KEY_HEX`, `MM_ACTION_TTL_SECONDS`, `MM_BLOCKS_ENABLED`, `MM_BOT_TOKEN`, `MM_CALLBACK_BASE_URL`, `MM_DIALOG_PREFIX`, `MM_HTTP_HOST`, `MM_HTTP_PORT`, `MM_HTTP_TRUSTED_PEERS`, `MM_LOCAL_STAND_STATE`, `MM_REDIS_URL`, `MM_SLASH_TOKEN`, `MM_TEAM_ID`, `MM_UI_PROFILE_VERSION`, `MM_URL`
- Permissions: registered slash command; trusted HTTP ingress; bot can post and edit
- Assertions: six order rows compile as text columns plus local Edit/Delete buttons; Edit opens the selected order form and Delete requires explicit confirmation; the complete orders post stays below 50 actions and 100 blocks; summary tables paginate and filter independently; WebSocket input edits the same post without dropping actions
- Evidence: [docs/reports/dialog-ui-design.json](reports/dialog-ui-design.json)

## `examples/form_bot.py`

Open and validate an already provisioned /request command's native form.

- Kind: `runnable`
- Dependencies: `core`
- Environment: `MATTERGRAM_DIALOG_REDIS_URL`, `MM_BOT_TOKEN`, `MM_CALLBACK_BASE_URL`, `MM_HTTP_TRUSTED_PEERS`, `MM_SERVER_VERSION`, `MM_SLASH_TOKEN`, `MM_TEAM_ID`, `MM_URL`
- Permissions: registered slash command; trusted HTTP ingress
- Assertions: invalid title returns a field error; valid submission closes once
- Evidence: [docs/reports/forms-core.json](reports/forms-core.json)

## `examples/form_declaration.py`

Typed reusable declaration; open it through the managed native form runtime.

- Kind: `declaration`
- Dependencies: `core`
- Environment: none
- Permissions: none
- Assertions: declaration serializes under its explicit UIProfile
- Evidence: [docs/reports/forms-core.json](reports/forms-core.json)

## `examples/http_bot.py`

Own an HTTP/WS runtime for an already provisioned native slash command.

- Kind: `runnable`
- Dependencies: `core`
- Environment: `MM_BOT_TOKEN`, `MM_HTTP_TRUSTED_PEERS`, `MM_SLASH_TOKEN`, `MM_TEAM_ID`, `MM_URL`
- Permissions: registered slash command; trusted HTTP ingress
- Assertions: slash response completes once; WS and HTTP share one dispatcher lifecycle
- Evidence: [docs/reports/http-core.json](reports/http-core.json)

## `examples/native_api.py`

Read identity and recent channel posts through reviewed low-level operations.

- Kind: `runnable`
- Dependencies: `core`
- Environment: `MM_BOT_TOKEN`, `MM_CHANNEL_ID`, `MM_URL`
- Permissions: bot can read the target channel
- Assertions: uses only generated operation IDs
- Evidence: [docs/reports/native-api.json](reports/native-api.json)

## `examples/redis_bot.py`

Run with MM_URL, MM_BOT_TOKEN and MM_REDIS_URL; send 'start', then a title.

- Kind: `runnable`
- Dependencies: `redis`
- Environment: `MM_BOT_TOKEN`, `MM_REDIS_URL`, `MM_URL`
- Permissions: bot can read and post in the channel
- Assertions: state and event isolation use the same owned Redis lifecycle
- Evidence: [docs/reports/redis-core.json](reports/redis-core.json)

## `examples/routing_bot.py`

Run with MM_URL and MM_BOT_TOKEN; send 'ping' in a channel joined by the bot.

- Kind: `runnable`
- Dependencies: `core`
- Environment: `MM_BOT_TOKEN`, `MM_URL`
- Permissions: bot is a channel member
- Assertions: ping reaches one handler and creates pong
- Evidence: [docs/reports/routing-core.json](reports/routing-core.json)

## `examples/showcase_bot.py`

Run a multi-page Mattermost card with tables, forms, callbacks, and live updates.

- Kind: `runnable`
- Dependencies: `core`
- Environment: `LOG_LEVEL`, `MM_ACTION_SIGNING_KEY_HEX`, `MM_BOT_TOKEN`, `MM_CALLBACK_BASE_URL`, `MM_HTTP_HOST`, `MM_HTTP_PORT`, `MM_HTTP_TRUSTED_PEERS`, `MM_SERVER_VERSION`, `MM_SLASH_TOKEN`, `MM_TEAM_ID`, `MM_URL`
- Permissions: registered slash command; trusted HTTP ingress; bot can post and edit
- Assertions: two tables retain independent pagination and filters; WebSocket input causes a REST edit that preserves props
- Evidence: [docs/reports/showcase.json](reports/showcase.json)

## `examples/ui_input_formats.py`

Executable offline Slack/Adaptive -> signed native Blocks example.

- Kind: `runnable`
- Dependencies: `core`
- Environment: none
- Permissions: none
- Assertions: converted actions retain signed application data
- Evidence: [docs/reports/ui-input-core.json](reports/ui-input-core.json)

## `examples/ui_menu_bot.py`

Send a native signed menu when a user writes 'menu'; no setup/admin token.

- Kind: `runnable`
- Dependencies: `core`
- Environment: `MM_ACTION_SIGNING_KEY_HEX`, `MM_BOT_TOKEN`, `MM_CALLBACK_BASE_URL`, `MM_HTTP_TRUSTED_PEERS`, `MM_URL`
- Permissions: trusted HTTP ingress; bot can post in the channel
- Assertions: callback data is signed and actor-bound
- Evidence: [docs/reports/ui-schemas.json](reports/ui-schemas.json)

## `examples/upload_document.py`

Upload one local file and attach it to a native Mattermost post.

- Kind: `runnable`
- Dependencies: `core`
- Environment: `MM_BOT_TOKEN`, `MM_URL`
- Permissions: bot can upload and post in the target channel
- Assertions: prints the post ID after confirmed multipart upload
- Evidence: [docs/reports/high-level-api.json](reports/high-level-api.json)

## `examples/game_accounts_admin.py`

Compatibility imports for the extracted Game Admin feature.

- Kind: `declaration`
- Dependencies: `core`
- Environment: none
- Permissions: registered administrator-only slash command; trusted callback and form ingress; bot can create and edit the post; bot can read the submitted import file
- Assertions: only an explicit enabled administrator can enter; account cards never render password or cookie values; import and check histories contain aggregate results; single, all and import-scoped checks use the injected verifier; batch imports and checks use monotonic background job progress; push rules select exact destinations and events
- Evidence: [docs/reports/game-accounts-admin.json](reports/game-accounts-admin.json)

## `examples/game_accounts_admin_bot.py`

Run the game-account admin dialog on an owned local Mattermost stand.

- Kind: `runnable`
- Dependencies: `core`
- Environment: none
- Permissions: local stand administrator can register slash commands; bot can create and edit posts; bot can read submitted import files; bot can create direct channels for configured pushes
- Assertions: replaces only the owned /game-admin command; binds the exact fixture administrator; serves trusted slash, callback and form ingress; uses deterministic demo verification and explicitly ephemeral job storage only
- Evidence: [docs/reports/game-accounts-admin.json](reports/game-accounts-admin.json)
