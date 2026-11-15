# План реализации mattergram с повторным использованием aiogram

**Дата:** 13 ноября 2026 года.  
**Статус:** E0–E4, E5-01, E5-02, E6, E7 и E8 выполнены. HTTP/WS/FSM, backend native forms, automatic provisioning, generated low-level API, high-level facade, полный operationId coverage и dialog runtime/scenario проверены через настоящие Mattermost и Redis; dialog extraction/audit воспроизводимы. E5-03 native clients, E9 documentation/evaluations/release и последующие задачи открыты.

Основной контракт находится в [дизайне](aiogram_mattermost_fork_spec.md). Документация планируется в [DOCUMENTATION_PLAN.md](DOCUMENTATION_PLAN.md).

## Прогресс реализации

**Состояние:** E0-01–E0-04, E1-01–E1-04, E2-01–E2-03, E3-01–E3-03, E4-01–E4-03, E5-01–E5-02, E6-01–E6-03, E7-01–E7-03, E8-01–E8-03, E9-01, CI-01 и CI-03 выполнены: 31 из 56. Core SDK, dialog runtime/scenario и documentation scope прошли строгие drift/behavior checks. Полный продукт и публикация открыты.

Чекбоксы задач находятся в разделе 10, инфраструктуры и публикации в разделе 13, будущих обновлений upstream в разделе 15. ID задач сохраняются между сессиями.

После каждой рабочей сессии:

1. Отмечать `[x]` только выполненные задачи с подтверждённым результатом. Частично выполненные оставлять `[ ]`.
2. В журнале ниже записывать ID задач, изменённые файлы, команды проверок и результаты. Для CI и публикации указывать ссылки на реальные запуски и артефакты.
3. Обновлять [STATUS.md](../STATUS.md): результат, блокеры и следующий конкретный шаг.
4. Не отмечать интеграционные проверки выполненными, если стенд не запускался. Для неприменимой задачи записывать основание закрытия в журнале.

**Текущая задача:** E9-02 LLM evaluations; E5-03 native client acceptance остаётся открытой.  
**Следующий шаг:** зафиксировать evaluation tasks, prompt/context/model metadata, assertions и воспроизводимый отчёт без выдуманной оценки.  
**Незакрытые решения:** см. раздел «Решения перед реализацией» в STATUS.md.

