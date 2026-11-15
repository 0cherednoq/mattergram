# Состояние проекта

**Дата:** 15 ноября 2026 года.  
**Этап:** E0–E4, E5-01–E5-02, E6, E7 и E8 завершены; E5-03 проверка native clients открыта.  
**Задачи:** закрыты E0-01–E0-04, E1-01–E1-04, E2-01–E2-03, E3-01–E3-03, E4-01–E4-03, E5-01–E5-02, E6-01–E6-03, E7-01–E7-03, E8-01–E8-03, E9-01, CI-01 и CI-03: 31 из 56. Остальные пункты исходного плана открыты.

## Готово

- Основной дизайн v1.2 собран в docs/aiogram_mattermost_fork_spec.md.
- Зафиксирован приоритет нативных возможностей Mattermost и узнаваемого API aiogram.
- Описаны commands bootstrap и автоматическая настройка каналов/team.
- Выбран подход с исходниками-донорами, allowlist и codemods.
- Подготовлен отдельный план документации и проверки примеров.
- Подготовлена краткая инструкция входа для coding agent.
- `upstream.lock.json`: реальные commits и SHA-256 всех 3711 файлов четырёх источников. aiogram 3.31.0, aiogram_dialog 2.6.0, API Mattermost 11.1.0, закреплённые UI references. Snapshots восстановимы и исключены из дистрибутивов.
- `tools/fetch_upstream.py`: загрузка/восстановление, read-only verify, проверка file set и hashes; запрет тихой перезаписи lock и изменённого snapshot.
- `tools/inventory_upstream.py`: AST imports/guards/exports/symbols, прямые связи tests, fixtures, build dependencies и runtime-registration candidates. Донор не исполняется; ограничения полноты перечислены в отчёте.
- `tools/build_manifest.py`: 25 явных решений, 19 copy/adapt и 6 exclude; точные source hashes, зависимости, лицензии и связанные tests. Без автоматического разрешения Telegram aggregates.
- `docs/minimal-slice-contract.md` и `contracts/minimal_api.pyi`: posted → Router/F → answer, ошибки/ресурсы, нативные IDs, открытые решения и приёмка.
- Устанавливаемый `src/mattergram` с `py.typed`, одним источником версии и нативным Bot. Доступны User/Channel/Message, posted normalizer, get_me/createPost, answer/reply и typed native exceptions.
- `uv.lock`, Ruff, strict mypy, тесты инструментов, `CONTRIBUTING.md`, provenance/licenses.
- Локальный `.github/workflows/ci.yml`: quality, upstream audit, изолированная сборка/установка и итоговый required job. SHA actions проверены по upstream refs; actionlint 1.7.12 прошёл. Внешний CI не запускался.
- `tools/extract_core.py`: 19 явных модулей в `build/port-staging`, проверка source/manifest/patch hashes, лицензии, attribution и receipt. Maintained src и изменённый staging не перезаписываются.
- `tools/rewrite_imports.py`: AST/token namespace codemod с сохранением комментариев, строк, encoding и line endings; неоднозначный unaliased import отклоняется.
- `tools/_patches.py`: replay reviewed семантических patches по точным pre/post hashes и числу замен, без fuzzy matching. Флаг applied без pinned patch запрещён.
- `tools/audit_port.py`: integrity, forbidden imports/exports, missing modules/attributes, undeclared external roots, dynamic links и pending adaptations. Любая ошибка CLI даёт exit code 1.
- `tools/verify_extraction.py`: повторяемость реального allowlist и неизменность maintained src. Строгая статическая готовность выбранного core теперь ready: 19 модулей и нативные границы совпадают с reviewed replay; полный продукт этим не объявляется готовым.
- `src/mattergram/bot.py`: lazy owned aiohttp pool, identity check при async context entry, URL subpath, no redirects/retries, terminal close, cancellation/startup cleanup и различение ошибки соединения от неопределённого исхода уже отправленной записи.
- Pydantic models без ID coercion, unknown/raw fields, timezone-aware milliseconds и detached snapshots. posted сохраняет channel_type/team_id и полный WS envelope; username не выдумывается и HTTP enrichment отсутствует.
- `examples/create_post.py` выполнен против собственного настоящего stand. Два REST endpoints описаны в authored `contracts/native-rest.openapi.json`; vendor prose не копировалась.
- Собственный Mattermost 11.1.0/PostgreSQL Docker stand с image digests и loopback binding. Admin fixture bootstrap отделён от runtime bot token. `tools/verify_native_stand.py` прошёл реальные REST/WS-проверки нативного клиента.
- Postman 1.82.0 guest: локальные mock/simulation/context и выполненный native-client fault profile; ничего не опубликовано, owner workspace search пропущен. Нужные вызовы подтверждены журналом, simulation корректно остановлена.

## Предыдущий этап: routing

- Реализованы 18 allowlisted core modules через pinned semantic patches; `tools.promote_core --check` подтверждает точное совпадение maintained source с replay. Composite staging читает только hash-pinned native files; extract не пишет в src. Строгий static audit: ready, 0 findings. Три extraction запуска повторяемы, maintained source не меняется.
- Работают Router/F, generic event/handler/middleware, named DI, flags, filter logic, State/StatesGroup/FSMContext, MemoryStorage и native string StorageKey. Reserved application/filter context keys защищены от подмены; async callable handlers ожидаются. Изоляция state на server/bot/channel/user/thread без фиктивных IDs; отменённые waiters не оставляют locks, данные копируются глубоко.
- Dispatcher: authenticated SDK-owned WS, bounded queue, sequential worker, reconnect/backoff/jitter, raw events, self suppression, error observer, startup/reverse shutdown, handler drain/timeout и owned-resource cleanup. WS не durable queue; возможны потери при разрыве. Shutdown callbacks ограничены timeout. Runtime не использует admin fallback.
- 213 тестов + 56 subtests проходят на Windows/Python 3.11, 3.13 и 3.14. Из них 131 тест — 13 адаптированных upstream suites; hashes источников/patches/результатов и причины изменения/исключения перечислены в `manifests/ported-tests.json`. Ни donor conftest, ни Telegram runtime не импортируются. Redis/Mongo/PyMongo profiles не заявлены пройденными.
- 7 local WS integration tests проверяют реальный socket/challenge/ack (включая hello до ACK), subpath, Router/F/DI/answer, self suppression, 403 → error observer, reconnect, startup failure, remaining reverse cleanup, cancellation и handler timeout. Исправлен trace callback, ломавший WS handshake; WS redirects отклоняются до отправки credential challenge.
- Повторно пройден настоящий Mattermost 11.1.0: выданный bot token → SDK-owned WS → Router/F/DI → handler → answer с REST read-back, self-event counter, 403 в error observer, 401 startup failure и closed Bot/Dispatcher. `build/reports/native-stand.json`, сохраняемая сводка `docs/reports/routing-core.json`. Это дополняет native client/Postman profile предыдущей сессии.
- Ruff lint/format, strict mypy и reviewed test replay проходят. CI дополнен обязательными `promote_core --check`/`verify_behavioral_port`; real stand job теперь включает routing. Внешний GitHub CI по-прежнему не запускался.
- Пример `examples/routing_bot.py`, границы и lifecycle: `docs/native-routing.md`. Изолированный consumer verifier расширен до 44 model/routing/FSM/local HTTP+WS tests; результаты wheel/sdist см. `build/reports/distributions.json`.
- PyPI/TestPyPI не использовались для публикации. Имя по прежней проверке не резервировалось. Пакет остаётся pre-alpha 0.1.0a1.

## Последнее изменение: E3-02

- Через отдельный reviewed Redis profile и core manifest добавлен 19-й donor module — RedisStorage/RedisEventIsolation. Алгоритмы state/data/key/serialization и redis-py token/SET-NX/Lua ownership сохранены; семантические изменения закреплены exact patches. Composite static audit ready, 0 findings; maintained port соответствует replay.
- Optional `redis` extra (`redis>=5.3,<9`); базовый import не требует Redis. Явное владение: constructor заимствует client, from_url владеет client/pool, create_isolation заимствует storage client. Close запрещает дальнейшую работу и не удаляет серверные данные. Активное isolation нельзя закрыть.
- Проверены namespace всех native scopes, две dialog_session_id одного пользователя и отдельная обычная беседа, все пять Dispatcher FSM strategies, bytes/decoded clients, Mapping/custom JSON hooks, malformed stored data, реальное TTL expiration, независимые clients и 20 конкурентных read-modify-write без потерь.
- Lease/acquire/handler/release ограничены. Исправлена воспроизведённая потеря cancellation в сочетании CPython 3.11/redis-py 8.1: собственный ограниченный polling проверяет pending cancellation между атомарными попытками. Проверены быстрая отмена waiter, сохранение чужого lock, отмена holder, вторая отмена во время release, истёкший handler budget, late acquisition и видимая потеря ownership. Advisory lease не объявляется fencing или бизнес-транзакцией.
- 236 tests + 88 subtests проходят с настоящим Redis на Windows/Python 3.11/3.13/3.14. Отдельный minimum-client profile: redis-py 5.3.0 — 28 tests + 32 subtests; locked latest — 8.1.0. Без явного Redis URL реальные cases пропускаются, но mandatory stand verifier запрещает skips.
- Настоящий Redis 7.4.12 и Mattermost 11.1.0: два human WS events устанавливают/читают/очищают FSM, данные дополнительно прочитаны независимым Redis client. Отдельно повторён Memory профиль. Пройдены 401/403/error observer/self suppression/cleanup. Отчёты: `build/reports/redis-stand.json`, `native-stand-redis.json`; сохраняемая сводка `docs/reports/redis-core.json`.
- Wheel/sdist verifier проверяет extras metadata, base install без Redis, extra install и configuration tests, а с реальным профилем — все 13 Redis cases без skips. Native consumer suite по-прежнему содержит 44 проверки. CI дополнен mandatory Redis/Mattermost/installed-extra job; внешнего GitHub запуска пока нет.
- Обновлены `examples/redis_bot.py`, `docs/native-redis.md`, provenance upstream storage suites и profile manifest. У старого HTTP timeout test устранена неподтверждённая 50ms cold-start предпосылка: тест теперь явно наблюдает запись сервером до проверки timeout/no-retry.

