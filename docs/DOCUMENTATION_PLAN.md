# Стратегия документации и сопутствующих материалов

**Дата:** 13 ноября 2026 года.  
**Статус:** E9-01 выполнена; generated reference, проверяемые примеры, authored guides и ограничения связаны единым manifest/drift gate. Публикация сайта и LLM evaluations относятся к следующим задачам.

Связанные документы: [дизайн](aiogram_mattermost_fork_spec.md), [реализация](IMPLEMENTATION_PLAN.md).

## 1. Решение

Использовать смешанную модель:

- Генерировать точный справочник из кода, schemas и capability registry.
- Адаптировать только подтверждённые общие концепции перенесённого ядра.
- Писать Mattermost guides, deployment и ограничения специально для проекта.
- Включать примеры из единственных проверяемых source files.
- Сохранять происхождение заимствованных материалов.

Ни полное копирование документации aiogram, ни генерация всего сайта нейросетью не дают надёжного справочника.

## 2. Что делать с каждым типом материала

| Материал | Решение | Причина |
| --- | --- | --- |
| Публичные методы, поля, imports | Генерировать из реального SDK | Соответствие установленной версии |
| Type signatures и defaults | Генерировать | Исключение расхождений |
| Router/middleware/DI/FSM guides | Адаптировать выборочно | Только сохранённое поведение |
| Telegram Bot API reference | Не переносить | Другой протокол и набор функций |
| Telegram screenshots/widgets | Не переносить автоматически | Другой UI и возможные права на assets |
| Mattermost forms/Blocks guide | Написать под свои schemas | Правильные surfaces и lifecycle |
| Migration aiogram → mattergram | Написать отдельно | Явные семантические различия |
| Native REST reference | Генерировать из закреплённой схемы | Coverage и воспроизводимость |
| Capability matrix | Генерировать из registry | Версии, клиенты, flags, права |
| Deployment/bootstrap | Написать по реальным сценариям | Provisioning и secrets отличаются |
| Examples | Адаптировать или написать, затем проверять | Один источник кода |
| CLI reference | Генерировать из реализованного CLI | Никаких несуществующих флагов |
| Changelog | Авторские release notes + проверяемый diff | Изменения поведения требуют объяснения |
| LICENSE/NOTICE | Сохранять по происхождению | Не заменяются generated prose |
| Skills | Позже, из стабильного контракта | Не компенсируют ошибки SDK |

## 3. Классификация исходной документации

Перед переносом провести inventory страниц:

- относится к перенесённому ядру;
- содержит Telegram-specific parts;
- зависит от ещё не реализованного метода;
- содержит внешний asset;
- пригодна после адаптации;
- полностью исключается.

Не выполнять глобальную замену Telegram → Mattermost. В тексте могут остаться неверные ID, ограничения callbacks, polling, HTML и правила permissions.

Для .rst возможна механическая конвертация в Markdown, но результат считается черновиком до проверки смысла, ссылок и примеров.

Скопированный текст проверяется на лицензию и атрибуцию конкретного источника. Не следует предполагать, что любые изображения и внешние материалы покрыты лицензией кода.

## 4. Источники истины