| Дата | Задачи | Результат и проверки | Следующее действие |
| --- | --- | --- | --- |
| 2026-11-12 | Добавлено отслеживание задач | Добавлены чекбоксы, постоянные ID и правила записи прогресса. Реализация и протокольные проверки не выполнялись | Начать E0-01 |
| 2026-11-12 | E0-01–E0-04, CI-01, CI-03; CI-02/04/07 частично | `upstream.lock.json`: 3711 файлов, 4 закреплённых источника. `tools/fetch_upstream.py`, `inventory_upstream.py`, `build_manifest.py`; `manifests/core.json`: 24 решения, 18 copy/adapt и 6 exclude. Сводка `docs/reports/upstream-inventory.json`, контракт `docs/minimal-slice-contract.md`, stubs `contracts/minimal_api.pyi`. 12 тестов проходят на Windows/Python 3.11, 3.13, 3.14; Ruff, strict mypy, actionlint 1.7.12. Wheel/sdist собраны изолированно, twine 7 прошёл; оба wheel установлены вне checkout. CI создан локально, внешних запусков нет. При первом форматировании затронуты snapshots: исключения исправлены, все snapshots восстановлены и повторно сверены с исходными hashes. | E1-01: детерминированный staging, затем namespace codemod/audit. E7: отдельно решить условия генерации из API-документации CC BY-NC-SA; не включать её в MIT runtime. |
| 2026-11-12 | E1-01–E1-04 | Реализованы `extract_core`, `rewrite_imports`, `_patches`, `audit_port`, `verify_extraction`. 18 модулей перенесены только в staging; три запуска дали одинаковые receipt/bytes, hashes maintained src не изменились. Strict audit вернул exit 1 и 101 findings, включая 13 pending adaptations: неготовый core не разрешён к переносу. 37 тестов прошли на Windows/Python 3.11, 3.13, 3.14; Ruff/strict mypy/actionlint прошли. Проверены реальные synthetic imports и изменение результата через pinned semantic patch, tampering/collisions/native boundaries и ошибки зависимостей/exports. JSON generation исправлена на одинаковые LF bytes между ОС. Отчёты: `build/reports/extraction.json`, `port-audit.json`, `tests.xml`; правила: `docs/porting-tools.md`. CI дополнен проверкой инвариантов extraction; внешних запусков нет. | E2-01: нативные модели, posted normalizer, исключения и createPost; затем семантические routing patches и E2/E3 behavioral tests. Не заменять pending audit пустыми native stubs. |
| 2026-11-12 | E2-01; E2-03/CI-04/07 частично | Реализованы Bot, строгие Pydantic User/Channel/Message, posted normalizer, get_me/createPost, answer/reply и native errors. 63 теста прошли на Windows/Python 3.11/3.13/3.14; 26 новых model/local-HTTP tests проверяют subpath, thread roots, 401/403/429, invalid payloads, no-retry/unknown write outcome, cancellation, concurrency и cleanup. Postman simulation 1.82.0 прошла, вызовы подтверждены журналом, guest owner-search пропущен, контекст сохранён. Собственный Mattermost 11.1.0 с pinned images: реальное WS posted, answer с REST read-back, self suppression normalizer, nested thread, 401/403 и закрытие Bot прошли; CLI create_post отправил реальный post. Captured fixture имеет provenance и не содержит credentials. CI дополнен явно ограниченным native stand profile; внешнего CI нет. Отчёт: `docs/reports/native-client.json`. | E2-02/E3: Router/F/middleware/DI patches, SDK-owned WS и настоящий сквозной handler. E2-03 не закрыт по одному REST/normalizer/harness результату. Full core audit остаётся BLOCKED. |
| 2026-11-12 | E2-02/E2-03/E3-01/E3-03; E3-02 частично | 18 reviewed core modules promoted; exact replay/static audit ready (0 findings), native composition hash-pinned, 13 adapted upstream suites with source/patch/target hashes and exclusions. 213 tests + 56 subtests passed Windows/Python 3.11/3.13/3.14; Ruff/strict mypy passed. Настоящий Mattermost 11.1.0: SDK-owned WS → Router/F/DI → handler → answer/read-back, self suppression, 403 error observer, 401 startup и lifecycle cleanup. Local socket tests cover reconnect/backpressure/cancellation/startup/shutdown timeout. CI gates и installed consumer tests расширены; внешнего CI и публикации нет. Документы: `docs/native-routing.md`, `docs/reports/routing-core.json`. | E3-02: настоящий Redis profile; затем E4 HTTP ingress. |
| 2026-11-12 | E3-02 | RedisStorage/RedisEventIsolation — 19-й reviewed donor module; exact replay/static audit ready. Optional extra и явное client/pool ownership, native namespace, TTL, strict data, bounded advisory leases/cleanup. Real Redis 7.4.12: 28 tests/32 subtests; minimum redis-py 5.3.0 и locked 8.1.0. Полная Windows matrix 3.11/3.13/3.14: 236 tests/88 subtests. Воспроизведена и исправлена потеря cancellation на 3.11/8.1, усилен write-before-timeout test. Настоящий Mattermost+Redis FSM: set/read by separate client/clear через human WS; Memory профиль повторён. CI Redis job и base/extra installed-artifact verification; внешнего CI и публикации нет. `docs/native-redis.md`, `docs/reports/redis-core.json`. | E4-01 callback/slash payloads/tokens, затем deadlines и real HTTP integrations. |
| 2026-11-12 | E4-01–E4-03 | Native SlashCommand/CallbackQuery/PostRef, issued token/scope/peer auth, signed context, separate selected_option, single response/deadline, bounded explicit defer, response_url без Bearer/retry/redirect, HTTP/ASGI и combined HTTP/WS lifecycle. 21 reviewed donor modules, 15 upstream suites, exact replay/static audit ready. Windows Python 3.11/3.13/3.14: 342 tests + 103 subtests на каждой с real Redis, skips нет; Ruff/strict mypy/actionlint passed. Настоящий Mattermost 11.1: POST/GET slash, escaping, ACK/in_channel/read-back, defer/followup, пять hook sends, deadline/cancel, post actions/ephemeral actor WS/update/select/repeated presses/tamper rejection/cleanup. Postman faults подтверждены HTTP logs/mock state; guest owner search недоступен, Orbit 403. Baseline post-action navigation/error явно unsupported. Fixtures/provenance, contract/profile, example и docs/reports/http-core.json сохранены. Installed-artifact verifier включает HTTP/ASGI; отдельный receipt build/reports/distributions.json. UI rendering, внешний CI и публикация не проверены/не выполнялись. | E5-01 UI schemas/serialization/capability gates; затем forms и реальные клиенты. |
| 2026-11-12 | E5-01 частично | Все объявленные native Form/Blocks types, rich attachments/legacy actions, common signed tree/registry compiler и Bot.send_message UI arguments; conservative server/client/flag gates и точные datetime wire profiles. 18 immutable primary references из 11.1/11.7/11.11/12.0.2, authored declaration schemas, native-ui.md и typed examples. Windows 3.11/3.13/3.14: 353 tests + 125 subtests с real Redis, skips нет. Real 11.1 accepted SDK rich attachment и compiler-created signed callbacks; Postman actual HTTP/mock-state подтверждает UI serialization и прежние faults. CLIENT LIST lifecycle assertion исправлен на bounded remote convergence, production close не ослаблен. UI rendering, submissions и input-format translation ещё не подтверждены/не реализованы; E5 остаётся открытым, счёт 19/56. | Завершить supported-subset Slack/Adaptive input translation; E5-02 open_dialog/submissions/lookup/refresh/validation/parser/session/replay; E5-03 реальные клиенты. |
| 2026-11-12 | E5-02 | Native open_dialog/trigger shortcuts и actor WS, form HTTP/ASGI/Dispatcher events, single-response validation/next/lookup/refresh/cancel/action, schema-directed domain parser и lossless snapshots. Scoped opaque state; bounded Memory/Redis records; claim before business, terminal replay, stale-step checks, uncertain-outcome reconciliation и modal depth/live-child/parent guards. Настоящий 11.1 API/WS прошёл open/errors/correction/wizard/lookup/refresh/cancel/deadline/tamper/server-derived actor. Real Redis independent workers показали one handler и durable uncertain claim; Postman native writes/faults подтверждены HTTP log/mock state. Windows 3.11/3.13/3.14: 375 tests + 146 subtests, skips нет. 41 pinned primary references, sanitized captured fixtures/provenance, guide/example/contracts; UI rendering и newer client matrix не заявлены. Счёт 20/56. | Завершить E5-01 input adapters и E5-03 реальные клиенты. E6 provisioning, E7 full API, dialog/docs/release остаются в исходном scope. |
| 2026-11-12 | E5-01 завершён | Authored Slack/Adaptive input adapters к native Blocks, exact allowed fields/types/version gates, common text conversion/explicit workspace renderer, lossless action/data bindings и отсутствие silent fallback. Общий compiler/signing/capabilities сохранён. 10 новых unit cases и 2 local HTTP cases, offline example выполнен. Windows Python 3.11/3.13/3.14: 387 tests + 193 subtests с real Redis, skips нет; Ruff/strict mypy (92), reviewed replay/static audit ready, 45 primary refs и 3711 donor files verified. Wheel/sdist: по 130 installed consumer checks, все 62 source files совпадают с wheel. Contract/profile/docs/reports/ui-input-core.json сохранены. Реальные newer server/UI clients пока не проверены. Счёт 21/56. | E5-03: реальные web/desktop/mobile UI scenarios и newer-server API evidence. E6 onward и публикация остаются в исходном scope. |
| 2026-11-12 | E5-03 частично | Настоящий Windows/Chrome 157 web на pinned Mattermost 12.0.2: записанные сценарии всех 13 Form fields и 9 Blocks, Slack/Adaptive imports и signed callbacks; validation/correction, datetime, wizard, lookup/refresh/submit/cancel, file upload/read-back, nested submit/cancel/parent restore и fourth-modal refusal. Исправлены stable callback identity, opening-state read/cancel, lookup option objects и refresh prefill; snapshot metadata и stale-write guards покрыты regression tests. Windows 3.11/3.13/3.14: 389 tests + 199 subtests с real Redis, без skips. 11.1 API/WS regression, replay/audit, fresh installed-artifact checks; receipts/screenshots в docs/reports/ui-web-12.0.2.json. Инструкция: docs/ui-stand.md. Счёт остаётся 21/56. | Date bounds/timezone/label-position и legacy web cases; остальные 11.7/11.11/native desktop/mobile profiles. E5-03 не закрыт. |
| 2026-11-12 | E5-03 дополнение | Web 12.0.2: disabled cells вне date bounds и успешная отправка обеих включённых границ; GMT+5:30/Asia/Kolkata, manual-time off, 48 half-hour choices, 19:00 → 13:30 UTC; before/after для Checkbox inline caption/Radio/Group; legacy attachment, signed button/select callbacks и disabled click guard. 13 callback requests двух прогонов — 200, handler errors нет. Ещё 4 screenshots и 3 DOM excerpts: docs/reports/ui-web-12.0.2-additional.json. 63 runtime hashes неизменны, счёт 21/56. | Web 11.7/11.11, native desktop/mobile; полный E5-03 открыт. |
| 2026-11-12 | E5-03 web 11.11 | Отдельные pinned 11.7/11.11 stands healthy, setup identities/credentials/volumes изолированы; helper выбирает явный --profile. Реальный 11.11 web прошёл десять применимых field types, field error/correction, wizard, lookup/refresh, 9 Blocks/signed callbacks, imports, legacy menu, file/child submit/parent restore, date/manual datetime и cancel. File bytes скачаны ordinary actor и совпали. 31 callback HTTP 200, одна field-error response, handler errors нет; 8 screenshots: docs/reports/ui-web-11.11.0.json. 63 runtime hashes неизменны; Ruff/format/strict mypy (94), Compose config и evidence hashes прошли. Счёт 21/56. | Web 11.7, native desktop/mobile; дополнительные 11.11 parameter cases остаются unverified. |
| 2026-11-12 | E5-03 web 11.7 | Реальные восемь применимых field types, validation/correction, wizard, lookup/refresh/submit, обе date bounds, manual datetime и Asia/Kolkata/time menu, cancel. Native legacy 404 выявил ограниченный 11.1/11.7 route ID; исправлена проверка до HTTP, explicit newer profiles сохраняют `_`/`-`, ID не переписывается. Добавлены legacy declaration schema и две регрессии; повторный button/select callback прошёл. 391 tests + 225 subtests с real Redis на Windows 3.11/3.13/3.14, no skips; Ruff/format/mypy (94), native 11.1 regression/replay/audit/47 refs прошли. Отчёт: docs/reports/ui-web-11.7.0.json, 7 screenshots/2 DOM excerpts. Native apps не исполнялись. Счёт 21/56. | E6-01 как независимая реализация provisioning. E5-03 native desktop/mobile и отдельные parameter cases остаются открытыми. |
| 2026-11-12 | E6-01 частично | Slash description/hint/explicit triggers/provision=False, immutable metadata и attached-router CommandManifest/CommandSpec с literal aliases, deterministic fingerprint, conflict checks и native payload. Existing registration/traversal algorithms сохранены. Реальный Mattermost 12.0.2 создал три manifest commands с разными issued tokens, metadata round-trip и три callbacks/runtime posts с ordinary-actor read-back; development cleanup выполнен. Windows 3.11/3.13/3.14: 405 tests + 253 subtests с real Redis, no skips; native 11.1 HTTP/WS regression, strict mypy (102), Ruff/format, replay/audit/extraction и primary refs прошли. Guide/example и docs/reports/provisioning-commands.json. Счёт 21/56. | Setup-клиент, persistent ownership/secrets, bot/team/channel/membership reconciliation, conflicts, resume/concurrency/readiness; E6 остаётся открытым. |
| 2026-11-12 | E6-01 explicit SetupClient | Механически выделен общий native HTTP/session transport, runtime/setup credentials/pools раздельны. Typed bot/PAT/team/channel/membership/command operations, native PAT 200, safe secret snapshots, malformed/lost write → unknown без retry. Живой 12.0.2 создал bot/token/team/private channel, memberships, обновил команду без ротации token, callback/runtime post с ordinary-actor read-back; 403 ordinary create_bot и 401 после revoke без fallback; owned cleanup выполнен. Postman state/log: 13 command calls, POST/PUT/DELETE committed-before-timeout и по одной попытке; старые simulations прошли. Windows 3.11/3.13/3.14: 417 tests + 267 subtests с real Redis, no skips; native 11.1 REST/WS/HTTP/forms regression, Ruff/format/mypy (107), replay/audit/extraction и refs прошли. Evidence: docs/reports/setup-client.json. Счёт 21/56. | Protected ownership/secret store и journal, Bootstrap reconciliation, conflicts/recovery/concurrency/readiness. E6 открыт. |
| 2026-11-12 | E6-01 store/journal | Encrypted SQLite bindings/secrets/completion, durable pending/uncertain intents, explicit recovery и owned/reused checks; optional cryptography Fernet с внешним key, private Windows DACL и POSIX contract, directory-wide local OS lock. 17 новых cases/3 subtests включают tamper/wrong key/context, atomic rollback между records, native permissions/hardlink и actual process competition/kill/reopen. Живой 12.0.2: native PAT/command tokens сохранены, второй isolated process подтвердил identity, persisted binding callback/runtime post прочитан ordinary actor; только новые command/PAT очищены. Windows 3.11/3.13/3.14: 434 tests + 270 subtests с real Redis, no skips; Ruff/format/mypy (112), exact replay/audit/extraction ready. Evidence: docs/reports/provisioning-store.json. POSIX execution и полный E6 не закрыты, счёт 21/56. | Bootstrap planner/reconciler, actual native ownership/permission/feature checks, lost-secret recovery, multiple replicas/deployment readiness. |
| 2026-11-12 | E6-01–E6-03 | Automatic `Bootstrap` сверяет bot/team/channel/membership/commands с ownership evidence, exact native permissions, encrypted secrets и durable intent journal; runtime-only загрузка не получает setup credential или admin fallback. Живой Mattermost 12.0.2: 18 created/2 reused, repeat 20 reused, change 4 updated/18 reused; 2 teams, 2 private channels, 4 commands, четыре callbacks/posts, IDs и tokens сохранены. Postman stand-in записал 437 HTTP events для 403, committed-before-timeout/recovery, foreign trigger, feature allowlist, least privilege и shared OS lock. Windows 3.11/3.13/3.14: 445 tests + 275 subtests с real Redis, no skips; Ruff/format, strict mypy (119), replay/audit/extraction, 13 primary refs и native 11.1 regressions прошли. Финальные wheel/sdist прошли strict twine и по 188 installed checks. Evidence: `docs/reports/bootstrap.json`. Счёт 24/56. | E7-01: generated low-level API из pinned OpenAPI и явных overlays; E5-03 native desktop/mobile остаётся открытой. |
| 2026-11-12 | E7-01 | `tools.generate_native_api` извлёк 493 endpoints/491 unique operationId из pinned OpenAPI, сохранил две upstream-коллизии и сгенерировал immutable registry 17 reviewed JSON operations из authored overlay без schema prose. Отдельный `NativeAPI` строго проверяет path/query/body/status и uncertain writes. Живой Mattermost 11.1.0 принял все 17 operations ordinary bot token; явный overlay учёл native `SaveReaction` 200 рядом с documented 201, пустые preferences вернули raw null. Windows 3.11/3.13/3.14: 454 tests + 285 subtests с real Redis, no skips; Ruff/format, strict mypy (126), replay/audit/extraction прошли. Wheel/sdist: exact 82 source files, strict twine, оба install по 74 unit + 75 integration + 1 Redis config + 16 real Redis + 28 provisioning. Evidence: `docs/reports/native-api.json`. Счёт 25/56. | E7-02: typed high-level operations отдельно от generated registry; затем E7-03 full coverage/errors. |
| 2026-11-12 | E7-02 | Ручной typed facade добавил user/channel reads, direct channel, post edit/delete, Message shortcuts и потоковый multipart document upload без изменения generated registry. Reviewed `InputFile` algorithm адаптирован из pinned aiogram: без Telegram/session/URL/aiofiles, с owned handle и bounded chunks. Живой Mattermost 11.1.0 ordinary bot token выполнил все 8 methods/shortcuts, upload скачан byte-for-byte. Windows 3.11/3.13/3.14: 462 tests + 301 subtests с real Redis, no skips; Ruff/format (170), strict mypy (129), replay/audit 22 modules/30 decisions прошли. Wheel/sdist: exact 83 source files, strict twine, оба install по 78 unit + 78 integration + 1 Redis config + 16 real Redis + 28 provisioning. Evidence: `docs/reports/high-level-api.json`. Счёт 26/56. | E7-03: full operationId coverage/error matrix и unsupported operations. |
| 2026-11-12 | E7-03 | Exhaustive drift gate классифицирует 491 unique operationId/493 endpoints: 17 generated, 2 high-level-only, 470 unreviewed и 2 collision IDs/4 endpoints. Unsupported ID отклоняется до сети. Error tests закрывают typed statuses, unexpected 2xx/redirect, invalid JSON, redaction и disconnect; найден и отключён implicit aiohttp GET retry, теперь read/write делают одну попытку. Живой 11.1 повторно прошёл 17 low-level + 8 facade methods ordinary bot token. Windows 3.11/3.13/3.14: 467 tests + 312 subtests с real Redis, no skips; Ruff/format (172), mypy (130), replay/audit прошли. Wheel/sdist exact 83 source files, strict twine, оба install по 79 unit + 79 integration + Redis/provisioning. Evidence: `docs/reports/native-api-coverage-summary.json`. Счёт 27/56. | E8-01 dialog inventory/manifest; E5-03 native apps остаётся открытой. |
| 2026-11-12 | E8-01 | Pinned aiogram_dialog 2.6.0/Apache-2.0 inventory: 250 Python files, 111 package modules, 45 tests. Отдельный plan/manifest содержит 40 решений: 27 selected (3 copy/24 adapt), 13 exclude; Telegram keyboard/input/media/markup/background semantics не переносятся. Две extraction дали одинаковый receipt, src не изменён. Audit намеренно BLOCKED: 24 pending + 179 related findings, точный boundary проверяется CI. Windows 3.11/3.13/3.14: 469 tests + 312 subtests с real Redis, no skips; Ruff/format (174), mypy (131). Sdist несёт plan/manifest/evidence, wheel не содержит dialog runtime; strict twine и installed checks прошли. Evidence: `docs/reports/dialog-inventory.json`. Счёт 28/56. | E8-02 adaptations/widgets/session transitions и ready audit. |
| 2026-11-12 | E8-02 | Отдельный typed `mattergram_dialog`: transactional stack/context manager, registry, transitions/nested results, text widgets, native `FormWindow`, post/form renderers и signed `OpenFormActions`. 27 reviewed modules + 17 native boundaries replay exact; 0 pending, audit 0 findings. Native behavior покрывает rollback, isolation, Memory/real Redis, documented declaration/form submit и передачу signing context до wire compiler. Windows 3.11/3.13/3.14: 477 tests + 312 subtests с real Redis, no skips; Ruff/format (219), mypy (175). Wheel/sdist exact 127 source files, strict twine, оба install прошли 79 unit + 79 integration + 16 Redis + 28 provisioning checks. Evidence: `docs/dialog-porting.md`, `build/reports/dialog-audit.json`. Счёт 29/56. | E8-03 real catalog/card/form/confirmation scenario; E5-03 native apps остаётся открытой. |
| 2026-11-12 | E8-03 | `DialogRuntime`/`setup_dialogs` связывает slash, signed actions и managed submit/cancel с одним scoped stack. Local HTTP tests закрывают stale action, terminal replay, form→form next, cancel и Router fallthrough. Owned Mattermost 11.1 выполнил issued slash → catalog post → signed card action → signed form action → actor WS open_dialog → submit → confirmation; effect один, independent Redis read восстановил final context. Временные resources удалены. Windows 3.11/3.13/3.14: 482 tests + 312 subtests с real Redis, no skips; Ruff/format (224), mypy (178); audit 0. Evidence: `docs/reports/dialog-scenario.json`. Счёт 30/56. | E9-01 documentation scope; E5-03 native apps остаётся открытой. |
| 2026-11-13 | E9-01 частично | Runnable showcase использует один Mattermost post как многоэкранный интерфейс: две независимо paginated/filtered Markdown tables, 3 action groups/11 controls, Overview/Activity/About и 3 native forms. Callback обновляет text+props одним response; live событие приходит через WS, shared post изменяется REST text patch с сохранением props. Owned 11.1 прошёл slash, native form, независимые table mutations и WS→REST path; temporary resources удалены. Windows 3.11/3.13/3.14: 486 tests + 312 subtests с real Redis, no skips; Ruff/format (227), mypy (180). Guide/evidence: `docs/showcase.md`, `docs/reports/showcase.json`. Счёт остаётся 30/56. | Продолжить E9-01: generated reference, остальные guides/examples и documentation drift gates. |
| 2026-11-13 | E9-01 generated reference частично | Authored documentation manifest покрывает 20 public modules/183 exports, 18 guides и все 14 examples. Детерминированный generator создаёт API/public-api.json, exceptions, capabilities, examples index и llms.txt без сети/мутаций; exact drift/local links/example metadata проверяются тестами и CI. Исправлен stale `dialog_wizard` access. Windows 3.11/3.13/3.14: 489 tests + 312 subtests с real Redis, no skips; Ruff/format (229), mypy (181), actionlint и docs checks прошли. Strict twine прошёл; sdist содержит 9 проверенных docs inputs/outputs, wheel runtime-only. Evidence: `docs/reports/documentation.json`. Счёт 30/56. | Дописать Mattermost model/install/deployment/troubleshooting/migration guides; затем docs site и LLM evaluations. |
| 2026-11-13 | E9-01 завершён | Documentation manifest расширен до 23 guides: добавлены installation/first bot, Mattermost model, production deployment, troubleshooting/recovery и migration from aiogram. Все 14 examples имеют explicit status/profile/permissions/assertions/evidence; generated reference остаётся 20 modules/183 exports/335 members и детерминирован на Python 3.11/3.13/3.14. Registered local links, syntax/types, drift, Ruff/format (229), mypy (181), actionlint прошли. 3.14 validation выявила 50 ms test cleanup flake при 105 ms Proactor stall: ordinary test bound поднят до 500 ms, dedicated timeout case оставлен 50 ms; 10/10 targeted и полный matrix прошли, production default не менялся. По 489 tests + 312 subtests на 3.11/3.13/3.14 с real Redis, no skips. Strict twine/sdist docs/wheel boundary проверены. Evidence: `docs/reports/documentation.json`. Счёт 31/56. | E9-02: фиксированные LLM evaluation tasks и фактический воспроизводимый отчёт. |