## Последнее изменение: E4-01–E4-03

- Native SlashCommand/CallbackQuery/PostRef, строгие normalizers, per-command issued token и scope/method checks, обязательный доверенный transport peer. Signed callback context с key rotation/expiry/scope; data и selected_option разделены. Подпись context не объявляется аутентификацией произвольных actor fields body; повторные намеренные нажатия разрешены.
- Command и CallbackData — ещё два reviewed donor modules: теперь 21 модуль, 28 явных решений, 15 перенесённых upstream suites. Восстановлены реальные Command constructor cases в logic tests. Exact patch replay, повторяемая extraction и composite static audit проходят, 0 findings; все 3711 snapshot files неизменны.
- ResponseController завершает initial response один раз; короткий None → допустимый JSON ACK, UNHANDLED → 404, timeout → 504 с cancellation. Явный defer имеет bounded capacity/time/drain; это non-durable работа в памяти процесса. DelayedResponder ограничивает origin/path, пять попыток/30 минут одного handle, не отправляет Bearer/cookies, не делает retry/redirect, сохраняет неизвестный исход записи.
- HttpAdapter/aiohttp и ASGIAdapter используют один ingress core. Dispatcher.start объединяет HTTP/WS lifecycle; bind failure, disconnect, deadline, shutdown и FSM lock cleanup проверены. Mountable ASGI/aiohttp заимствуют Bot/Dispatcher; владелец закрывает их после drain.
- Настоящий Mattermost 11.1.0: issued POST/GET slash tokens, escaping GET arguments, initial in_channel/read-back, JSON ACK, explicit defer/followup, пять response_url posts/read-back, deadline, настоящие post actions/update/read-back, ephemeral_message на WS адресата, menu data/selected_option и два повторных нажатия, реальная пересылка подменённого context → SDK 403 до handler. Combined HTTP/WS cleanup проходит. Это API/transport proof; UI rendering не проверен.
- В baseline 11.1 post-action navigation/inline error отсутствуют в закреплённой модели: SDK явно поднимает UnsupportedCapabilityError. Новые web docs не используются как доказательство старой версии. Native source URLs/hashes без копирования server code записаны в manifests/http-profile.json; authored contract — contracts/http-ingress.openapi.json.
- Полная Windows matrix Python 3.11/3.13/3.14: **342 tests + 103 subtests**, настоящий Redis в каждой, skips нет. Ruff lint/format, strict mypy (70 файлов), actionlint 1.7.12 проходят. Real HTTP CI step добавлен; внешний GitHub CI не запускался.
- Local Postman simulation 1.82.0: пять фактических HTTP sends и локальный отказ шестого, 401/403/429/302 без повторов, write-before-timeout → RequestOutcomeUnknown и одна запись в mock state. CLI request log сохранён. Guest owner search недоступен; Orbit search вернул 403. Provider acceptance установлен отдельным реальным стендом, simulation подтверждает fault behavior SDK.
- Обезвреженные captured fixtures с provenance, examples/http_bot.py, docs/native-http.md и сохраняемый source/protocol report docs/reports/http-core.json. Installed-artifact verifier расширен HTTP/ASGI cases; точный отдельный результат сборки/установки — build/reports/distributions.json. Credentials и private captures остаются в ignored build/; публикации нет.

## Последнее изменение: E5-01 частично

- Добавлены `mattergram.ui.forms` со всеми 13 объявленными типами полей/групп (MultiSelect — select+multiselect), native `ui.blocks` со всеми 9 типами, legacy Actions и rich Attachments. Strict kwargs, type-specific constraints, глобальная уникальность field names, native matrix/default/date/time форматы и version-selected datetime_config. Form title различается: 24 UTF-8 bytes у 11.1/11.7/11.11 и 80 у 12.0.2.
- Общий compiler согласует native tree/registry/legacy actions, создаёт допустимые технические action IDs отдельно от callback data, подписывает context через ActionContext, проверяет missing/unused/duplicate IDs, query/tree/text limits и Column placement. Подключён к Bot.send_message; capability или binding error возникает до HTTP write. Raw native context остаётся явным escape hatch, без обещания аутентификации SDK.
- UIProfile хранит точные referenced server versions, явные client declarations/evidence, unknown/unsupported states, MmBlocksEnabled и explicit overrides для unknown. Неизвестная поддержка клиента не считается supported автоматически. Unsupported серверные поля или выключенный flag override не обходит. Никаких обещаний проверенного UI клиента пока нет.
- 18 primary source files из четырёх закреплённых commits описаны в `manifests/ui-profile.json`; `tools.fetch_ui_references` восстанавливает/сверяет hashes без перезаписи изменённых files. Referenced source остаётся в ignored build/, не исполняется и не переносится в MIT runtime. CI добавлен read-only reference verify после fetch; внешнего запуска CI нет.
- Настоящий Mattermost 11.1 принял SDK-created rich attachment; button/select callbacks теперь созданы общим compiler и прошли прежний реальный HTTP/WS profile. Local Postman simulation подтвердила реальную отправку SDK UI props и содержимое mock state; прежние response_url fault cases проходят. Это transport/API proof, не UI rendering.
- Windows Python 3.11/3.13/3.14: **353 tests + 125 subtests** на каждой с real Redis, skips нет. Обнаружена задержка server-side CLIENT LIST относительно FIN другого socket: lifecycle test теперь проверяет bounded convergence вместо недоказанного cross-socket ordering, production close не ослаблен. Strict mypy/Ruff/audit и extraction replay проходят; installed-artifact verifier дополнен UI tests.
- `examples/ui_menu_bot.py`, `docs/native-ui.md`, authored declaration schemas в contracts и source/protocol evidence `docs/reports/ui-schemas.json`. Example type-checked, отдельный запуск приложения не заявлен. E5-01 остаётся открытым: supported-subset Slack/Adaptive Cards input translation ещё отсутствует. E5-02/03 также открыты. Полный счёт остаётся 19/56.

## Последнее изменение: E5-02

- Native Bot.open_dialog, SlashCommand/CallbackQuery/DialogAction shortcuts и DialogHandle. Настоящий пользовательский trigger → runtime bot REST open → native OK → initial ACK; форма приходит actor по WS. Trigger/state metadata редактируется в API errors; WS Message не получает фиктивный open_dialog shortcut.
- DialogSubmission/Refresh/Lookup/Cancel/Action, HTTP/ASGI ingress и Dispatcher/FSM с local dialog_session_id. FormValues различает MISSING/null/empty/false, разбирает text-number, bool, arrays, schema-bound matrix, ISO date/aware datetime; file_ids остаётся подтверждённым глобальным списком без выдуманной per-field association. Lossless form snapshot сохраняет defaults, исключённые из wire dumps.
- Immediate single-response lifecycle: field/global errors, next/replace_form, lookup items, ACK и cancel. Нет auto-ACK незавершённого submit/lookup/refresh/action. Parser errors возвращаются до business handler. Native session state отделён от application_state.
- Opaque 256-bit state capability и scoped Memory/Redis records. Claim сохраняется до business dispatch; terminal result/step transitions сохраняются до ответа. Повтор terminal submission не запускает handler; timeout/cancel/error после claim блокирует replay до явного application reconciliation. Стек native forms ограничен тремя уровнями, одна живая child на parent, invalid parent отклоняется. Это не транзакция с business DB и не fencing.
- Настоящий Mattermost 11.1: open_dialog на actor WS; parser errors/business errors/corrected input; bool/array/date/offset; server-derived actor вместо forged client user_id; wizard с новым state и сохранёнными prior values; lookup/refresh с частичным вводом; cancel; timeout → indeterminate → explicit reconcile; tampered state rejected before handler. Private capture в ignored build/; три обезвреженных fixtures имеют provenance. UI rendering и newer server/client execution не заявлены.
- Реальный Redis: два независимых clients/managers разделяют record и исполняют terminal handler один раз; uncertain claim переживает смену worker и требует reconcile. Pool/manager ownership проверен. Optional base install по-прежнему не требует Redis; mandatory Redis verifier и installed consumers включают новые cases.
- Postman 1.82.0: actual SDK open write, native shape/mock state, 403/429/302 без retry/redirect и write-before-timeout → uncertain session, subsequent submit не dispatches. Прежние REST/response_url simulations проходят. Guest/owner discovery ограничения сохраняются; ничего не публиковалось.
- Full Windows matrix 3.11/3.13/3.14: 375 tests + 146 subtests с real Redis, skips нет. Primary UI/form references — 41 pinned files; strict audit/replay остаются ready. Source/protocol report: docs/reports/forms-core.json; guide/example: docs/native-forms.md, examples/form_bot.py. Standalone example type-checked, отдельный запуск файла не заявлен. Точный installed-artifact receipt — build/reports/distributions.json.

## Последнее изменение: E5-01 завершён

- from_slack_blocks/from_adaptive_cards и UIConversionError(path, reason): strict allowed fields/types/version gates, common mrkdwn и explicit renderer для workspace identities, native layout и action/data bindings. Нет silently dropped properties или inputs внутри поста.
- Подпись, registry reconciliation и capability gates остаются в общем native compiler. Exact embedded data, deep copies, missing/unused/duplicate IDs и bounded JSON проверены; явный null ID не принимается как отсутствие.
- Windows Python 3.11/3.13/3.14: 387 tests + 193 subtests с настоящим Redis, skips нет. Ruff lint/format, strict mypy (92 файла), 21 reviewed module/15 suites/replay/static audit и 3711 immutable files прошли. UI/form/input references: 45 pinned files.
- Wheel/sdist установлены вне checkout: по 130 consumer checks (55 unit + 58 integration + 1 Redis config + 16 real Redis). Все 62 source/py.typed files совпадают с wheel. Offline examples/ui_input_formats.py выполнен. Evidence: docs/reports/ui-input-core.json; контракт: docs/ui-input-formats.md.
- E5-03 подготовлен: отдельный pinned Mattermost 12.0.2, PostgreSQL и volumes, loopback endpoint; baseline 11.1/Redis healthy. Ping/version header и начальная страница web проверены, screenshot осмотрен. Это preparation; forms/Blocks ещё не проверены через UI. Evidence: docs/reports/ui12-preflight.json.

