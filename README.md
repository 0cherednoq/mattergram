# mattergram

Проект форка ядра aiogram 3 для ботов Mattermost и отдельного расширения mattergram_dialog.

**Состояние на 15 ноября 2026:** выполнены E0–E4, E5-01, E5-02, E6, E7, E8 и E9-01, 31 из 56 задач. Bot, Router, Dispatcher, F, middleware/DI, Memory/optional Redis FSM, slash/post actions, backend native forms, automatic provisioning, generated low-level API, typed high-level facade и `mattergram_dialog` runtime работают. Команды, callbacks, формы, provisioning, 17 low-level operations и high-level edit/delete/direct/file upload проверены на настоящих Mattermost 11.1.0/12.0.2; FSM и dialog session replay — с Redis. Все 491 unique operationId классифицированы. Живой Mattermost 11.1 прошёл сценарий одного редактируемого поста: две ветки через общее окно и path-aware Back, фильтр, пагинация, actor WebSocket form, same-post confirmation и независимое Redis-чтение. Реальная native UI client matrix и публикация остаются впереди.

На web Mattermost 12.0.2/Chrome 157 проверены записанные сценарии всех 13 типов полей и 9 типов Blocks, wizard, lookup/refresh, файл и вложенные формы. [Отчёт со screenshots](docs/reports/ui-web-12.0.2.json), [воспроизведение стенда](docs/ui-stand.md). Остальная клиентская матрица E5-03 открыта.

Отдельно прошли [дополнительные сценарии 12.0.2](docs/reports/ui-web-12.0.2-additional.json) и [web 11.11](docs/reports/ui-web-11.11.0.json): формы, wizard, lookup/refresh, файл/child form, Blocks, imports и legacy menu. Подтверждённые границы перечислены в [client profile](manifests/ui-client-profile.json).

[Web 11.7](docs/reports/ui-web-11.7.0.json) проверен отдельно; найден и исправлен несовместимый legacy action ID, вызывавший native 404. Полная Windows matrix: 391 tests + 225 subtests на Python 3.11/3.13/3.14 с real Redis. E5-03 native desktop/mobile остаётся открытым.

[Automatic Bootstrap](docs/bootstrap.md) собирает [slash manifest](docs/provisioning-commands.md),
использует отдельный [SetupClient](docs/setup-client.md) и [encrypted store/journal](docs/provisioning-store.md),
сверяет bot/team/channel/membership/command resources и передаёт runtime обычный
неадминистративный Bot. Живой Mattermost 12.0.2 прошёл repeat/update по двум teams,
четырём commands, callbacks и posts без дублей или ротации secrets. Runtime-only
загрузка работает без setup credential; ambiguous writes требуют явного recovery.
Windows matrix — 445 tests + 275 subtests на Python 3.11/3.13/3.14 с real Redis,
без skips. Evidence: [bootstrap report](docs/reports/bootstrap.json).

[Low-level NativeAPI](docs/native-api.md) генерирует reviewed registry из authored overlay,
сверенного с закреплённой OpenAPI без копирования schema prose в MIT runtime. Все 17
текущих operations прошли на живом Mattermost 11.1.0 с ordinary bot token. Полная
матрица после E7-03 — 467 tests + 312 subtests на Python 3.11/3.13/3.14 с real Redis;
evidence: [native API report](docs/reports/native-api.json).

High-level facade, message shortcuts и потоковая multipart-загрузка описаны в
[high-level-api.md](docs/high-level-api.md); evidence: [high-level API report](docs/reports/high-level-api.json).

Исчерпывающая классификация 491 unique operationId и error contract:
[native-api-coverage.md](docs/native-api-coverage.md); evidence:
[coverage summary](docs/reports/native-api-coverage-summary.json).

Runtime, публичный API и воспроизводимая граница переноса `aiogram_dialog`:
[dialog-porting.md](docs/dialog-porting.md).

Навигация внутри одного редактируемого поста, path-aware `Back` и несколько независимых таблиц: [dialog-navigation.md](docs/dialog-navigation.md).

Декларативные кнопки, action-table, динамические defaults форм и обновление прогресса фоновой задачи в том же посте: [dialog-ui-design.md](docs/dialog-ui-design.md), пример — [dialog_progress.py](examples/dialog_progress.py).

Постоянные Telegram-подобные клавиатуры над composer для Web/Desktop через server-backed plugin:
[composer-keyboards.md](docs/composer-keyboards.md).

Явная сборка переиспользуемых Python application plugins, dependency order, lifecycle rollback и
optional keyboard adapter: [application-plugins.md](docs/application-plugins.md).

Готовый декларативный каркас админки игровых аккаунтов — пользователи, push-маршруты, безопасный JSONL-импорт, карточки и проверки: [game-accounts-admin.md](docs/game-accounts-admin.md), пример — [game_accounts_admin.py](examples/game_accounts_admin.py).