## 1. Основной подход

Клонировать зафиксированные исходники как доноры, автоматически перенести выбранное ядро в новый namespace и адаптировать платформенные границы небольшими патчами.

Цель — сохранить готовые алгоритмы routing, фильтров, middleware, DI и FSM. Новая работа сосредоточена на Mattermost models, REST/WS/HTTP integration, native UI и provisioning.

Полный форк с последующим бессистемным удалением файлов не выбирается. Он легко затягивает Telegram-типы в новый пакет и увеличивает объём ошибок. Извлечение по allowlist делает границы переноса проверяемыми.

## 2. Разделение исходников

| Область | Назначение | Правило |
| --- | --- | --- |
| upstream snapshots | aiogram, затем aiogram_dialog | Не изменять; зафиксировать commit |
| schemas/upstream | Схема Mattermost нужной версии | Хранить источник и checksum |
| manifests | Решения по переносу | Редактировать осмысленно и проверять |
| tools | Inventory, extraction, codemods, audit | Детерминированные операции |
| build/port-staging | Результат пробного переноса | Можно пересоздать |
| src/mattergram | Реальный поддерживаемый SDK | Не затирать повторной extraction |
| src/mattergram_dialog | Отдельное расширение | После стабилизации SDK |
| tests | Перенесённые и нативные проверки | Отмечать происхождение и scope |
| docs | Дизайн, guides и reference | Отделять authored и generated |