## Последнее изменение: E5-03, web 12.0.2 — частично

- Настоящий Chrome 157 на Windows, Mattermost 12.0.2 с pinned image, ordinary actor отдельно от setup admin и runtime bot. Все 13 типов полей и 9 типов Blocks прошли записанные сценарии; Slack/Adaptive imports отрисованы, signed button/select callbacks получены от реального сервера. Скриншоты и границы: `docs/reports/ui-web-12.0.2.json`.
- Исправлены четыре воспроизведённых UI несовместимости: стабильный opening callback ID при wizard/refresh; opening capability для lookup/cancel после перехода; native lookup option objects; сохранение совместимых введённых значений при refresh с приоритетом явных defaults. Stale writes, scope/TTL/actor checks и terminal guards сохранены. Snapshot хранит explicit-field metadata; regression tests покрывают повторный refresh.
- Пройдены field error/correction, date/datetime, wizard, dynamic lookup/refresh/submit/cancel, настоящий file upload с ordinary-actor byte read-back, child submit/cancel/parent restore и отказ четвёртого modal level. Поздний lookup закрытой формы и превышение глубины получили ожидаемые 409 до handler.
- Windows/Python 3.11/3.13/3.14: 389 tests + 199 subtests на каждой версии, с настоящим Redis, без skips. Повторный Mattermost 11.1 API/WS regression прошёл. Reviewed replay/static audit: 21 module, 15 suites; primary references: 47 files. Свежие package receipts указаны в web report.
- `tools.ui_stand` и `docs/ui-stand.md` воспроизводят TLS/setup/serve/stop. CA установлен только в отдельном UI container, проверка исходящего TLS включена. Browser и callback host закрыты; текущая slash command удалена. E5-03 остаётся открытой, общий счёт 21/56.

## Дополнение E5-03: web 12.0.2 bounds/timezone/labels/legacy

- Native calendar отключает даты вне 10–14 ноября, обе включённые границы успешно отправлены. Asia/Kolkata показывает GMT+5:30; manual_time_entry=False даёт 48 вариантов с шагом 30 минут, выбранные 19:00 разобраны как 13:30 UTC.
- Before/after проверены для Checkbox, Radio и CheckboxGroup с реальным вводом и submission. У Checkbox позиция относится к inline caption из placeholder, заголовок поля остаётся сверху.
- Legacy rich attachment отрисован; signed button/static-select callbacks получены, Beta остаётся отдельным selected_option. Disabled button блокирует обычный click. Все 13 callback requests двух дополнительных прогонов — 200, handler errors нет, hosts/browser закрыты и команды удалены.
- Evidence: `docs/reports/ui-web-12.0.2-additional.json`, четыре новых screenshot и три DOM excerpts. Runtime не менялся: все 63 source hashes совпадают с предыдущим проверенным wheel. Полный E5-03 и счёт 21/56 остаются открытыми.

## E5-03: web 11.11 и отдельные matrix stands

- `infra/ui-client-matrix.compose.yml`: pinned linux/amd64 Mattermost 11.7.0/11.11.0, отдельные PostgreSQL/data volumes и loopback ports. Оба healthy; native ping, issued bot identity и ordinary actor/setup admin fixtures проверены. `tools.ui_stand --profile` разделяет state, credentials, callback captures и reports. Готовность: `docs/reports/ui-matrix-preflight.json`.
- Настоящий web 11.11/Windows Chrome 157 прошёл basic field error/correction с сохранением ввода, multiselect, wizard с prior values, dynamic lookup/refresh/submit, все 9 Blocks и signed callbacks, Slack/Adaptive imports, legacy button/menu, date/manual datetime и cancel. Child submit восстановил parent text/file; 36-byte файл скачан обычным actor и совпал побайтно.
- 31 callback request — HTTP 200; одна ожидаемая field-error response, handler errors нет. Восемь screenshots и sanitized events: `docs/reports/ui-web-11.11.0.json`. Source runtime не менялся: все 63 hashes совпадают с ранее проверенным wheel. Ruff/format, strict mypy (94 файла), Compose config и hashes evidence проверены. Полная Python matrix повторно не запускалась для изменения development harness/docs.
- 11.11 browser/host закрыты, issued slash command удалена. 11.7 пока имеет setup/preflight, без UI acceptance. Native desktop/mobile не проверены. E5-03 открыт, общий счёт 21/56.

## E5-03: web 11.7 и исправление legacy route IDs

- Web 11.7/Windows Chrome 157 прошёл восемь применимых типов полей, field error/correction, multiselect, wizard/prior values, dynamic lookup/refresh/submit, date bounds с обеими границами, manual datetime, Asia/Kolkata/time menu и cancel. Семь screenshots и два DOM excerpts: `docs/reports/ui-web-11.7.0.json`.
- Реальный legacy button выявил native 404: маршруты 11.1/11.7 допускают только алфавитно-цифровые IDs, хотя post model сохраняет underscore. SDK теперь отклоняет несовместимый ID до HTTP-записи; `_`/`-` разрешаются при явном referenced 11.11/12 profile. ID не переименовывается, отсутствующий выдаёт Mattermost. Новый legacy declaration schema и две регрессии. Повтор с явно выбранными alphanumeric fixture IDs доставил реальные signed button/select callbacks.
- Windows Python 3.11/3.13/3.14: 391 tests + 225 subtests на каждой версии с real Redis, без skips. Native 11.1 HTTP/WS regression, Ruff/format, strict mypy (94), 21-module replay/audit, 15 suites и 47 primary refs прошли. Свежий installed-artifact receipt привязан к web 11.7 report.
- Все hosts/browser закрыты, issued commands удалены. Native Desktop/mobile не исполнялись: targeted registry/PATH/standard-path probes не нашли установленные clients/emulator; это не исключает custom portable installs. Evidence: `docs/reports/ui-native-availability.json`. E5-03 остаётся открытым, 21/56.

## E6-01: slash metadata и command manifest — частично

- `description`, `hint`, явные `triggers` и `provision=False` доступны у slash observer. Существующий алгоритм регистрации filters/handlers сохранён; metadata immutable, входные mutable lists копируются, reserved flag нельзя подменить из filters/decorator.
- `CommandSpec`, `CommandManifest`, `collect_commands`: только attached slash handlers, literal aliases, детерминированный fingerprint, duplicate merge/conflict checks, native payload с UTF-8 limits и P/G methods без выдуманных IDs/tokens. Regex/arbitrary/root/magic filters требуют явного решения приложения. Posted-message commands не регистрируются.
- Настоящий Mattermost 12.0.2: три native команды из manifest, metadata round-trip, три разных issued token, три SDK callbacks от ordinary actor и три runtime-bot posts с ordinary-actor read-back. Setup admin и runtime bot разделены. Команды удалены, listener/SDK resources закрыты. Harness — development fixture, не production Bootstrap/store.
- Windows Python 3.11/3.13/3.14: 405 tests + 253 subtests с real Redis на каждой, без skips. 14 новых unit cases/28 subtests; native 11.1 HTTP/WS regression, Ruff/format, strict mypy (102 файла), exact replay/audit (21 module/15 suites) и extraction прошли. Primary references: 47 UI + 2 provisioning; общий immutable verifier переиспользован, CI fetch/verify добавлен, actionlint прошёл. Внешний CI не запускался.
- Guide/example: `docs/provisioning-commands.md`, `examples/command_manifest.py` (offline execution прошёл). Evidence: `docs/reports/provisioning-commands.json`. E6-01/02/03 остаются открытыми, счёт 21/56.
- Финальные wheel/sdist из `build/final-dist/e6-command-manifest-final` прошли strict twine и установку вне checkout: по 148 checks (71 unit + 60 integration + 1 Redis config + 16 real Redis). Все 67 source/py.typed files побайтно совпали с wheel. Receipts заморожены в `build/reports/command-manifest-distributions.json` и `command-manifest-package-integrity.json`; hashes добавлены в maintained report после закрытия архивов. Публикации нет.

## E6-01: отдельный SetupClient — частично

- HTTP/session алгоритм механически выделен из Bot в общий internal client. Runtime Bot и SetupClient — разные классы/экземпляры с отдельными credentials/pools; настройки no retry/redirect/cookies/trust_env и lifecycle сохранены. Post-send transport failure для PUT/PATCH/DELETE теперь также RequestOutcomeUnknown.
- Явные native bot/PAT/team/channel/membership/command create/read/update/list/delete/revoke операции; native PAT issuance возвращает 200. Strict response snapshots, SecretStr исключён из repr/dumps, raw/unknown setup payload не сохраняется. Built-in command definitions без persisted ID поддержаны; malformed success и неверная credential/resource identity после записи оставляют результат indeterminate. Автоматического ownership/reconciliation пока нет.
- Настоящий Mattermost 12.0.2: новый бот и runtime PAT, новая team/private channel, четыре membership round-trips, command create/read/update/list и сохранение issued token при update. Native callback → SDK → runtime-token post → ordinary-actor read-back. Ordinary setup actor входит без требования admin role, но create_bot получает 403; отозванный runtime token получает 401 без fallback. Команда удалена, PAT отозван, owned channel/team архивированы, fixture bot отключён, SDK/listener закрыты.
- Local Postman simulation: реальный SetupClient выполнил CRUD и list; POST/PUT/DELETE committed-before-timeout → RequestOutcomeUnknown, одна попытка, состояние отдельно прочитано. 403/429/302 без write/retry/redirect. CLI log подтвердил 13 command calls; прежние REST/response_url/form simulation прошли. Guest owner search недоступен, использованы pinned source facts; cloud действий нет. Simulation process/listener остановлены.
- Windows Python 3.11/3.13/3.14: по 417 tests + 267 subtests с real Redis, без skips. 12 новых local-HTTP cases/14 subtests. Native 11.1 REST/WS и HTTP/forms regression, Ruff/format (141), strict mypy (107), replay/audit/extraction (21 modules/15 suites), 47 UI + 8 provisioning primary refs прошли. Документы: `docs/setup-client.md`, `docs/reports/setup-client.json`. E6 остаётся открытым; счёт 21/56.
- Wheel/sdist в `build/final-dist/e6-setup-client` прошли strict twine и по 160 installed checks вне checkout (71 unit + 72 integration + 1 Redis config + 16 real Redis). Все 70 source/py.typed files побайтно совпали с wheel. Receipts: `build/reports/setup-client-distributions.json`, `setup-client-package-integrity.json`; maintained report дополнен после закрытия архивов. CLI stderr содержит dataset-daemon exit/restart diagnostic; dataset operations не проверялись, фактические HTTP/state checks прошли, PID/listener simulation закрыты. Публикации нет.

