# Отдельный setup-клиент

`SetupClient` выполняет явные операции настройки через собственный credential и
HTTP pool. Он проверен на изолированном Mattermost 12.0.2. Runtime `Bot` использует
другой экземпляр и bot token. Store-backed ownership и автоматический
reconciliation реализованы отдельным [Bootstrap](bootstrap.md); этот документ
описывает низкоуровневые explicit setup operations.

## Прочитать существующие ресурсы

```python
import os
from mattergram.provisioning import SetupClient

async with SetupClient(
    base_url=os.environ["MM_URL"],
    token=os.environ["MM_SETUP_TOKEN"],
) as setup:
    team = await setup.get_team_by_name("engineering")
    commands = await setup.list_commands(team.id)
```

Async context проверяет identity через `users/me`, затем закрывает pool при
выходе, включая ошибки и cancellation. Закрытие терминально и идемпотентно;
экземпляр нельзя переносить между event loops. `setup.user_id` содержит
проверенный ID после `get_me` или входа в context. `instance_id` учитывает
deployment prefix.

Вход не требует роли System Admin: права проверяет Mattermost для каждой
операции. Нельзя считать успешный `get_me` доказательством права создавать
бота, читать private channel или управлять чужими командами. SDK возвращает
401/403/404 и не подставляет другой credential. В частности, native commands
API может скрывать недостаток доступа ответом 404; это не доказанное отсутствие
ресурса для автоматического создания.

## Доступные операции

| Ресурс | Методы |
| --- | --- |
| Identity/users | `get_me`, `get_user`, `get_user_by_username` |
| Bot account | `create_bot`, `get_bot` |
| Runtime credential | `issue_access_token`, `revoke_access_token` |
| Team | `create_team`, `get_team`, `get_team_by_name` |
| Channel | `create_channel`, `get_channel`, `get_channel_by_name` |
| Membership | `add_team_member`, `get_team_member`, `add_channel_member`, `get_channel_member` |
| Slash command | `create_command`, `get_command`, `update_command`, `list_commands`, `delete_command` |

Это операции, не `ensure`: caller должен явно решить ownership, reuse, создание,
удаление и порядок зависимостей. Совпадение username/trigger не доказывает
принадлежность. Методы не создают альтернативные имена после конфликта или 403.

Создание team принимает native `type="O"` или `"I"`, channel — `"O"` или `"P"`.
Membership добавляется отдельно; существующие участники не удаляются.
Path arguments проверяются как один сегмент; query/path delimiters и traversal
отклоняются до HTTP. `list_commands(team_id)` использует `custom_only=false`,
включая built-in definitions без persisted ID. Ответ списка — tuple snapshots.

Command create/update принимает [CommandSpec](provisioning-commands.md), team ID
и полный callback URL. Update передаёт native ID также в body; endpoint не
переносит команду в другую team. Create требует native-issued ID и token в
ответе. Существующий integration token сохранился при обновлении на проверенном
сервере. Не обещается, что любой последующий GET вернёт секрет.

`issue_access_token(user_id, description=...)` получает native ответ **200**.
Description обязателен, 1–255 UTF-8 bytes. Возвращённый `AccessToken` содержит
`id`, `user_id`, `is_active`, `description` и `token: SecretStr`. Значение token
доступно только через `token.get_secret_value()`; его надо сохранить надёжно
сразу после выдачи. SDK ещё не делает это автоматически.

Secret-bearing setup snapshots исключают token из repr и model dumps,
не сохраняют raw/unknown payload. Это не шифрование и не защита process memory.
Нельзя логировать явно извлечённый secret или содержимое application store.

## Ошибки и результат записи

Общий HTTP transport механически выделен из существующего Bot. Каждый экземпляр
по-прежнему владеет своим pool, использует `DummyCookieJar`, `trust_env=False`,
один timeout и явный Bearer. Redirect и автоматические retry отключены.

401/403/429 сохраняют типизированные errors; Retry-After доступен без повтора.
Provider prose/payload не попадает в сообщение исключения. Диагностические
error/request IDs редактируют известный credential. Connect failure до отправки
даёт `TransportError`. Потеря соединения/timeout после отправки POST/PUT/PATCH/DELETE
даёт `RequestOutcomeUnknown`. Успешный, но невалидный setup response тоже оставляет
результат записи неопределённым: сервер уже мог создать ресурс или выдать secret.

Перед повтором нужна сверка фактического состояния. Cancellation распространяет
`CancelledError` и закрывает context; она также не доказывает отсутствие записи.
Definite HTTP error описывает ответ сервера, а не транзакционную гарантию отката.
Этот клиент не реализует journal, recovery или multiprocess lock.

## Проверки

[Отчёт с hashes](reports/setup-client.json) разделяет live и simulated evidence.
Native operation contract и восемь pinned primary files:
[provisioning-profile.json](../manifests/provisioning-profile.json).

```sh
uv run --locked pytest tests/integration/test_setup_http.py
# Требует существующий owned stand/TLS/fixtures из ui-stand.md:
uv run --locked python -m tools.verify_setup_stand
# Fault injection, другой терминал:
postman simulation run postman/simulations/mattergram-native.sim.yaml --port 4010 --no-history --output ndjson
uv run --locked python -m tools.verify_setup_simulation --base-url http://127.0.0.1:4010/mattermost
```

Живой сценарий создал bot/token/team/private channel, добавил участников,
создал и обновил команду, получил настоящий callback и runtime post с чтением
ordinary actor. После отзыва токена runtime получил 401; create_bot от ordinary
actor получил 403. Cleanup удалил команду, отозвал token, архивировал созданные
team/channel и отключил fixture bot. HTTPS и проверка TLS Mattermost сохранены.

Local simulation подтвердила committed state перед POST/PUT/DELETE timeout и
одну попытку без retry; это модель отказа, а не инъекция сбоя в реальный сервер.
Guest owner search был недоступен, прежний Orbit discovery вернул 403; mock
расширен локально по pinned native facts. Cloud binding/publish не выполнялись.
Development receipts в ignored build/ не являются production secret store.
