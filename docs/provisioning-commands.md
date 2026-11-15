# Декларации slash commands

`mattergram.provisioning.collect_commands` собирает намерение регистрации команд
из подключённых Router. Этот слой работает без сети. Отдельный [SetupClient](setup-client.md)
выполняет native operations, а [Bootstrap](bootstrap.md) добавляет ownership,
persistent secrets, reconciliation и runtime handoff.

## Объявить и собрать команды

```python
from mattergram import Dispatcher, Router
from mattergram.filters import Command
from mattergram.provisioning import collect_commands
from mattergram.types import SlashCommand

router = Router()

@router.slash_command(
    Command("request", "ticket"),
    description="Create a request",
    hint="[topic]",
)
async def request(event: SlashCommand) -> None:
    await event.answer("Request received")

dispatcher = Dispatcher()
dispatcher.include_router(router)
manifest = collect_commands(dispatcher)
# manifest.commands: две CommandSpec, отсортированные по trigger.
```

Полный offline example: [command_manifest.py](../examples/command_manifest.py).
Запуск: `uv run --locked python -m examples.command_manifest`.
Собирать manifest нужно после подключения всех Router. Он представляет снимок:
последующие регистрации требуют нового вызова `collect_commands`.

Автоматический экспорт разрешён для одного обычного `Command` с literal names
и `/` среди префиксов. Aliases дают отдельные native команды. Сборщик не исполняет
filters/handlers и не читает команды из `router.message`. Detached Router
не входят в результат.

При regex, magic, дополнительных filters или root filters у любого родителя
нужен явный `triggers=("request", "ticket")` в аргументах регистрации. Это
решение приложения о регистрации; runtime filters продолжают работать.
Для ручной регистрации команды или catch-all handler укажите `provision=False`.
Его нельзя сочетать с description/hint/triggers. Число таких handlers доступно
как `manifest.skipped_handlers`.

Одинаковые trigger и metadata объединяются. Разные description/hint одного
trigger вызывают ошибку. Имена и metadata не переименовываются автоматически.
Переданные списки triggers/command flags копируются; registration metadata и
CommandSpec immutable. Служебный flag `_slash_registration` зарезервирован.

## Подготовить native payload

`CommandSpec(trigger, description="", hint="", display_name="", method="POST",
auto_complete=True)` задаёт одну команду. `spec.to_payload(team_id=..., url=...)`
возвращает новый dict для native API, используя уже разрешённый реальный team ID.
Создания ресурса этот вызов не выполняет.

POST/GET сериализуются как `P`/`G`; description также заполняет
`auto_complete_desc`, hint — `auto_complete_hint`. Native IDs, creator и token
выдаёт Mattermost. В payload SDK их не придумывает. После реальной регистрации
issued token нужен отдельному `SlashBinding` для HTTP ingress.

По закреплённой native модели проверяются UTF-8 byte limits: trigger 1–128,
description ≤128, display_name ≤64, callback URL ≤1024. Leading slash, whitespace
и control characters trigger отклоняются; последние ограничения также согласуют
объявление с ingress SDK. Callback URL требует HTTP(S), host, корректный port,
отсутствие credentials/fragment/whitespace/control characters. URL с query допустим. Для hint
платформенный предел длины не выдуман.

`manifest.fingerprint` — SHA-256 канонических отсортированных деклараций.
Порядок Router/handlers, callable repr, URL, native IDs/tokens и skipped count
не входят в fingerprint. Это fingerprint команд, а не ключ ownership или
доказательство применённого серверного состояния.

## Проверки и границы

[Отчёт](reports/provisioning-commands.json) фиксирует реальные результаты и hashes.
`tools.verify_command_manifest_stand` использует существующие isolated fixtures
Mattermost 12.0.2 и HTTPS из [ui-stand.md](ui-stand.md). Запуск:

```sh
uv run --locked python -m tools.fetch_provisioning_references
uv run --locked python -m tools.fetch_provisioning_references --verify
uv run --locked pytest tests/unit/test_provisioning_commands.py
uv run --locked python -m tools.verify_command_manifest_stand
```

Development harness создаёт команды из manifest с setup identity, проверяет
metadata через GET, запускает их обычным actor и читает posts отдельного runtime
бота. Тестовые команды удаляются в cleanup. Harness не является production
provisioner; его ignored fixture receipt не заменяет защищённый secret store.
Callback ingress использует существующие scope/token/peer checks.

Первичные command model/API references и SHA-256 закреплены в
[provisioning-profile.json](../manifests/provisioning-profile.json).
Общий reference verifier сверяет immutable source bytes; provider source
остаётся в ignored build/, не исполняется и не переносится в runtime.

Manifest остаётся чистой декларацией. Store-backed Bootstrap обрабатывает repeat,
conflicts, recovery, concurrent workers и runtime bindings; правила и ограничения
описаны отдельно в [Bootstrap guide](bootstrap.md).