## E6-01: encrypted persistent store и journal — частично

- `SQLiteProvisioningStore`, `FernetCodec`, `ProvisioningScope`, `ResourceKey`: атомарные encrypted binding/secret/completion, durable intent до native write, explicit uncertain/reconciliation/confirmed-absence transitions. Owned/reused различаются; reused не даёт право update, native ID update не подменяется, secret сохраняется между updates. Все payloads authenticated с record/scope/key context; snapshots не содержат secret values. Default crypto — optional `mattergram[provisioning]` с cryptography, ключ внешний и не создаётся внутри state_dir.
- Новый leaf directory Windows создаётся атомарно с process-user/SYSTEM DACL; существующие directory/files проверяются native API. Broad ACE, links/reparse/hardlinks и foreign schema отклоняются без repair/reset. Локальный OS lock сериализует directory между processes/tasks, wait bounded/cancellable, crash освобождает lock и оставляет pending intent. POSIX backend реализован, но Windows evidence его не подтверждает; NFS/distributed/fencing не заявлены.
- 17 новых cases/3 subtests: encrypted restart/no plaintext, wrong key/tamper/context transplant, namespace/ownership, indeterminate recovery, missing secret error с native ID, encryption failure и failure между binding/completion с rollback, actual subprocess competition/kill, cancellation/task/closed ownership, ACL и hardlink rejection. Ошибка тестовых SQLite connection contexts исправлена явным close; production sessions всегда закрывались.
- Настоящий Mattermost 12.0.2: два native-issued secrets сохранены; второй изолированный Python process прочитал runtime token и подтвердил identity; reopen вернул actual IDs, повторные creates заблокированы. Persisted slash token доставил настоящий callback, runtime post прочитан ordinary actor. Только новые command/PAT удалены/отозваны; прежние stand credentials сохранены. Ephemeral test key не сохранялся. Evidence: `docs/reports/provisioning-store.json`; guide: `docs/provisioning-store.md`.
- Windows Python 3.11/3.13/3.14: по 434 tests + 270 subtests с настоящим Redis, без skips. Ruff/format (147), strict mypy (112), reviewed replay/audit/extraction (21 modules/15 suites) проходят. Dynamic imports заменены явными optional/platform imports после static rejection; guard не ослаблен. Счёт 21/56, E6-01/02/03 остаются открытыми.
- Ошибка rollback больше не скрывает native recovery ID: fault regression сначала воспроизвела потерю PersistenceError, затем прошла после исправления. Финальные wheel/sdist `build/final-dist/e6-provisioning-store-final` прошли strict twine и по 177 installed checks (71 unit + 72 integration + 1 Redis config + 16 real Redis + 17 provisioning). Base install не подтягивает cryptography/Redis. Постоянный distribution verifier сравнивает все 74 source/typing files с исходным и sdist-rebuilt wheel; настоящий stale wheel отклонён. Receipts: `build/reports/provisioning-store-distributions.json`, `provisioning-store-package-integrity.json`; maintained report дополнен после закрытия архивов. Публикации нет.

## E6-01–E6-03: automatic Bootstrap — завершено

- `Bootstrap`, `BotSpec`, `TeamSpec`, `ChannelSpec`, `MemberSpec`, `RuntimeResources`: explicit create/reuse policies, native ownership evidence, exact user identity, add-only membership, multi-team command sync и immutable secret-free report. Matching username/name/trigger не даёт ownership; чужие/built-in triggers, archive/privacy/scope drift и missing secrets завершаются явно.
- Setup actor permissions читаются из native roles. Проверяются feature flags и точные operation permissions; ограниченный team provisioner синхронизирует existing bot/command без global bot/PAT creation. Runtime Bot не получает setup client или System Admin fallback. `load_runtime()` использует только сохранённые runtime/command credentials и требует setup reconciliation при изменившемся manifest.
- Durable intent создаётся до каждой записи. Подтверждённые 4xx завершаются aborted и допускают исправленный retry; lost response/malformed success/identity drift сохраняют pending/indeterminate и блокируют автоматическую запись. Explicit recovery завершает тот же intent настоящим ID/secret либо подтверждает отсутствие. OS lock сериализовал два Bootstrap instance, а store subprocess test подтвердил межпроцессную блокировку/crash release.
- Настоящий Mattermost 12.0.2: первый запуск 18 created/2 reused, идентичный repeat 20 reused, изменение metadata 4 updated/18 reused; 2 teams, 2 private channels, 4 commands, IDs и bot/command tokens сохранены. Ordinary actor доставил 4 команды в SDK и прочитал 4 runtime posts. Только owned fixture resources очищены; callback registration не выдана за readiness.
- Postman local stand-in: 437 HTTP events подтверждают 403 без write, committed-before-timeout, recovery без дубля, foreign trigger, feature allowlist/read-back, least-privileged success/denial, runtime без setup credential и concurrent instances. Это SDK fault evidence, не утверждение о provider-side fault behavior.
- Windows Python 3.11.15/3.13.12/3.14.3: по 445 tests + 275 subtests с настоящим Redis, без skips. Ruff/format 155 файлов, strict mypy 119, 13 provisioning primary refs, reviewed replay/audit/extraction 21 modules/28 decisions/15 suites, native 11.1 REST/WS/HTTP/forms regressions прошли.
- Guide/example/evidence: `docs/bootstrap.md`, `examples/bootstrap.py`, `docs/reports/bootstrap.json`. Wheel/sdist `build/final-dist/e6-bootstrap-final` прошли strict twine и по 188 installed checks (71 unit + 72 integration + 1 Redis config + 16 real Redis + 28 provisioning); все 78 source/typing files совпали с original и sdist-built wheel. Hashes: wheel `a92d809119756b0933d354e363b9cf92307c8dadc9e547f2b7d579f1340bcd25`, sdist `55fabb216ef360347b5ad9a1afdda68816dde4abca88b3617b69f4de4392e317`. Публикации нет.

## E7-01: generated low-level NativeAPI — завершено

- `tools.generate_native_api` сверяет authored overlay с pinned Mattermost OpenAPI commit/hash и детерминированно создаёт immutable operation registry отдельно от ручных `NativeAPI`, `Bot` и `SetupClient`. CC BY-NC-SA documentation остаётся verification-only snapshot; generated runtime не содержит descriptions/schemas. CI получил drift gate.
- Source inventory: 493 endpoints, 491 unique operationId; upstream-коллизии `SearchFiles`/`GetSharedChannelRemotes` сохранены в отчёте. Reviewed scope — 17 users/status/preferences/teams/channels/posts/reactions operations. Exact path parameters percent-encoded, query/JSON structurally validated, statuses allowlisted, redirects/retries запрещены, неопределённые writes дают `RequestOutcomeUnknown`.
- Живой Mattermost 11.1.0 принял все 17 operations с ordinary bot token и cleanup. Stand выявил explicit overlay `SaveReaction: 200` рядом с documented 201 и `GetPreferences: null` вместо schema array; raw response не нормализуется выдуманным `[]`.
- Windows Python 3.11.15/3.13.12/3.14.3: по 454 tests + 285 subtests с настоящим Redis, без skips. Ruff/format 165 файлов, strict mypy 126, reviewed replay/audit/extraction 21 modules/28 decisions/15 suites прошли.
- Guide/example/evidence: `docs/native-api.md`, `examples/native_api.py`, `docs/reports/native-api.json`. Wheel/sdist `build/final-dist/e7-native-api-final` прошли strict twine; все 82 source/typing files совпали, оба install дали 74 unit + 75 integration + 1 Redis config + 16 real Redis + 28 provisioning tests. Hashes: wheel `47c09f209f2c18e6282af787f43bd0fa56ca098784d1f742ea0c06b4a3820cd9`, sdist `de98986bdb4880d70b2af6f146ad564bba431c1a80b537e63b5e17abf8817e10`. Публикации нет.

## E7-02: high-level native facade — завершено

- Добавлены typed `Bot.get_user`, `get_channel`, `create_direct_channel`, `edit_message_text`, `delete_message`, `send_document` и shortcuts `Message.edit_text/delete`; generated E7-01 registry не изменялся. Edit/delete до записи сверяют channel binding, подтверждённые writes проверяют возвращённый resource/status, неопределённый результат даёт `RequestOutcomeUnknown` без retry.
- `BufferedInputFile` и `FSInputFile` сохраняют reviewed chunked-source алгоритм aiogram, но не зависят от Telegram Bot/session, `aiofiles` или URL downloader. FS source открывает новый owned handle на чтение и закрывает его в `finally`; multipart upload потоковый. Provenance replay/audit: 22 modules, 30 решений, 15 behavioral suites, узкое исключение только для pinned `aiogram/types/input_file.py`.
- Настоящий Mattermost 11.1.0 принял все восемь facade methods/shortcuts ordinary bot token; 35 bytes переданы порциями по 7 и скачаны обратно byte-for-byte. Windows Python 3.11.15/3.13.12/3.14.3: по 462 tests + 301 subtests с real Redis, без skips; Ruff/format 170 файлов, strict mypy 129.
- Guide/example/evidence: `docs/high-level-api.md`, `examples/upload_document.py`, `docs/reports/high-level-api.json`. Wheel/sdist `build/final-dist/e7-high-level-final`: exact 83 source/typing files, strict twine; оба install прошли 78 unit + 78 integration + 1 Redis config + 16 real Redis + 28 provisioning. Публикации нет.

