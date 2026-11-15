# Persistent provisioning store и journal

`SQLiteProvisioningStore` сохраняет bindings, integration/runtime secrets и
статус шагов между запусками. SQLite transaction фиксирует binding, secret и
completion вместе. Store проверен на Windows с настоящими DACL, двумя процессами
и issued credentials Mattermost 12.0.2. Автоматический reconciliation поверх
store описан в [Bootstrap guide](bootstrap.md).

## Создать store с внешним ключом

Установите `mattergram[provisioning]`; из checkout — `uv sync --locked --extra provisioning`.
Базовая установка не требует cryptography. Подготовьте доверенный существующий
parent directory и постоянный volume. Ключ выдайте отдельно через secret manager
или защищённый mounted secret; он должен сохраняться между деплоями.

```python
import os
from pydantic import SecretBytes
from mattergram.provisioning import (
    FernetCodec, ProvisioningScope, ResourceKey, SQLiteProvisioningStore,
)

store = SQLiteProvisioningStore(
    "/data/mattergram",
    codec=FernetCodec(SecretBytes(os.environ["MM_STATE_KEY"].encode("ascii"))),
)
scope = ProvisioningScope(server=os.environ["MM_URL"], application="request-bot")

async with store.session(scope) as journal:
    key = ResourceKey(kind="command", team_id=resolved_team_id, name="request")
    binding = journal.binding(key)
    token = journal.secret(key)  # SecretStr | None; не логировать get_secret_value().
```

`FernetCodec` использует authenticated-encryption recipe библиотеки cryptography.
Ключ — URL-safe base64 encoding 32 случайных bytes, например результат
`Fernet.generate_key()`, сохранённый внешним механизмом один раз. Store не
генерирует и не сохраняет production key рядом с ciphertext. Потеря ключа
не восстанавливается из базы; неправильный ключ не переинициализирует state.
Fernet ciphertext раскрывает timestamp шифрования, но не значение секрета.
Ротация ключей и KMS integration пока не автоматизированы; `SecretCodec` задаёт
sync encrypt/decrypt interface для явного adapter.

## Intent до записи, completion после ответа

Пример ниже — явная операция с уже проверенными authority/ownership и отсутствием
конфликта. Store сам не запрашивает Mattermost и не доказывает владение по имени.

```python
async with store.session(scope) as journal:
    intent = journal.begin(key, "create", require_secret=True)
    created = await setup.create_command(spec, team_id=resolved_team_id, url=callback_url)
    journal.complete(intent, resource_id=created.id, secret=created.token)
```

`begin` фиксирует pending intent до native request. Если процесс завершится между
шагами, следующий запуск видит pending и получает `UnresolvedIntentError` при
попытке нового `begin` этого key. Истечение времени само по себе не разрешает retry.
`uncertain(intent, resource_id=...)` сохраняет известный native ID и indeterminate
статус. `complete` может завершить pending/indeterminate intent после явной сверки.
Записанный native ID нельзя подменить другим ID при таком завершении.

Если подтверждённого native результата нет, caller должен сверить provider state.
Только явный `confirm_absent(intent)` отмечает create/reuse intent как aborted и
разрешает новую попытку. Этот метод не выполняет проверку за caller. 403, скрытый
private resource или 404, скрывающий недостаток прав, не являются доказательством
отсутствия. Для исчезнувшего update target требуется отдельное recovery decision.

При ошибке сохранения `complete` поднимает `PersistenceError` с native
`resource_id` и указанием reconciliation. Значение секрета в ошибку не включается.
Ошибка самого rollback не скрывает этот recovery ID.
Не начинайте следующую native операцию: сохраните доступный secret через разрешённый
recovery mechanism либо явно отзовите/восстановите ресурс. Journal остаётся pending,
а partial binding/completion откатываются. Исход ошибки commit также требует
повторного чтения state, поскольку отсутствие ответа не доказывает отсутствие commit.

## Namespace, ownership и secrets

Scope включает настроенный server root с deployment prefix и application identity.
Как у текущего Bot.instance_id, configured authority сохраняется: aliases/default-port
написания одного сервера автоматически не объединяются. Resource key включает kind,
name и optional настоящий team ID. Scope/key digest — локальный индекс, а не native ID.
Intent ID также является локальным correlation ID.

`create` создаёт owned binding; `reuse` — reused binding. Повторные create/reuse
существующего binding отклоняются. `update` разрешён только для owned binding и
сохраняет прежний native ID. Переданное `secret=None` при update сохраняет
имеющийся secret; `require_secret=True` не позволяет завершить шаг без него.
Это bookkeeping доказанного результата caller, не автоматическое присвоение ресурса.

Bindings и intents — frozen snapshots без secret value. `secret(key)` — отдельное
явное чтение SecretStr. Все persisted payloads, включая ownership/journal metadata,
зашифрованы; envelope связывает ciphertext с типом записи, scope и resource/intent ID.
Подмена ciphertext другого key отклоняется. SQLite индексы содержат только digests
и локальные intent IDs. Reports не содержат secrets. Это не защита от кода,
выполняющегося с теми же credentials/key, и не детектор восстановления старого backup.

## Permissions и несколько процессов

На Windows новый leaf directory создаётся сразу с DACL для process user и SYSTEM;
файлы наследуют его. Существующие directory/files проверяются через Win32 ACL API.
Другие principals, reparse points, links и broadly-readable files отклоняются.
ACL существующего каталога SDK автоматически не ослабляет и не исправляет.
На POSIX backend требует своего owner, directory mode 0700/file mode 0600 без
group/other bits. POSIX путь реализован, но этим Windows отчётом не проверен.

State directory должен находиться под доверенным parent. SQLite file и lock должны
быть single-link regular files. Network/UNC path не поддерживается; POSIX filesystem
также должен быть локальным. Не удаляйте и не заменяйте lock/state во время работы.

`async with store.session(scope, timeout=30)` держит OS file lock всего state_dir.
Другие cooperative процессы/tasks ждут ограниченное время; cancellation закрывает
ожидающий descriptor. Process death освобождает OS lock, но не удаляет durable intent.
Session принадлежит одной asyncio Task; использование после выхода запрещено.
Не форкайте процесс с открытой session: inherited descriptors могут удерживать lock.

SQLite operations внутри session синхронны, с timeout=0 и `synchronous=FULL`;
транзакция не удерживается через HTTP await. Disk I/O может блокировать event loop.
Это local advisory lock, не distributed leader lease или fencing. Durability зависит
также от filesystem/volume/hardware; состояние и ключ надо резервировать согласованно.
Journal не очищается автоматически и не реализует миграцию схемы/prune policy.

## Воспроизвести проверки

```sh
uv run --locked pytest tests/provisioning
# Существующий owned Mattermost/TLS/fixtures: docs/ui-stand.md
uv run --locked python -m tools.verify_store_stand
```

17 cases проверяют restart, wrong key, tampering/context transplant, ownership,
uncertain recovery, rollback между binding и completion, task/cancellation,
реальную конкуренцию процессов и kill, native permissions и hardlink rejection.
Live harness сохраняет реальные PAT/command tokens, проверяет native identity
во втором процессе и обслуживает callback/runtime post после reopen.
Новые command/PAT удаляются/отзываются; существующие stand credentials не меняются.
Его ephemeral test key не сохраняется, поэтому оставшийся test store не является
production backup. Результаты/hashes: [provisioning-store.json](reports/provisioning-store.json).
