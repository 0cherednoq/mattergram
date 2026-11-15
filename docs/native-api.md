# Low-level Native API

`NativeAPI` выполняет reviewed JSON-операции Mattermost по точным generated
path/status definitions. Он нужен, когда готового high-level метода `Bot` нет,
но operation уже включён в [authored overlay](../manifests/native-api-overlays.json).

Текущий E7-01 scope — 17 операций users/status/preferences, teams, channels,
posts и reactions. Генератор обнаруживает 493 endpoints и 491 уникальный
`operationId` в закреплённой OpenAPI; это не означает, что остальные 474 уже
поддерживаются runtime-клиентом.

## Выполнить операцию

Base installation уже содержит `NativeAPI`; отдельный extra не нужен.

```python
import os

from mattergram import NativeAPI

async with NativeAPI(
    base_url=os.environ["MM_URL"],
    token=os.environ["MM_BOT_TOKEN"],
) as native:
    me = await native.call("GetUser", path={"user_id": "me"})
    posts = await native.call(
        "GetPostsForChannel",
        path={"channel_id": os.environ["MM_CHANNEL_ID"]},
        query={"page": 0, "per_page": 20},
    )
```

Client владеет своим HTTP pool, проверяет identity при входе в async context и
закрывается терминально. Он не получает setup credential и не выполняет
административный fallback. Для automatic provisioning используйте
[Bootstrap](bootstrap.md), а не `NativeAPI`.

Полный исполняемый read-only пример: [examples/native_api.py](../examples/native_api.py).

## Передать path, query и JSON body

`path` должен содержать ровно placeholders выбранной операции. Каждый value
percent-encoded как один path segment; лишние, пропущенные, пустые значения и
`.`/`..` отклоняются до сети.

`query` принимает строки, bool, конечные числа или непустые последовательности
этих значений. Bool сериализуется как `true`/`false`; повторяющееся значение
становится повторяющимся query parameter. Low-level слой проверяет безопасную
сериализацию, но не объявляет, что произвольное имя query поддерживается
сервером.

```python
post = await native.call(
    "CreatePost",
    body={"channel_id": channel_id, "message": "Deploy started"},
)
post_id = post["id"]

await native.call(
    "PatchPost",
    path={"post_id": post_id},
    body={"id": post_id, "message": "Deploy finished"},
)
```

Body проходит structural JSON validation: только object/array/scalars/null,
строковые object keys, конечные числа, без циклов и глубины больше 64. Это не
валидация OpenAPI schema. Ошибки полей и permissions остаются ответами
Mattermost.

## Обработать ошибки и неопределённую запись

Status принимается только если он указан у generated operation. `401`, `403` и
`429` сохраняют существующие typed exceptions; остальные неожиданные statuses
дают `APIError`. Redirect не выполняется.

Безопасные read operations не retry автоматически. Для write operation timeout,
disconnect после отправки или невалидный successful JSON означает
`RequestOutcomeUnknown`: не повторяйте запись вслепую, сначала прочитайте
ресурс или выполните предметную reconciliation.

Response возвращается как raw decoded JSON. Generated layer намеренно не
превращает payload в high-level models и не исправляет расхождения сервера со
schema. На живом Mattermost 11.1.0 пустой `GetPreferences` вернул JSON `null`,
хотя pinned OpenAPI объявляет array; это записано в evidence и не скрыто
автоматическим `[]`.

## Проверить доступные операции

```python
from mattergram.native import OPERATIONS

for operation_id, operation in OPERATIONS.items():
    print(operation_id, operation.method, operation.path_template)
```

Registry read-only. В E7-01 включены:

| Область | operationId |
| --- | --- |
| Users | `GetUser`, `GetUserByUsername`, `GetUserStatus`, `GetPreferences` |
| Teams/channels | `GetTeam`, `GetTeamByName`, `GetChannel`, `GetChannelByName` |
| Posts | `CreatePost`, `GetPost`, `DeletePost`, `PatchPost`, `GetPostThread`, `GetPostsForChannel` |
| Reactions | `SaveReaction`, `GetReactions`, `DeleteReaction` |

Полная классификация всех pinned `operationId`, high-level-only операции,
error contract и правила promotion: [native-api-coverage.md](native-api-coverage.md).

Настоящий Mattermost 11.1.0 принял все 17 operations от ordinary bot token.
`SaveReaction` вернул HTTP 200, хотя OpenAPI документирует 201; overlay поэтому
явно принимает только `{200, 201}` и ссылается на native stand evidence.

## Изменить generated scope

Не редактируйте `src/mattergram/native/_operations.py`. Добавьте reviewed entry
в `manifests/native-api-overlays.json`, укажите pinned source и выполните:

```sh
uv run --locked python -m tools.fetch_upstream --verify
uv run --locked python -m tools.generate_native_api
uv run --locked python -m tools.generate_native_api --check
```

Generator сверяет operationId, HTTP method, path, documented success status и
source hash с `upstream.lock.json`. Недокументированный success status требует
отдельного 2xx override с reason и evidence path. Две upstream-коллизии
operationId — `SearchFiles` и `GetSharedChannelRemotes` — остаются видимыми в
generation report и не могут случайно выбраться только по имени.

OpenAPI documentation лицензирована CC BY-NC-SA 3.0 и остаётся reference-only
snapshot. Distributed Python registry строится из отдельно authored structural
overlay и не копирует descriptions или schemas. Ручной `NativeAPI`, `Bot` и
`SetupClient` generator не перезаписывает.

Multipart/binary downloads, response headers, schema-generated models и
operations вне registry пока не поддержаны этим low-level client. Их нельзя
вызывать через произвольный URL в обход reviewed overlay.