## E7-03: operationId coverage и errors — завершено

- `tools.generate_native_api_coverage` детерминированно классифицирует все 491 unique operationId/493 endpoints из pinned structural inventory: 17 generated low-level, 2 high-level-only, 470 unsupported-not-reviewed и две upstream-коллизии (`SearchFiles`, `GetSharedChannelRemotes`) по два endpoints. Unknown/unsupported ID отклоняется до HTTP session; arbitrary URL escape hatch отсутствует.
- Error matrix проверяет 401/403/429/прочие status, неожиданный 2xx/redirect, invalid JSON, credential redaction, safe disconnect и uncertain write. Тест обнаружил implicit idempotent retry в aiohttp 3.14.5; owned session теперь явно отключает его, regression подтверждает ровно одну фактическую попытку read/write.
- Настоящий Mattermost 11.1.0 после transport hardening повторно принял 17 low-level operations и 8 high-level methods с ordinary bot token. Windows Python 3.11.15/3.13.12/3.14.3: по 467 tests + 312 subtests с real Redis, без skips; Ruff/format 172 файлов, strict mypy 130; replay/audit 22 modules/30 решений/15 suites.
- Guide/evidence: `docs/native-api-coverage.md`, `docs/reports/native-api-coverage.json`, `docs/reports/native-api-coverage-summary.json`. Wheel/sdist `build/final-dist/e7-coverage-final`: exact 83 source/typing files, strict twine; оба install прошли 79 unit + 79 integration + 1 Redis config + 16 real Redis + 28 provisioning. Публикации нет.

## E8-01: отдельный inventory/manifest mattergram_dialog — завершено

- Закреплённый `aiogram_dialog 2.6.0`/Apache-2.0 (commit `2c2fca8eab9532fba9c13f31be7404ec872c8a88`) прошёл read-only verify и AST inventory: 250 Python files, 111 package modules, 45 tests, 76 runtime-registration candidates. Donor не импортировался.
- `manifests/dialog-plan.json`/`dialog.json`: 40 явных решений, 27 выбранных модулей (3 copy, 24 adapt) и 13 excludes. Выбраны context/stack/storage, Dialog/Window/manager protocols/orchestration, getters/conditions/items и text composition; Telegram keyboard/input/media/markup/background semantics исключены.
- Две независимые extraction дали одинаковый receipt. Maintained `src/` не изменён, Apache license staged. Audit намеренно BLOCKED на 24 pending adaptations и 179 сопутствующих findings; CI проверяет точный pending boundary и запрещает выдавать stage за runtime.
- Windows Python 3.11.15/3.13.12/3.14.3: по 469 tests + 312 subtests с real Redis, без skips; Ruff/format 174 файлов, strict mypy 131. Sdist содержит plan/manifest/evidence, wheel ещё не содержит `mattergram_dialog`; оба install прошли 79 unit + 79 integration + Redis/provisioning. Evidence: `docs/dialog-porting.md`, `docs/reports/dialog-inventory.json`.

## E8-02: Mattermost-native dialog runtime — завершено

- В wheel добавлен отдельный typed package `mattergram_dialog`: `Dialog`, `Window`, `FormWindow`, registry, transactional manager, bounded stack/context, nested result и `start/switch_to/next/back/done/update/submit`.
- `DialogStorage` использует существующие `BaseStorage`/`BaseEventIsolation` и строковые Mattermost scopes. Исключение откатывает session; Memory и настоящий Redis прошли одинаковые transitions, reload и 20 конкурентных updates без потерь.
- Post backend использует `Bot.send_message`/`edit_message_text`; form backend — существующий typed `Form` и event `open_dialog`. `TextInput`/`Checkbox`/`DateTimePicker`, `OpenFormActions`, signed action data и явные ошибки для неподдерживаемого modal/post update исключают silent fallback.
- Все 27 выбранных donor modules запечатаны в 27 patches; 17 native boundaries hash-pinned. Reproducible extraction имеет 0 pending adaptations, static audit — 0 findings. Receipt закрепляет 9 donor suites и native behavioral suite.
- Windows Python 3.11.15/3.13.12/3.14.3: по 477 tests + 312 subtests с real Redis, без skips; Ruff/format 219 файлов, strict mypy 175. Wheel/sdist содержат exact 127 source/typing files, strict twine и install обоих артефактов прошли 79 unit + 79 integration + 16 real Redis + 28 provisioning checks. Evidence: `docs/dialog-porting.md`, `build/reports/dialog-audit.json`, `build/reports/dialog-extraction.json`.

## E8-03: каталог → карточка → форма → подтверждение — завершено

- Добавлен `DialogRuntime`/`setup_dialogs`: один scoped manager связывает trusted slash, signed callback и managed form events; Router filters не поглощают чужие callbacks. Signed transition содержит intent/source state, поэтому устаревшая кнопка получает явный ephemeral ACK без повторного перехода.
- Локальный HTTP ingress проверяет полный сценарий, terminal replay без второго effect, `FormWindow → FormWindow` через native `DialogSubmission.next()`, rotated form state, cancel с удалением intent и передачу unrelated callback следующему handler.
- Живой owned Mattermost 11.1: issued slash создал catalog post; две реальные server-forwarded post actions создали card и открыли форму; `open_dialog` наблюдался на actor WS; submit создал confirmation post. Повтор submit вернул сохранённый terminal response, business effect выполнен один раз.
- Финальный context/value восстановлен независимым Redis-клиентом. Временные команда, три поста и Redis keys удалены. Evidence: `docs/reports/dialog-scenario.json`, `manifests/dialog-scenario-tests.json`; private IDs без credentials — только в ignored `build/reports/dialog-scenario-stand.json`.
- Windows Python 3.11.15/3.13.12/3.14.3: по 482 tests + 312 subtests с real Redis, без skips; Ruff/format 224 файлов, strict mypy 178. Dialog manifest: 27 adapted + 18 native boundaries, 0 audit findings.

## E9-01: документация и проверяемые примеры — начато

- Добавлен runnable `examples/showcase_bot.py`: один пост содержит две Markdown-таблицы с независимыми filter/page states, три action groups и 11 controls, экраны Overview/Activity/About и три native forms с TextInput/TextArea/Select/Radio/MultiSelect/Checkbox/DatePicker.
- Callback path одним HTTP response заменяет `message + props`; live path принимает событие по WebSocket и изменяет общий пост через `Bot.edit_message_text`/REST, сохраняя существующие props. Однопроцессный `DashboardStore`, production persistence/distributed isolation, coalescing и idempotency явно ограничены в `docs/showcase.md`.
- Owned Mattermost 11.1 прошёл временную `/showcase`, native order form, независимые orders pagination/incidents filtering и WS message → REST patch с сохранением трёх attachments. Временные command/posts удалены; evidence: `docs/reports/showcase.json`.
- Windows Python 3.11.15/3.13.12/3.14.3: по 486 tests + 312 subtests с real Redis, без skips; Ruff/format 227 файлов, strict mypy 180. На этом срезе E9-01 оставалась открытой на generated reference, остальные guides/examples и общий documentation drift gate. Счёт 30/56.

## E9-01: документация и проверяемые примеры — завершено

- `manifests/documentation.json` задаёт проверяемый scope 20 публичных модулей, 23 guides и всех 14 `examples/*.py`. `tools.generate_documentation` из текущих exports/signatures/docstrings/capability registry создаёт API reference, `public-api.json`, exceptions, capabilities, examples index и корневой `llms.txt`; сеть и изменяющие стенд операции при сборке не выполняются.
- Drift gate требует точного совпадения generated files, полного покрытия examples, корректного Python syntax/docstring/env metadata, существующих evidence/guides и локальных Markdown links. Сигнатуры канонизированы между Python 3.11/3.13/3.14 и не содержат process-specific addresses. CI quality job запускает `python -m tools.generate_documentation --check`; native stand дополнен showcase verifier и его отчётом.
- Исправлен рассинхронизированный `examples/dialog_wizard.py`: terminal values читаются из реального mapping, а не через несуществующий `values.text`. Generated index различает 7 live-, 3 contract-, 2 offline-, 1 real-Redis-verified и 1 declaration-only example, не выдавая syntax/import check за live verification.
- Добавлены task-oriented `getting-started.md`, `mattermost-model.md`, `deployment.md`, `troubleshooting.md` и `migration-aiogram.md`. Они закрывают установку/minimal run, native entity/transport boundaries, setup-vs-runtime production lifecycle, recovery и семантическую миграцию без глобальной подмены Telegram→Mattermost. Ограничения exactly-once, WebSocket durability, distributed Bootstrap lock, PyPI, OS/client matrix и provider evidence опубликованы рядом с действиями.
- Первый повторный Python 3.14 matrix run обнаружил test-only timing flake: 50 ms cleanup bound истёк после 105 ms Proactor scheduling stall. Обычный test cleanup получил 500 ms, dedicated overdue-handler test сохраняет 50 ms и продолжает проверять `ShutdownTimeoutError`; production default 10 s не менялся. Проблемный сценарий прошёл 10/10 повторов, затем полный 3.14 run.
- Windows Python 3.11.15/3.13.12/3.14.3: по 489 tests + 312 subtests с real Redis, без skips; Ruff/format 229 файлов, strict mypy 181, actionlint 1.7.12 и documentation drift/registered-link tests прошли. Wheel/sdist прошли strict twine; sdist содержит 14/14 проверенных documentation inputs/outputs, wheel остаётся runtime-only. SHA-256: wheel `8cda502d08bfe8549082aa77b98c0b375c28ad2bdeabab349c19fba5afd1f4fc`, sdist `35cdae6b4b47690edbb4bbdacde444396f9ec0a5c2414df145aaf8c81d5cb37e`. Evidence: `docs/reports/documentation.json`, build: `build/final-dist/e9-documentation-final`. E9-01 закрыта; счёт 31/56.