Структура частично реализована: maintained каркас, tools/manifests/tests, pinned snapshots и отдельный staging. Минимальный рабочий routing/Memory SDK реализован; дальнейшие native/UI/dialog этапы открыты.

## 3. Фиксация источников

Создать upstream lock с repository URL, tag, commit, checksum и датой получения. Поля не заполнять выдуманными SHA.

Включить:

- aiogram как основной донор;
- aiogram_dialog как донор второго этапа;
- Mattermost OpenAPI и дополнительные UI references;
- версии генераторов и codemods.

aiohttp, Pydantic, Redis client и magic-filter использовать как зависимости. Не клонировать все сторонние библиотеки без конкретной необходимости.

Shallow clone или snapshot экономят время загрузки и диск. Токены экономятся прежде всего ограничением исходников, показываемых модели.

## 4. Read-only inventory

Первый скрипт собирает:

- Python-модули и их локальные imports;
- публичные exports и re-exports;
- ссылки на Telegram-модели и методы;
- связанные tests/fixtures;
- build/config dependencies;
- LICENSE, NOTICE и происхождение заимствуемых файлов.

Статический анализ imports выполняется через AST/CST. Dynamic imports, star imports, TYPE_CHECKING и runtime registration отражаются в отчёте как особые случаи. Граф не считается доказанно полным при наличии неразрешённых динамических связей.

