# Инструменты переноса E1

Реализованы extraction, namespace codemod, replay проверенных семантических
патчей и строгий статический audit. Это инструменты разработки. Наличие staging
не означает готовность SDK, успешный port поведенческих тестов или проверку сервера.

## Выполнить перенос в staging

Из каталога mattergram:

```sh
uv sync --locked
uv run --locked python -m tools.fetch_upstream
uv run --locked python -m tools.inventory_upstream --source aiogram
uv run --locked python -m tools.build_manifest --check
uv run --locked python -m tools.extract_core
uv run --locked python -m tools.audit_port
```

`extract_core` создаёт `build/port-staging/core` из явного allowlist. Проверяются
commit и digest источника, каждый source hash, лицензии, excluded scopes, уникальность
targets и native boundaries. Изменённый snapshot или manifest останавливает запись.
Никакие транзитивные зависимости автоматически не копируются.

Сначала строится временное дерево, затем готовое дерево перемещается в новый
каталог staging. `src/mattergram` не является допустимым output. Повторный запуск
проверяет существующее дерево без перезаписи файлов. Ручные изменения в staging
останавливают повторный запуск; они сохраняются. Для нового набора inputs выбирается
новый каталог, например `--output build/port-staging/review-2`.

Результат содержит выбранные Python-файлы, лицензии донора, копию manifest,
`attribution.json` и `receipt.json`. Receipt фиксирует source/output hashes,
порядок codemods, происхождение related tests, применённые patches и pending
adaptations. В receipt нет timestamp, абсолютного пути рабочего каталога или
платформенных переводов строк: одинаковые inputs дают одинаковые bytes.

## Namespace codemod

`namespace_imports_v1` разбирает AST и меняет только import tokens:

- `from aiogram... import ...` → `from mattergram... import ...`;
- `import aiogram... as alias` → тот же импорт из mattergram с сохранённым alias;
- relative imports остаются relative;
- комментарии, строки, encoding и line endings сохраняются.

Unaliased `import aiogram...` отклоняется: простая замена сломала бы обращения к
старому binding. Переименование объектов и семантики не выдаётся за namespace rewrite.
Новый код проверяется на синтаксис. Диагностический CLI `tools.rewrite_imports`
пишет только новый файл внутри `build/port-staging`; существующий файл не заменяет.

## Семантические патчи

Для adapt-модуля используются маленькие reviewed JSON patches из
`manifests/patches/`. Ссылка в соответствующей записи `core-plan.json`:

```json
{
  "adaptation": {
    "status": "applied",
    "patches": [{"path": "manifests/patches/example.json", "sha256": "<SHA-256 файла>"}]
  }
}
```

Patch содержит `schema_version: 1`, `reason`, точные `input_sha256` и
`output_sha256`, а также `replacements`: `before`, `after`, `occurrences`.
Он применяется после namespace codemod. Fuzzy matching отсутствует: несовпадение
hash или числа вхождений останавливает extraction. Результат должен быть валидным
Python. Patch и его hash сохраняются в staging и receipt.

`status: applied` без pinned patch запрещён. Частично применённые patches могут
оставаться в статусе pending; audit продолжает блокировать модуль. Изменение action
copy через семантический patch требует явного перевода решения в adapt. Подтверждение
поведения выполняется отдельными тестами; флаг applied сам по себе их не заменяет.

## Строгий audit

`tools.audit_port` заново воспроизводит ожидаемые outputs из lock/manifest/patches
и сверяет полный состав и hashes staging, включая receipt и лицензии. Затем проверяет:

- forbidden imports, bindings и exports;
- missing local modules и импортируемые attributes, в том числе TYPE_CHECKING;
- undeclared external roots;
- dynamic/star imports, dynamic exports и code execution;
- незавершённые адаптации и unresolved решения по зависимостям.

Любая ошибка даёт exit code 1. Отчёт: `build/reports/port-audit.json`. Инструмент
не копирует результат в maintained src. Статический анализ не доказывает runtime
initialization order, произвольные reflection/alias mutations или серверное поведение.
Следующие этапы обязаны проверить типы и реальные сценарии.

На текущем выбранном core все 19 модулей совпадают с reviewed patches; composite
staging включает только hash-pinned maintained native files. Строгий audit ready
с нулём findings. `tools.promote_core --check` воспроизводит stage, требует ready
и сверяет maintained port. Без --check копируются только отсутствующие source
targets с exclusive create; существующие отличающиеся файлы не перезаписываются.
Native modules читаются для audit, никогда не продвигаются поверх maintained source.
Это scoped static gate; поведение и server integration проверяются отдельно.

`manifests/ported-tests.json` фиксирует 13 адаптированных behavioral suites, source
commit/license, hashes source/target и точные adaptation patches. Команда
`python -m tools.verify_behavioral_port` проверяет replay без исполнения donors.
Native fixtures находятся в `tests/ported/conftest.py`; upstream conftest не используется.
Optional Redis profile теперь включён; исключения Mongo/PyMongo и замены Telegram DTO/observer registry указаны
для каждого файла, без объявления полной платформенной совместимости.

## Повторяемость и CI

```sh
uv run --locked python -m tools.verify_extraction
uv run --locked pytest
uv run --locked python -m tools.fetch_upstream --verify
```

`verify_extraction` запускает extraction дважды в один каталог и ещё раз в
независимый каталог, сравнивает receipt/bytes и hashes maintained src до/после.
Каталоги этой проверки включают fingerprint manifest и версии extraction: новый
reviewed manifest не требует удаления предыдущих staging или ручных изменений.
Отдельно сохраняет строгий audit со статусом ready/BLOCKED. Exit code этой команды
относится только к инвариантам extraction; проверку готовности SDK выполняет
`audit_port`, где ошибки всегда дают ненулевой exit code.

В CI обязательны tests, repeatability, `promote_core --check` и
`verify_behavioral_port`. Ошибка strict audit останавливает promote gate. Внешний
GitHub CI пока не запускался; локальные результаты не подменяют внешний запуск.