## Декларативный UI и прогресс фоновых задач — контрактный срез реализован

- Добавлены декларативные `Button` и `Progress`. Callback кнопки может быть sync/async; одноразовый render token отклоняет повторное или устаревшее нажатие. `Progress` поддерживает determinate/indeterminate состояние и всегда выводит числовой/текстовый статус, а не полагается только на полосу.
- `DataBlock` теперь сохраняет read-only content без видимых действий. Это позволяет описывать статус, результат и terminal state одним деревом виджетов без фиктивных кнопок или imperative post payload.
- `DialogRuntime.start()` возвращает сериализуемый `DialogRef`; `DialogRuntime.update(ref, data=..., revision=N)` восстанавливает scoped manager, атомарно отклоняет stale revision и редактирует тот же пост с новыми signed actions. `CoalescingDialogUpdater` ограничивает частоту process-local REST updates, заменяет pending snapshot более новой revision и немедленно flush-ит terminal состояние.
- Динамические defaults typed forms принимают `Format`, `F[...]`, sync/async getter и заново проходят native Pydantic validation после разрешения данных. Demo-only row action table заменена публичными `ActionTable`, `ActionColumn` и `RowAction`; Blocks capability остаётся обязательной и скрытого legacy fallback нет.
- Runtime action dispatch больше не содержит веток по типам Button/Pager/Filter/ActionTable: подписанный lowercase action делегируется `process_action(WidgetAction, event, manager)`, а `WidgetActionResult` сообщает stale token или следующий state. Интеграционный custom action подтверждает расширение без изменения runtime.
- `DialogRuntime.drain()` завершает deferred Blocks REST edits после ACK и возвращает delivery error вызывающему lifecycle-коду, поэтому shutdown больше не зависит от неотслеживаемых фоновых tasks.
- Контрактный срез: 39 focused tests passed, 3 real-Redis tests skipped без запрошенного Redis profile. Доступный полный набор без трёх snapshot-dependent файлов: 486 passed, 23 Redis skips, 303 subtests; Ruff и strict mypy для 192 source files прошли. Generated reference содержит 249 exports, 411 public members, 25 guides и 17 examples. Живой Mattermost для нового progress flow не запускался.
- Следующий конкретный шаг этого среза: выполнить живой progress/action-table сценарий на подтверждённых 11.1 и 11.11/12.x профилях; process-local coalescing не заменяет durable distributed job queue.

## Декларативная админка игровых аккаунтов — контрактный пример реализован

- Добавлен `examples/game_accounts_admin.py`: один administrator-only dialog управляет пользователями, push-маршрутами, игровыми аккаунтами, JSONL-импортом, проверками и обеими историями. Вход требует точного enabled admin Mattermost user ID; административного fallback нет, последнего администратора нельзя отключить или понизить.
- Dashboard имеет ряды `2/2/2`; карточка аккаунта доступна за 2 клика, check/edit — за 3, импорт — за 2, проверка импорта — за 3. Максимальная глубина — 2. Списки используют локальные `PagedTable + Filter + PagePager + PageItemSelect`; выбранный аккаунт имеет ряды `5/6/2`, а весь post — 16 actions.
- Пароль и значения cookie не входят в render data, repr или истории. Карточка показывает только наличие пароля, количество cookie и уникальные домены. Summary внешнего checker очищается от совпадений с паролем/cookie; результат конкурентной проверки не перезаписывает credentials, изменённые во время запроса.
- Реальные `AccountVerifier`, `ImportFileReader` и `PushDispatcher` являются обязательными адаптерами. `MemoryAdminRepository` явно ограничен тестами/локальным demo; production требует durable encrypted storage. Большие batch-проверки должны выполняться durable worker'ом с декларативным progress, а не inline callback.
- Документация и evidence: `docs/game-accounts-admin.md`, `docs/reports/game-accounts-admin.json`; documentation manifest теперь содержит 18 examples и 26 guides. Четыре focused tests проверяют import/check/push, доступ, last-admin invariant, stale checker result и фактические формы рядов/маскирование.
- Ruff format/check прошли; strict mypy — 194 source files; documentation drift-check — 249 exports, 19 examples, 26 guides. Доступный suite без трёх snapshot-dependent tool files: **490 passed, 23 Redis skips, 303 subtests passed**. Owned Mattermost 12.0.2 принял `/game-admin` с HTTP 200 и создал интерактивный `mm_blocks` post; точные ряды требуют подтверждённого Blocks-профиля, импорт файла — `forms.file`.
- Следующий конкретный шаг: подключить production repository/file/checker/push adapters и выполнить живой сценарий на целевой версии Mattermost; для массовой проверки добавить durable queue и обновление `Progress`.

## Локальный deploy game-admin — воспроизводимый запуск проверен

- Добавлен единый операторский сценарий `tools/deploy_game_accounts_admin_demo.ps1`: он поднимает pinned Mattermost 12.0.2, каждый раз определяет фактический случайный loopback-порт, безопасно заменяет только `/game-admin`, запускает callback-процесс и сохраняет PID/логи/JSON-отчёт в ignored `build/`.
- Готовность больше не определяется по запущенному процессу. Один прогон проверяет Docker health, Mattermost ping, admin login, ровно одну актуальную slash-команду, HTTP 200 от `/game-admin`, bot `mm_blocks` post и браузерный переход «главная → Аккаунты»; результат записывается в `build/reports/game-accounts-admin-deploy.json`.
- Добавлен `tools/verify_game_accounts_admin_browser.cjs` с чистым browser context и скриншотами `output/playwright/game-accounts-admin/home.png`/`accounts.png`. Финальный live-прогон 15 ноября 2026 прошёл все gates на `http://127.0.0.1:45400/mattergram-ui/channels/ui-scenarios`; callback оставлен работающим на host port 18774.
- Создан и проверен Codex skill `mattergram-local-deploy` в `C:\Users\user\.codex\skills`: будущая сессия запускает тот же единый сценарий и не сообщает URL/credentials до полного smoke-test. `quick_validate.py` прошёл через isolated `uv --with pyyaml`.
- Повторно прошли 4 focused pytest, Ruff и strict mypy для двух example-модулей. Следующий конкретный шаг: подключить production repository/file/checker/push adapters; локальный demo остаётся in-memory и не является production deployment.
- Повторная ручная проверка выявила ложноположительный browser gate: старый сценарий находил экран «Аккаунты» в другом посте, а persisted SiteURL случайного Docker-порта оставался устаревшим и не давал клиенту получить live edit. Deploy теперь патчит SiteURL на фактический URL, привязывает browser assertions к post ID, созданному текущей командой, явно отвергает `Invalid action id`, удаляет smoke-post и только затем создаёт отдельный нетронутый пользовательский пост. Полный повторный прогон прошёл; итоговый permalink хранится в `build/reports/game-accounts-admin-deploy.json`.
- Исправлен lifecycle managed forms: `FormWindow` теперь всегда устанавливает `notify_on_cancel=true`, поэтому закрытие native modal через `×` возвращает runtime из form-state в исходное post-window и не делает оставшиеся кнопки устаревшими. Контрактный и HTTP integration tests проверяют флаг/cancel transition; live Chrome 12.0.2 прошёл точный сценарий «Пользователи → Добавить пользователя → × → Назад» на одном post ID. Deploy gate требует `browser_form_cancel=true`.

## Mattergram Menu plugin 0.1.0 — локальный вертикальный срез

- Добавлен отдельный dependency-free webapp plugin `plugins/mattergram-menu` для Mattermost 11.9+: официальный `registerChannelComposerBannerComponent` держит меню над composer, семь кнопок образуют раскладку `2 + 2 + 1 + 2` и создают ordinary user posts `/campaigns`, `/mailing_new`, `/pools`, `/proxy_groups`, `/test_email`, `/nodes`, `/sites` через actor session REST API.
- Меню ограничено DM/GM и public/private каналами с `[mattergram-menu]` в header/purpose. Запросы не содержат bot/admin credential, не повторяют uncertain POST автоматически, блокируют double-click на время write и показывают явную ошибку.
- Добавлены Node unit tests, dependency-free reproducible Python bundle builder, README и фазовый PLAN. Прошли 5/5 Node tests, `node --check`, Python compile, повторная byte-for-byte сборка и проверка состава tar; SHA-256 архива `3ccb708d1cb56a4d51b4585d8fbff1513f7962b6c10507e97dd51a2cadd5a87b`.
- Поднят отдельный Docker Compose stand Mattermost 12.0.2 на loopback-порту `29465` с отдельными контейнерами и volumes. Плагин установлен и включён; реальный Chrome загрузил bundle с HTTP 200 и отрисовал все семь кнопок в marked public channel. Нажатие «Кампании» создало ordinary user post `/campaigns` через `POST /api/v4/posts` с HTTP 201, сообщение появилось в канале, а меню осталось над composer. Evidence: `output/playwright/mattergram-menu-demo/result.json`, `menu-before-click.png`, `menu-after-click.png`.
- Следующий конкретный шаг плагина: проверить оставшиеся шесть кнопок, DM/GM/unmarked-channel и failure matrix, затем подключить тестовые `mattergram` handlers и подтвердить семь Dispatcher routes. Нативное мобильное приложение остаётся отдельной M4-задачей с Blocks fallback.

## Mattergram Menu plugin 0.2.0 — декларативная клавиатура и production packaging