Результат должен быть кратким и машиночитаемым. Модель читает сводку и конкретные dependency paths, а не все исходники.

## 5. Manifest переноса

Для каждого файла или символа определить одну операцию:

| Решение | Действие |
| --- | --- |
| copy | Перенос алгоритма с namespace rewrite |
| adapt | Перенос плюс перечисленные семантические изменения |
| generate | Создание из закреплённого источника схемы |
| implement | Новый код Mattermost |
| exclude | Не переносить; указать причину |
| unresolved | Решение требует анализа; этап не объявляется завершённым |

Manifest хранит source path, target path, source commit/hash, действие, зависимости, linked tests, codemods и attribution.

Копирование транзитивных зависимостей не должно автоматически разрешать весь Telegram API. Неразрешённая зависимость останавливает extraction и попадает в отчёт.

## 6. Первоначальные кандидаты

| Компонент | Направление |
| --- | --- |
| Handler objects и filtering chain | Повторное использование после проверки зависимостей |
| Middleware orchestration | Повторное использование алгоритма |
| Router traversal | Сохранение поведения, замена observers |
| FSM State/Context/Storage abstractions | Сохранение интерфейса, адаптация keys |
| Generic utilities | Перенос только необходимых |
| Dispatcher | Переработка transport и event normalization |
| Bot/types/methods | Собственные модели и Mattermost API |
| Telegram-only observers/widgets | Исключить |
| aiogram_dialog | Отдельная поздняя extraction |