| Данные | Источник |
| --- | --- |
| Реализованные публичные символы | Код и exports SDK |
| Планируемые signatures | Отдельный design contract |
| REST endpoints | Закреплённая OpenAPI + documented overlays |
| Native UI types/options | Wire schemas + подтверждённые источники |
| Поддержка версии/клиента | Capability registry и результаты проверок |
| Поведение сложного сценария | Contract и integration tests |
| Код примера | Единственный examples/*.py файл |
| Лицензия и происхождение | Upstream lock и attribution registry |

Планируемый API не должен автоматически попадать в stable reference как реализованный. Поля planned/implemented/verified различаются.

## 5. Документационный стек

Для нового Markdown-проекта рекомендуется MkDocs и mkdocstrings-python. Reference извлекается из Python source/docstrings через Griffe; использовать статический сбор, когда он достаточен.

Выбор theme и versioning tool откладывается до первого SDK-среза. Первоначально важнее текст, imports и правильные examples.

Переносить весь Sphinx toolchain aiogram не требуется. Если конкретный API плохо извлекается выбранным инструментом, сначала проверить конфигурацию/overloads и только потом менять стек.

При генерации нельзя импортировать модуль, который запускает сеть или provisioning. Документационная сборка должна быть воспроизводимой без токенов.

## 6. Генерируемые материалы

Планируемые артефакты:

| Артефакт | Вход |
| --- | --- |
| API reference | Публичные классы, методы, docstrings |
| public-api.json | Exports, signatures, source links |
| capabilities.md | Capability registry |
| native-api-coverage.md | operationId и wrapper/test status |
| exceptions.md | Типизированные ошибки |
| cli.md | Реальные команды и help metadata |
| examples index | Example metadata и результаты проверок |
| llms.txt | Краткая карта versioned документации |
| llms-full.txt | Опциональная сборка выбранных verified страниц |

Машинные exports не редактируются вручную. Исправляется источник: код, docstring, registry или generator.

Нейросеть может помогать с описаниями, но не должна придумывать справочные поля из названия метода.

Текущее состояние: `manifests/documentation.json` является authored scope, а
`python -m tools.generate_documentation --check` проверяет воспроизводимость API reference,
`public-api.json`, capabilities, exceptions, examples index и `llms.txt`. Generator не использует
сеть и не запускает provisioning; integration/live статусы примеров берутся только из явно
указанных evidence. Публичного CLI entry point у пакета пока нет, поэтому `cli.md` не
генерируется; `llms-full.txt` остаётся опциональным и не подменяет короткий task-oriented index.

## 7. Авторские руководства

Нужны отдельные небольшие guides:

1. Модель Mattermost: server, team, channel, post, thread, user.
2. Установка и минимальный запуск.
3. Router, filters и middleware.
4. FSM и session isolation.
5. Slash-команды и автоматическая регистрация.
6. Message actions и CallbackData.
7. Forms, validation, lookup, refresh и wizard.
8. Даты, время, timezone и файлы.
9. DM, channel post и ephemeral.
10. Bootstrap, ChannelSpec/TeamSpec и повторные деплои.
11. REST/WS/HTTP lifecycle и отказоустойчивость.
12. Deployment с reverse proxy и persistent secret storage.
13. mattergram_dialog.
14. Ошибки и troubleshooting.
15. Миграция с aiogram.

Каждая страница отвечает на конкретную задачу и ссылается на reference. Подробные ограничения размещаются рядом с операцией.

## 8. Примеры без рассинхронизации

Пример хранится в examples как обычный Python-файл. Документация включает весь файл или именованные фрагменты из него.

Полный runnable example содержит imports, configuration, router registration и запуск. Короткий snippet явно маркируется как fragment.

Нельзя вручную поддерживать три независимые копии одного примера: в README, guide и llms-файле.

Каждый пример имеет metadata: scenario, minimum profile, required permissions, dependencies, проверяемые assertions и статус.

## 9. Проверки примеров

Проверки разделяются:

| Уровень | Что доказывает |
| --- | --- |
| Syntax | Корректность Python |
| Imports | Существование публичных символов |
| Types | Соответствие signatures |
| Contract | Ожидаемое SDK-поведение на fixtures |
| Integration | Работа с выбранным Mattermost стендом |
| Client/UI | Отображение и действия в проверенном клиенте |

Синтаксическая проверка не позволяет назвать пример работающим. UI на web не доказывает поддержку mobile.

Не запускать отправку сообщений, создание команд или provisioning при обычной сборке docs. Mutating integration checks выполняются на изолированном стенде.

## 10. Семантические отличия

Карта миграции должна объяснять, в частности:

- Строковые ID и aliases.
- Team и Channel как отдельные сущности.
- answer и reply в тредах.
- Literal command и настоящая slash-команда.
- HTTP response и срок жизни interaction.
- Callback data и технический action_id.
- Форма и обычный пост как разные surfaces.
- Ephemeral permissions и ограниченный lifecycle.
- Markdown и отсутствие Telegram HTML API.
- Версии клиента, flags и непроверенные возможности.
- Отсутствие Telegram-only widgets.
- Bootstrap credential и runtime token.

Для каждого различия нужен короткий корректный пример и ожидаемый результат, а не только пометка «не поддерживается».

## 11. Документация для нейросетей

Главный LLM entrypoint должен быть коротким: версия, imports, карта сценариев и ссылки на релевантные guides.

Для конкретной задачи модель получает небольшой набор страниц, а не весь llms-full. Отдельные сценарии: messages, slash/actions, forms, provisioning и dialog UI.

Запрещено скрывать breaking changes под знакомым именем. Публичные kwargs строгие, aliases статические, ошибки объясняют причину.

Полезен machine-readable public API index. Он строится из exports и может использоваться для проверки выдуманных imports/methods.

Skill создаётся после измерения задач с обычной документацией. Его вклад проверяется отдельно.

## 12. Версии и публикация

Каждый релиз связывает:

- SDK version;
- upstream commits;
- schema revision;
- compatibility/capability matrix;
- reference;
- examples results;
- release notes.

Latest/develop и stable не смешиваются. Непроверенная будущая возможность не выглядит доступной текущему пользователю.

Публикация сайта не является частью текущего задания. До публикации проверить сборку, ссылки и доступность версионных путей.

## 13. CI gates

После появления соответствующих инструментов:

- Сборка docs без необъяснённых warnings.
- Проверка внутренних ссылок.
- Проверка imports и types примеров.
- Drift check generated reference.
- Проверка exported API против documented API.
- Coverage новых operations и UI fields.
- Проверка Telegram-only names с явным allowlist для migration/legal страниц.
- Проверка лицензий заимствованных материалов.
- Проверка отсутствия секретов.

Внешние URL могут временно быть недоступны; сетевые link checks не должны подменять проверку правильности собственного API.

## 14. Прочие материалы upstream

| Файл/область | Решение |
| --- | --- |
| pyproject/dependency lock | Создать под свой пакет; зависимости переносить по необходимости |
| Tests | Перенести подходящие поведенческие проверки |
| Fixtures | Адаптировать к Mattermost, исключения учитывать |
| CI workflows | Использовать как reference, создать свои права и jobs |
| Release scripts | Перепроверить; не копировать чужую публикацию |
| Issue/PR templates | Коротко адаптировать под поддержку Mattermost |
| CONTRIBUTING | Описать собственный процесс extraction и проверок |
| SECURITY | Указать фактический канал отчётов перед публикацией |
| Branding/assets | Создать свои либо использовать с проверенными правами |
| LICENSE/NOTICE | Сохранить применимые upstream notices |
| Benchmarks | Переносить методику только при сопоставимом поведении |

## 15. Последовательность работ

1. Сейчас: сохранить стратегию и ссылки, не писать весь сайт до кода.
2. После первого SDK-среза: реальный quickstart и минимальный reference.
3. После каждого компонента: guide, пример и соответствующая проверка.
4. После native UI/provisioning: capability matrix и troubleshooting.
5. До релиза: migration, coverage, versioned docs и LLM evaluation.
6. После стабилизации: skill и сравнение результатов.

## 16. Критерий завершения

Из документации можно собрать поддерживаемый бот без просмотра внутренних исходников. Все imports и kwargs существуют в указанной версии. Непроверенные возможности видны. Generated reference не расходится с кодом, а примеры не размножены вручную.

## Источники

- [mkdocstrings-python](https://mkdocstrings.github.io/python/)
- [aiogram documentation](https://docs.aiogram.dev/)
- [aiogram source and license](https://github.com/aiogram/aiogram)
- [aiogram_dialog](https://github.com/Tishka17/aiogram_dialog)
- [Mattermost developer documentation](https://docs.mattermost.com/developers/integrate/getting-started)