Начать с установки и первого post: [getting-started.md](docs/getting-started.md).
Модель server/team/channel/post/thread описана в
[mattermost-model.md](docs/mattermost-model.md); production lifecycle — в
[deployment.md](docs/deployment.md), миграция с aiogram — в
[migration-aiogram.md](docs/migration-aiogram.md), recovery — в
[troubleshooting.md](docs/troubleshooting.md).

Полный прикладной пример одной многоэкранной карточки с двумя независимыми таблицами,
фильтрами, пагинацией, native forms и корректным WS → REST live-update:
[showcase.md](docs/showcase.md), исходник — [examples/showcase_bot.py](examples/showcase_bot.py).

Справочные артефакты генерируются из текущего кода и documentation manifest:
[public API](docs/reference/api.md), [machine-readable exports](docs/reference/public-api.json),
[examples index](docs/examples.md), [exceptions](docs/reference/exceptions.md),
[UI capabilities](docs/reference/capabilities.md) и [llms.txt](llms.txt).
Drift проверяется командой `uv run --locked python -m tools.generate_documentation --check`.

## Назначение

Сохранить узнаваемые Bot, Dispatcher, Router, F, middleware, DI и FSM. Дать полный документированный набор операций, меню и форм Mattermost. Автоматизировать настройку бота, команд, каналов и членства при деплое с необходимыми правами.

Telegram-only функциональность не переносится. Семантика Mattermost имеет приоритет перед внешним сходством.

## Карта документов

| Файл | Для чего читать |
| --- | --- |
| [docs/aiogram_mattermost_fork_spec.md](docs/aiogram_mattermost_fork_spec.md) | Основной дизайн v1.2, публичный API и ограничения платформы |
| [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) | Порядок переноса, скрипты, проверки и экономия токенов |
| [docs/DOCUMENTATION_PLAN.md](docs/DOCUMENTATION_PLAN.md) | Что переносить, генерировать и писать заново |
| [STATUS.md](STATUS.md) | Сделанное, ближайшая задача и незакрытые решения |
| [AGENTS.md](AGENTS.md) | Краткий маршрут работы для coding agent |

Low-level generated operations и их границы: [native-api.md](docs/native-api.md).
Частые typed reads/writes, edit/delete shortcuts и потоковая отправка файлов:
[high-level-api.md](docs/high-level-api.md).

Пример бота и границы routing/FSM: [native-routing.md](docs/native-routing.md), REST и stand: [native-client.md](docs/native-client.md), команды разработки: [CONTRIBUTING.md](CONTRIBUTING.md).

## Запуск реализованных инструментов

```sh
uv sync --locked
uv run --locked pytest
uv run --locked python -m tools.fetch_upstream
uv run --locked python -m tools.inventory_upstream --source aiogram
uv run --locked python -m tools.build_manifest --check
uv run --locked python -m tools.fetch_upstream --verify
```

Snapshots восстанавливаются по `upstream.lock.json` и не входят в wheel/sdist.
Полный inventory сохраняется в `build/inventory/aiogram.json`, краткий отчёт —
[docs/reports/upstream-inventory.json](docs/reports/upstream-inventory.json).
Инструменты переноса описаны в [docs/porting-tools.md](docs/porting-tools.md).
Redis setup, ownership и lease semantics: [native-redis.md](docs/native-redis.md).
Slash/post actions, доверие транспорту, deadlines, ASGI и capability gates:
[native-http.md](docs/native-http.md), доказательства — [http-core.json](docs/reports/http-core.json).
Следующий шаг — реальные UI клиенты и newer-server scenarios.

E5 уже в работе: native Form/Blocks declarations, legacy actions и общий UI compiler
подключены к `Bot.send_message`. Профили и неподтверждённые возможности:
[native-ui.md](docs/native-ui.md). Backend forms — [native-forms.md](docs/native-forms.md).
Контракт Slack/Adaptive input formats: [ui-input-formats.md](docs/ui-input-formats.md).
Проверка реальных UI клиентов остаётся задачей E5-03.

## Порядок работы

1. Прочитать STATUS и нужный раздел дизайна.
2. Зафиксировать upstream commits и список публичных контрактов.
3. Подготовить manifest выборочного переноса и автоматические преобразования.
4. Получить работающий небольшой сценарий и перенести проверки ядра.
5. Постепенно добавлять Mattermost transport, UI и provisioning.
6. Подключить mattergram_dialog после стабилизации базового API.

## Правило исходников

aiogram и aiogram_dialog используются как неизменяемые доноры. Выбранные модули переносятся скриптами; адаптационные изменения остаются небольшими и воспроизводимыми. Создавать маршрутизатор и FSM с нуля без необходимости не предполагается.

Новый платформенный код пишется отдельно. Массовая замена Telegram на Mattermost не является адаптацией протокола.

## Документация

Публичные сигнатуры, перечень экспортов и coverage генерируются из проверяемых источников. Руководства, семантические различия, deployment и ограничения описываются вручную. Примеры подключаются в документацию из проверяемых файлов.

Skills — завершающий этап, после стабильных API, примеров и измерения генерации кода моделью.