Это кандидаты, а не уже проверенный allowlist. Точный состав формируется inventory.

## 7. Инструменты переноса

| Планируемый инструмент | Вход | Выход/проверка |
| --- | --- | --- |
| inventory_upstream.py | Зафиксированный snapshot | Imports, symbols, tests, unresolved links |
| extract_core.py | Lock и manifest | Staging tree, attribution, receipt |
| rewrite_imports.py | Staging и правила codemod | Предсказуемые namespace changes |
| audit_port.py | Staging/SDK | Forbidden imports, exports, missing deps |
| compare_upstream.py | Старый и новый snapshots | Изменения только в используемом scope |
| generate_native_api.py | Закреплённая OpenAPI | Low-level client и coverage |
| generate_api_reference.py | Реальные exports/signatures | Reference data |
| verify_examples.py | Примеры и profile | Imports/types/поведенческие результаты |

inventory_upstream/extract_core/rewrite_imports/audit_port реализованы; запускать как Python-модули из корня проекта по `porting-tools.md`. Остальные инструменты таблицы ещё предстоит реализовать.

Для структурных изменений Python рекомендуется LibCST. Обычная текстовая замена подходит только для однозначных механических случаев.

## 8. Безопасная воспроизводимость

1. Extraction пишет в staging.
2. Перед изменением проверяет ожидаемый source hash.
3. Codemods выполняются в фиксированном порядке.
4. Проверки показывают unresolved dependencies и diff.
5. Проверенный результат переносится в поддерживаемые исходники.

Повторный запуск не перезаписывает native modules и ручные адаптации. Последующие upstream updates оформляются отдельным переносом с review, не пересозданием всего проекта поверх текущего кода.

Сохранять исходный алгоритм, если меняется только платформенный интерфейс. Не просить модель перегенерировать готовый файл целиком без необходимости.

## 9. Минимальный работающий срез

Первый результат: normalizer принимает реальный posted event, Router проверяет F.text, handler выполняет message.answer, transport создаёт Mattermost post.

Для этого достаточно минимальных моделей User/Channel/Message, одного API метода, одного события и переносимого ядра routing. Остальные операции не маскируются рабочими заглушками.

Проверяются неправильный токен, ошибка права, собственное исходящее WS-событие и завершение lifecycle. Успешный импорт не равен работоспособности бота.

## 10. Последовательность этапов

| Этап | Deliverable | Завершение |
| --- | --- | --- |
| 0 | Lock, inventory, manifest, API contract | Известны границы extraction |
| 1 | Скрипты extraction/codemod/audit | Повторяемый staging без скрытых зависимостей |
| 2 | Message → handler → answer | Реальный минимальный сценарий |
| 3 | Routing/middleware/DI/FSM | Перенесённые поведенческие проверки |
| 4 | Callback и slash HTTP | Правильные response lifecycle и tokens |
| 5 | Native forms и Blocks | Все declared schemas и capability gates |
| 6 | Provisioning | Бот, команды, каналы, членство без дублей |
| 7 | Расширенный native API | Coverage и ошибки документированы |
| 8 | mattergram_dialog | Каталог → карточка → форма → подтверждение |
| 9 | Docs/LLM evaluation/release | Реальные примеры и опубликованные ограничения |
| 10 | Skills | Инструкции к стабильной версии |

Ранний прототип может проверять формы и provisioning до полного расширения остальных операций, если это снимает архитектурный риск.

### Этап 0. Источники, inventory и контракт