- В публичный Python API добавлены строгие `ComposerButton`, `ComposerKeyboard`, `ComposerKeyboardState` и typed lifecycle `Bot.set/get/delete_composer_keyboard`. Layout задаётся декларативно: 1–6 rows, 1–4 buttons в row, стабильные уникальные IDs, `text`, отправляемый `message`, `default/primary/danger` и `disabled`; без установленного plugin нет silent fallback.
- `com.mattergram.menu` стал server+webapp plugin. Server endpoint хранит schema v1 по channel ID в plugin KV, проверяет membership, разрешает writes только bot accounts/system admins, использует compare-and-set revisions и публикует channel-scoped WebSocket update. Web/Desktop компонент перечитывает state без reload; click делает ровно один ordinary actor post без bot/admin credential и без retry неоднозначного write.
- Universal Linux bundle содержит `amd64`/`arm64`, server binaries имеют executable mode, MIT license и 54 license/notice files связанных Go-модулей; две последовательные сборки дали SHA-256 `0aa7ca9e77d7073a0d5afce5ea9adcf540cc2337026adc6a95821695f970c808`. Добавлены GPG signing/install/rollback инструкции и `Dockerfile.mattermost` для отдельного подписанного Mattermost image; private signing key намеренно не входит в репозиторий/image.
- Реальный Mattermost 12.0.2 принял финальный licensed archive и typed set/get read-back (revision 4). Chrome отрисовал объявленный layout, click создал один `/accounts` post, а revision 2 обновила открытую клавиатуру до `Accounts v2` без reload; ошибок самого plugin в console не было. Evidence: `docs/reports/composer-keyboards.json`, `output/playwright/mattergram-menu-0.2.0/keyboard-after-click.png`.
- Проверки: Python `501 passed, 23 skipped, 308 subtests`, Node `5 passed`, `go test ./server`, Ruff lint/format, strict mypy, documentation drift `255 exports / 19 examples / 27 guides`. Следующий конкретный шаг: staging/HA acceptance на точной production-версии Mattermost и отдельный mobile plugin либо Blocks fallback для native iOS/Android.

## Game-admin переведён на persistent composer keyboard

- Глобальная навигация `Обзор / Аккаунты / Импорт / Пользователи / Push-настройки / История импортов / История проверок` теперь объявляется одной функцией `build_admin_keyboard()` и устанавливается через `Bot.set_composer_keyboard` в direct channel `администратор ↔ bot`, а не в общий team channel. Домашний post больше не дублирует эти разделы; в нём осталось только локальное действие обновления сводки.
- `DialogRuntime.respond(Message, State)` реализует reply-keyboard семантику: exact-text handler принимает обычные сообщения `Обзор`, `Аккаунты` и другие без slash, сбрасывает активный actor/DM stack и на каждую принятую команду создаёт отдельный новый bot-post. Предыдущие bot-posts не редактируются; локальные фильтры, pager, формы и `Назад` остаются inline-действиями своего post. Native `/game-admin` сохранён только как team-channel entry.
- Game Admin handlers вынесены из setup-замыкания в модульный `game_admin_router`: `@game_admin_router.slash_command(...)` и `@game_admin_router.message(...)` объявлены глобально по модели aiogram, а immutable `GameAdminHandlerContext(runtime, service)` приходит через стандартный `Dispatcher.workflow_data` DI. `register_admin_panel(...)` теперь только связывает dependencies и включает router; прикладные callbacks аналогично могут объявляться через глобальный `@router.callback_query(...)`.
- Для импорта добавлен явный промежуточный post-window: ordinary keyboard post не имеет Mattermost `trigger_id`, поэтому native form открывается следующим интерактивным кликом без фиктивного fallback.
- Локальный deploy теперь включает uploads только на owned UI fixture, устанавливает/включает `com.mattergram.menu` 0.2.0 и проверяет keyboard как часть browser gate. Чистый Chrome в bot DM подтвердил пары `видимый actor text → отдельный новый bot post` для `Обзор → Аккаунты → Обзор → Пользователи`, неизменность предыдущего post и локальный сценарий `Add user → закрытие → Назад` без `Invalid action id`/`This dialog action is outdated`. Deploy отдельно подтвердил отсутствие keyboard в shared `UI scenarios`; PowerShell HTTP helper явно кодирует JSON как UTF-8. Итоговый отчёт `build/reports/game-accounts-admin-deploy.json` имеет `status: passed` и прямой DM URL.
- Проверки текущего изменения: полный suite `505 passed, 23 skipped, 308 subtests`, Ruff/format (245 файлов), strict mypy (195 файлов), documentation drift (`255 exports / 428 members / 19 examples / 27 guides`) и реальный deploy Mattermost 12.0.2 прошли. Следующий конкретный шаг: добавить Blocks fallback для native mobile clients либо отдельный mobile plugin; текущий webapp keyboard поддерживает Web/Desktop 11.9+.

## Game-admin: team-scoped управление пользователями

- `Bot.get_users_in_team` добавляет typed read одной страницы native
  `GET /api/v4/users?in_team=...&active=true`; проверяются параметры,
  JSON-array, строгие `User` и отсутствие duplicate IDs.
- Форма добавления пользователя больше не принимает ручные Mattermost ID и
  username. Uncached `TeamUserDirectory` собирает участников настроенной
  команды, исключает bot accounts и уже назначенных пользователей, а сервис
  повторно проверяет выбранный ID на submit и сохраняет username из Mattermost.
- Demo provisioning по-прежнему добавляет runtime bot в команду setup/admin
  credential'ом, после чего каталог явно проверяется обычным bot token. Native
  endpoint требует active session и membership; system-admin fallback не
  добавлялся. Team ID
  задаётся конфигурацией, потому что DM не несёт team scope.
- `mattergram_dialog.Select`/`MultiSelect` теперь принимают sync/async getter
  для `options`, поэтому небольшой актуальный список можно строить при каждом
  открытии native form без кэша и отдельного lookup endpoint.
- Проверки 16 ноября 2026: focused suite `42 passed, 3 skipped, 17 subtests`;
  доступный общий suite без пяти зависящих от закрытого upstream snapshot
  tool-файлов — `466 passed, 23 skipped, 296 subtests`. Ruff — без ошибок,
  strict mypy — `195 source files`, documentation drift — `255 exports / 429
  members / 19 examples / 27 guides`.
- Живой browser/deploy для этого изменения не запускался; live-поддержка нового
  read на Mattermost 11.1/12.x пока не объявляется проверенной. Обновление
  установленного skill заблокировано sandbox; готовый patch сохранён в
  `output/mattergram-dialog-ux-team-users.patch`.

## План plugin architecture и рефакторинга Game Admin

- Обновлён `docs/game-admin-routing-refactor.md`: зафиксированы два независимых
  уровня расширения, Mattermost server/webapp plugins и Python application
  plugins. Первый implementation slice ограничен минимальным `PluginManager`,
  независимым optional `ComposerKeyboardPlugin` и reusable `AdminUsersPlugin`
  для app-scoped users, roles и permissions.
- Исправлена граница keyboard: `AdminUsersPlugin` не знает о button IDs,
  composer API или наличии native plugin. Keyboard публикует ordinary user post,
  а feature router принимает exact text как обычный `Message`. Ручной ввод того
  же текста обязан работать без keyboard; авторизация всегда выполняется service.
- `AdminUsersPlugin` не получает system-admin fallback, не меняет Mattermost
  system/team roles, повторно проверяет team membership на submit и защищает
  последнего enabled administrator на service level. Notifications, settings,
  jobs, S3, AI и dynamic discovery явно отложены до появления реальных consumers.
- План сохраняет текущую границу global Router и dialog runtime, целевое
  разделение feature на router, getters, actions и декларацию окон, а также
  совместимую manager-only сигнатуру `Button` callback без `del event, button`.
- План отдельно сохраняет семантику `reply text -> новый bot-post` и `inline
  action -> update своего post`, описывает progress через существующие
  `DialogRef`/`CoalescingDialogUpdater`/revision guard и запрещает объявлять
  planned API уже реализованным.
- Проверки документации: generator и drift `--check` прошли с результатом `255
  exports / 19 examples / 28 guides`, documentation tests дали `3 passed`, Ruff
  для изменённого теста прошёл. Runtime и live stand не запускались, потому что
  это изменение фиксирует план и не меняет исполняемый код.
- Следующий конкретный шаг: сначала написать contract tests для plugin manifest,
  dependency order, lifecycle rollback и registration collisions, затем
  реализовать минимальный `PluginManager` без второго routing DSL. Первым
  functional plugin после core станет `AdminUsersPlugin`.

## Пока не выполнено

- Остальные typed WS normalizers и реальная E5-03 native UI client matrix.
- Полная матрица Python/OS/runtime dependencies; проверенные Windows 3.11/3.13/3.14 и Redis minimum/latest не закрывают Linux/macOS или всю CI-06 dependency matrix.
- Версионированный документационный сайт и LLM evaluations.
- GitHub repository/required checks, release workflow, environments и Trusted Publishing; публикации нет.

## Следующая конкретная задача

E9-02: определить фиксированный набор LLM evaluation задач поверх versioned `llms.txt`,
записать model/prompt/context/expected assertions и только фактически измеренные результаты.
Отдельно затем собрать версионированный docs site без изменения source-of-truth guides.
E5-03 native desktop/mobile остаётся отдельной открытой acceptance-задачей.
E5-03 native desktop/mobile остаётся открытым; отдельные дополнительные 11.11
date-bound/timezone/modal-depth combinations не проверены.
Записанный web 12.0.2 profile не подтверждает другие клиенты: `manifests/ui-client-profile.json`.
Backend forms и replay controls: `docs/native-forms.md`, `manifests/forms-profile.json`.
Native UI и границы: `docs/native-ui.md`, `manifests/ui-profile.json`.
Input formats: `docs/ui-input-formats.md`, `manifests/ui-input-profile.json`.
HTTP contract и ограничения baseline: `docs/native-http.md`, `manifests/http-profile.json`.
Storage contract и выбранная транзакционная стратегия: `docs/native-redis.md`.
Stand project: mattergram-native-stand; loopback only. Redis service запускается явно
через compose profile, credentials остаются в ignored build/. Не удалять чужие networks.

## Решения перед реализацией