- [x] **E0-01.** Получить неизменяемые snapshots доноров и закреплённые схемы Mattermost; записать реальные commits, checksums, источники и лицензии в upstream lock.
- [x] **E0-02.** Реализовать read-only inventory: imports, exports, связанные tests/fixtures, лицензии и неразрешённые динамические зависимости.
- [x] **E0-03.** Составить allowlist manifest с решениями copy/adapt/generate/implement/exclude/unresolved и происхождением файлов.
- [x] **E0-04.** Зафиксировать API минимального среза, границы переноса и открытые решения. Сохранить отчёт inventory и критерии приёмки.

### Этап 1. Инструменты переноса

- [x] **E1-01.** Реализовать extraction в staging с проверкой source hashes, attribution и receipt.
- [x] **E1-02.** Реализовать детерминированные namespace codemods и запрет неявного расширения allowlist.
- [x] **E1-03.** Реализовать audit запрещённых imports/exports, missing dependencies и unresolved links; ошибки должны останавливать перенос.
- [x] **E1-04.** Проверить повторяемость staging и сохранность поддерживаемых native modules и ручных адаптаций при повторном запуске.

### Этап 2. Минимальный работающий срез

- [x] **E2-01.** Реализовать минимальные User/Channel/Message, posted normalizer и один метод создания Mattermost post.
- [x] **E2-02.** Соединить реальное posted event, Router, F.text, handler и message.answer; проверить сценарий на стенде Mattermost.
- [x] **E2-03.** Проверить неправильный токен, недостаточные права, собственное исходящее WS-событие и завершение lifecycle.

### Этап 3. Routing, middleware, DI и FSM

- [x] **E3-01.** Перенести согласованное ядро routing, filters, middleware и DI с привязанными поведенческими тестами.
- [x] **E3-02.** Адаптировать FSM keys и storage; проверить state isolation и заявленные storage profiles.
- [x] **E3-03.** Проверить routing order, handler data, UNHANDLED/SkipHandler; зафиксировать исключённые upstream тесты с причинами.

### Этап 4. Callback и slash HTTP

- [x] **E4-01.** Реализовать нормализацию callback/slash payloads, проверку tokens и разделение callback data и selected_option.
- [x] **E4-02.** Реализовать response lifecycle, допустимые ответы, ошибки и таймауты по контракту Mattermost.
- [x] **E4-03.** Проверить HTTP-сценарии с протокольными fixtures и реальными интеграционными запросами.

### Этап 5. Native forms и Blocks

- [x] **E5-01.** Реализовать объявленные UI schemas, сериализацию и capability gates для целевых версий и клиентов.
- [x] **E5-02.** Реализовать forms submission, валидацию, ошибки и parser wire values согласно контракту полей.
- [ ] **E5-03.** Проверить UI-сценарии на заявленных клиентах; записать подтверждённые возможности и ограничения.

### Этап 6. Provisioning

- [x] **E6-01.** Реализовать provisioning бота, команд, каналов и членства с явным ownership и необходимыми правами.
- [x] **E6-02.** Проверить reconciliation и повторный запуск без дублей, хранение secrets и обработку недостаточных прав.
- [x] **E6-03.** Проверить deployment-сценарий на стенде и отсутствие административного fallback в runtime.

### Этап 7. Расширенный native API

- [x] **E7-01.** Реализовать генератор low-level API из закреплённой OpenAPI и явных overlays с источниками.
- [x] **E7-02.** Реализовать согласованные high-level операции отдельно от generated files; не перезаписывать ручной facade.
- [x] **E7-03.** Сформировать operationId coverage, проверить ошибки и документировать неподдерживаемые операции.

### Этап 8. mattergram_dialog

- [x] **E8-01.** После стабилизации SDK выполнить отдельный inventory/manifest и выборочную extraction aiogram_dialog.
- [x] **E8-02.** Адаптировать widgets, session isolation и переходы с учётом возможностей Mattermost.
- [x] **E8-03.** Проверить сценарий «каталог → карточка → форма → подтверждение» и связанные поведенческие тесты.

### Этап 9. Документация, оценка и релиз

- [x] **E9-01.** Выполнить согласованный scope DOCUMENTATION_PLAN.md: guides, generated reference, проверяемые examples и опубликованные ограничения.
- [ ] **E9-02.** Выполнить фиксированный набор LLM evaluation задач и записать фактические результаты без неподтверждённых оценок.
- [ ] **E9-03.** Закрыть обязательные проверки раздела 13, выпустить стабильную версию и подтвердить установку из PyPI.

### Этап 10. Skills

- [ ] **E10-01.** Подготовить инструкции для стабильного API на основе проверенных сценариев и документации конкретной версии.
- [ ] **E10-02.** Проверить инструкции на задачах создания бота; записать результаты, ограничения и требования к обновлению.

## 11. Тесты и fixtures

Переносить вместе с алгоритмом связанные поведенческие tests: routing order, filters, middleware, handler data, UNHANDLED/SkipHandler и state isolation.

Telegram fixtures заменять собственными event fixtures. Исключённые тесты фиксировать с причиной; не объявлять их прохождение проверкой совместимости.

Добавить нативные протокольные fixtures и интеграционные сценарии для HTTP response, WS reconnect, string IDs, тредов, ephemeral и forms.

Не писать тесты ради повторения реализации. Проверять наблюдаемое поведение и платформенные границы.

## 12. Генерация API

Из OpenAPI можно получить low-level request/response wrappers, endpoint paths и coverage. Это не заменяет:

- high-level semantics answer/reply;
- нормализацию WS;
- управление HTTP ответом;
- формы, описанные вне OpenAPI;
- provisioning ownership/reconciliation;
- session isolation.

Генератор не должен изменять manually maintained facade. Generated files отмечаются и отделяются. Для отсутствующих в схеме данных нужны явные overlays с источниками.

## 13. Лицензии, упаковка и CI

При заимствовании сохраняются лицензии и attribution. Не копировать исходный pyproject, workflows и release secrets целиком: они относятся к upstream проекту.

Для своего пакета настроить:

- [x] **CI-01. Каркас пакета.** Создать `src/mattergram`, структуру tests/examples/tools/manifests, `pyproject.toml`, лицензию и attribution. Структуру отдельного mattergram_dialog подготовить при его реализации.
- [ ] **CI-02. Упаковка и окружение.** Настроить package metadata, dependencies/extras, `requires-python`, `py.typed`, один источник версии и воспроизводимое окружение разработки. Проверить доступность имён в PyPI.
- [x] **CI-03. Статические проверки.** Настроить formatter/linter и type checking; описать одинаковые локальные и CI-команды.
- [ ] **CI-04. Базовый workflow.** Создать `.github/workflows/ci.yml` для PR и основной ветки: audit переноса, contract tests, integration profiles, таймауты и отчёты. Интеграционные jobs явно показывают, какой стенд и profile проверены.
- [ ] **CI-05. Матрица Python и ОС.** Зафиксировать поддерживаемые версии после проверки зависимостей. Предварительно: Linux с Python 3.11–3.15; Windows/macOS с минимальной и максимальной версиями на PR и полной заявленной матрицей перед релизом.
- [ ] **CI-06. Совместимость зависимостей.** Проверять минимальные заявленные и свежие допустимые runtime-зависимости в отдельных окружениях, а также базовую установку и заявленные extras, включая Redis profile.
- [ ] **CI-07. Дистрибутивы.** Собирать wheel и sdist изолированно; проверять метаданные, README, ресурсы и attribution. Тестировать установленный wheel вне checkout и wheel, повторно собранный из sdist.
- [ ] **CI-08. Документация и API.** Подключить examples checks, API surface drift, coverage reports и запрет Telegram-only exports; сохранить происхождение заимствованных тестов.
- [ ] **CI-09. Обязательные проверки.** Настроить required checks для слияния, минимальные permissions jobs и проверенные версии actions; убедиться, что ошибка блокирует merge и релиз.
- [ ] **CI-10. Доступ к индексам.** Настроить GitHub Environments и отдельные Trusted Publishers для TestPyPI/PyPI с точными repository/workflow/environment. Дать `id-token: write` только publish jobs.
- [ ] **CI-11. Релизный workflow.** Создать `.github/workflows/release.yml`: проверить тег, версию и релизный commit, выполнить обязательные проверки, один раз собрать и проверить артефакты, опубликовать в TestPyPI и затем те же файлы в PyPI через OIDC. Сборку отделить от publish jobs.
- [ ] **CI-12. Проверка публикации.** Проверить точную версию из TestPyPI в чистом окружении: зависимости из PyPI, пакет из TestPyPI с `--no-deps`. После публикации в PyPI проверить обычную установку и основной сценарий. Сохранить ссылки на CI и релизные артефакты.
- [ ] **CI-13. Повторные релизы.** Описать changelog, prerelease/stable versions, GitHub Release и восстановление частичной публикации. Проверять совпадение hashes уже загруженных файлов; не пересобирать другое содержимое под той же версией.
- [ ] **CI-14. Сопровождение CI.** Настроить обновления зависимостей/actions и периодические проверки свежих зависимостей и следующей предварительной версии Python. Экспериментальную поддержку отделить от обязательных проверок.

Фактические versions и permissions релизного CI задаются при реализации. Никаких скопированных upstream publish credentials или ссылок на чужой пакет.

Инфраструктуру подключать с первого рабочего каркаса; CI-01–CI-04 не откладывать до этапа 9. Фактический релиз и внешние настройки не считать выполненными по одному наличию YAML.

## 14. Работа модели с минимальным контекстом

Один цикл задачи:

1. Прочитать STATUS и относящийся к задаче контракт.
2. Найти символы через rg.
3. Прочитать изменяемые файлы и непосредственные интерфейсы.
4. Выполнить механическую часть скриптом.
5. Адаптировать небольшой семантический участок.
6. Запустить соответствующие проверки.
7. Записать результат, ошибки и следующий шаг в STATUS.

В prompt включать цель, входные файлы, границу изменений и критерий приёмки. Не прикладывать каждый раз весь repository или весь дизайн.

Ошибки выводить кратко с нужным traceback. Повторные массовые dumps не добавляют достоверности.

Не заявлять заранее процент экономии токенов. Сравнивать затраты на одинаковых задачах и фактическое число исправлений.

## 15. Обновление upstream

При новой версии:

- [ ] **UP-01.** Получить новый snapshot рядом со старым.
- [ ] **UP-02.** Сравнить только используемый manifest scope.
- [ ] **UP-03.** Проверить изменения dependencies.
- [ ] **UP-04.** Повторно выполнить extraction в staging.
- [ ] **UP-05.** Перенести полезные изменения.
- [ ] **UP-06.** Выполнить тесты и обновить provenance.
- [ ] **UP-07.** Пересобрать reference/coverage.
- [ ] **UP-08.** Описать изменения семантики, если они есть.

Native Mattermost modules не перегенерируются из Telegram upstream.

UP-01–UP-08 относятся к будущему обновлению upstream, не блокируют первый релиз. Для каждого обновления создавать отдельную запись прогресса с исходным и целевым commits; отметки предыдущего обновления сохранять в журнале.

## 16. Ближайший результат

Lock/inventory/manifest и инструменты переноса подтверждены этапами E0/E1. 19 выбранных модулей перенесены через reviewed patches; строгий audit и behavioral suites проходят.

E2/E3 подтвердили posted → Router/F/DI → handler → настоящий createPost и Memory/Redis FSM. E4 подтвердил issued slash tokens, post-action signed context, response lifecycle/deadlines и реальный combined HTTP/WS runtime. Следующий результат — E5 UI/forms. Документационный сайт, dialog extension и релиз сохраняются в исходном scope последующих этапов.

## Источники

- [aiogram](https://github.com/aiogram/aiogram)
- [aiogram_dialog](https://github.com/Tishka17/aiogram_dialog)
- [LibCST codemods](https://libcst.readthedocs.io/en/latest/codemods.html)
- [Mattermost API](https://docs.mattermost.com/api)