- Реальные версии сервера и клиентов пользователя. 11.1.0 — выбранный протокольный baseline, а не подтверждение целевой установки.
- Schema/API documentation licensing: закреплённый api/README.md содержит CC BY-NC-SA 3.0. Выбран verification-only snapshot; distributed registry строится из authored structural overlay и не копирует descriptions/schemas. Пересматривать policy при расширении генерации моделей.
- Финальная доступность namespace перед публикацией; текущая проверка не резервирует имена.
- Финальные high-level signatures до их публикации.

## Источник истины

Дизайн задаёт ожидаемое поведение; реализация и тесты впоследствии подтверждают фактическую поддержку. Статусы planned, implemented и verified должны различаться.

## Mattergram Bot UX skill

- Канонический skill `mattergram-bot-ux` добавлен в репозиторий: `.codex/skills/mattergram-bot-ux`. Он предназначен для проектирования, реализации и ревью полного UX Mattermost-ботов на `mattergram` и `mattergram_dialog` и должен распространяться вместе с библиотекой через GitHub. Ошибочно созданная пользовательская копия в `C:\Users\user\.codex\skills` удалена после переноса.
- Skill разделён по progressive disclosure: корневой workflow, модель взаимодействия Mattermost, выбор компонентов, тексты/feedback/accessibility, архитектурные границы, verification и отдельный разбор источников. Для материальных многоэкранных функций добавлены шаблоны map, screen, flow и component в `assets/design/`.
- Из `aiobot-skill`, GramIO UX patterns, aiogram/aiogram-dialog и Telegram UI skill перенесены только применимые паттерны: task/screen map, button-first entry, Back/Home/Cancel, явные empty/error/stale states, состояние toggle в подписи, подтверждение опасных действий, ранний feedback для долгих задач и разделение UI/durable state.
- Telegram-специфичное поведение не перенесено как runtime-контракт: удаление пользовательских сообщений, callback ACK/byte limits, polling/privacy mode и message-by-message forms. Для Mattermost закреплено `global reply text -> видимый user post + новый bot post`, `local inline action -> update собственного post`, а composer keyboard остаётся необязательным producer обычного текста без зависимости feature-кода от plugin.
- Проверки 16 ноября 2026: repository copy прошла `quick_validate.py` в изолированном `uv` окружении с `pyyaml`; `.codex` не исключён в `.gitignore`; все относительные Markdown-ссылки разрешаются; TODO/TBD/FIXME не найдено; упомянутые Mattergram UI symbols сверены с текущим `src/`.
- Live Mattermost stand не запускался: задача меняет инструкции skill, а не исполняемый код или клиентский UI.
- Следующий конкретный шаг для skill: применить его к первому реальному изменению Game Admin, сохранить минимальный `.mattergram-design/` map/flow и по фактическому agent run уточнить слишком общие или недостающие правила.
- Локальный Git-репозиторий инициализирован на ветке `main`; исходники, документация, plugins и repository skill включены в initial commit. Кэши `.uv-cache`, `.npm-cache`, `node_modules` и локальные `.env` исключены, а `.gitattributes` фиксирует LF для текстовых файлов.

## Application plugin architecture — этапы 0–2 реализованы

- Добавлен `mattergram.plugins`: строгие `PluginManifest`, `MattergramPlugin`, `PluginBuilder`,
  immutable `PluginContext`, typed named-DI `ProviderKey` и `PluginManager`. Plugins перечисляются
  явно; новый routing DSL, entry-point discovery и hot reload не добавлялись.
- Setup сначала собирает все contributions без мутации приложения, проверяет уникальные plugin IDs,
  dependencies/cycles, required capabilities и конфликты routers, dialog states, slash commands и
  providers, затем подключает их к существующим `Dispatcher`/`DialogRegistry`. Startup идёт по
  dependency order, shutdown — в обратном; частичный startup откатывает уже завершённые plugins.
- Добавлен independent `ComposerKeyboardPlugin`. Он получает одну готовую `ComposerKeyboard`,
  проверяет target через native channel read как DM, проверяет доступность `com.mattergram.menu`
  через typed GET, делает один set на target и подтверждает read-back. При startup failure и normal
  shutdown восстанавливается предыдущее состояние. Feature handlers и button IDs ему неизвестны.
- Local Game Admin composition root больше не вызывает `set_composer_keyboard` напрямую: keyboard
  подключена через `PluginManager`; обычные exact-text handlers остаются в существующем Router.
- Добавлен публичный guide `docs/application-plugins.md`, новые modules включены в documentation
  manifest. Contract suite: 10 новых tests; полный доступный suite после синхронизации ранее
  изменённого `FormWindow` hash в dialog manifests — `517 passed, 23 skipped, 309 subtests`.
  Ruff/format для 255 файлов, strict mypy для 204 source files и documentation drift
  (`268 exports / 447 members / 19 examples / 29 guides`) прошли. Live Mattermost после замены
  composition root ещё не проверялся.
- Следующий конкретный шаг этого плана: реализовать `AdminUsersPlugin` с app-scoped roles,
  repository/directory protocols, service-level authorization и last-admin invariant, не добавляя
  зависимости от composer keyboard.

## AdminUsersPlugin — этап 3 реализован

- Добавлен `mattergram.contrib.admin_users`: явные `PermissionSpec`/`RoleSpec`, immutable
  `AdminUser`/`TeamUser`, async `AdminUserRepository`, `TeamUserDirectory`, `AuditSink` и
  process-local `MemoryAdminUserRepository` только для tests/local demo.
- `AdminUsersService` проверяет permission на каждом read/write use case, принимает только известные
  app roles, повторно читает active team members на submit, отвергает bot/outside-team IDs,
  реактивирует disabled user с прежним application ID и защищает последнего enabled member роли с
  `protects_last_member=True`. Initial admins задаются только точными Mattermost IDs и создаются
  идемпотентно; пустой repository не даёт administrative fallback.
- Reusable dialog разделён на getters, form actions и declaration. List использует `PagedTable`,
  filter `3`, pager+page selector `6`, выбранную detail card и локальное edit action; add/edit —
  native forms с cancel lifecycle. От entry-list добавление требует 2 действий, редактирование — 3,
  максимальная глубина — 1 form transition. Exact row geometry требует подтверждённый Blocks profile.
- Router владеет `/app-users` и exact text `Пользователи` только в bot DM. Text handler принимает
  обычный `Message` и создаёт новый post через `DialogRuntime.respond`; plugin не импортирует
  composer keyboard types и не получает button ID/callback.
- Game Admin local composition root подключает `AdminUsersPlugin` и временные adapters к прежней
  in-memory repository/directory. `Пользователи` удалён из старого keyboard message router, поэтому
  один visible text обрабатывается только новым plugin. `/app-users` получает отдельную scoped slash
  binding; старые imports и persisted `AdminPanel.users` сохранены на migration cycle.
- Шесть focused tests проверяют explicit/idempotent bootstrap, authorization, team revalidation,
  reactivation, last-admin invariant, current username, manual DM text без keyboard и фактические
  rows/forms. Полный suite: `523 passed, 23 skipped, 309 subtests`; Ruff/format для 267 файлов,
  strict mypy для 215 source files и documentation drift
  (`293 exports / 478 members / 19 examples / 29 guides`) прошли. Live Mattermost после этого этапа
  ещё не запускался.

## Button callbacks — этап 4 реализован

- `mattergram_dialog.Button` принимает две строгие формы: полную
  `(event, button, manager)` и короткую `(manager)`. Публичные typing aliases экспортируются из
  `mattergram_dialog`; compiled adapter остаётся внутренней деталью.
- Сигнатура анализируется один раз при создании widget. Обычные функции, bound methods,
  `functools.partial` и callable objects поддерживаются при однозначной итоговой сигнатуре;
  0, 2, 4 аргумента, variadic и keyword-only формы немедленно отклоняются.
- Sync и async callbacks поддерживаются одинаково. `TypeError` из прикладного callback не
  используется как сигнал для повторного вызова, а уже принятый action token остаётся одноразовым.
- `examples/dialog_progress.py` переведён на manager-only callback без фиктивных параметров.
  Layout, число кликов и capability requirements не изменились; live Mattermost для этого API
  среза не запускался.
- Проверки: полный suite `531 passed, 23 skipped, 309 subtests`; Ruff lint/format для 269 файлов,
  strict mypy для 215 source files, dialog extraction replay и documentation drift
  (`300 exports / 478 members / 19 examples / 29 guides`) прошли.
## Декларативная навигация Game Admin — этап 5 реализован

- Три статических edit-перехода больше не проходят через imperative callbacks:
  `adminUserEdit`, `adminPushEdit` и `adminAccountEdit` объявлены как `OpenForm` с прежними target
  states и условиями видимости. Остальные переходы уже используют `SwitchTo`/`OpenForm`;
  прямые keyboard-разделы сохраняют явный переход на главную, потому что новый post начинает
  dialog без предыдущего окна для path-aware `Back`.
- Четыре прикладных `Button` callback (`refresh_home`, проверки выбранного, всех и импортированных
  аккаунтов) используют manager-only форму. В `examples/game_accounts_admin.py` больше нет
  `del event, button`.
- UX не изменился: accounts edit остаётся двумя действиями (выбор строки и открытие формы),
  add/edit form находится на глубине 1, ряды accounts остаются `5/6/2` внутри data block и `3` в
  navigation row. Exact row geometry по-прежнему требует подтверждённый Blocks profile; legacy
  attachments сохраняют порядок и допускают client wrapping.
- Unit test фиксирует тип widget, прежние IDs/states, row shapes и masked credentials. HTTP
  integration test закрывает native form, затем успешно использует действие исходного post и
  отклоняет его повторный replay как outdated. Live Mattermost на этом этапе не запускался.
- Проверки: полный suite `531 passed, 23 skipped, 309 subtests`; Ruff lint/format для 269 файлов,
  strict mypy для 215 source files, E8-03 evidence receipt и documentation drift
  (`300 exports / 478 members / 19 examples / 29 guides`) прошли.
- Следующий конкретный шаг плана: этап 6 — вынести accounts/imports/checks из большого
  `build_admin_dialog`, сохранив совместимые re-exports и поведение service.

