# Native API coverage

Полная машиночитаемая матрица находится в
[`native-api-coverage.json`](reports/native-api-coverage.json). Она сверяется с
закреплённым Mattermost commit и классифицирует каждый из 491 unique
`operationId` ровно один раз. Это coverage SDK, а не перечень возможностей
самого Mattermost.

| Класс | operationId | endpoints | Значение |
| --- | ---: | ---: | --- |
| `generated_low_level` | 17 | 17 | Доступен через reviewed `NativeAPI.call`; часть также имеет typed facade |
| `high_level_only` | 3 | 3 | Реализован вручную только через typed `Bot` |
| `unsupported_upstream_collision` | 2 | 4 | Один `operationId` объявлен двумя endpoints; не выбирается неявно |
| `unsupported_not_reviewed` | 469 | 469 | Нет reviewed overlay или отдельной high-level реализации |
| Всего | 491 | 493 | Полное покрытие pinned structural inventory |

Итого SDK поддерживает 20 operation IDs. Три high-level-only случая:

- `GetUsers` → `Bot.get_users_in_team`;
- `CreateDirectChannel` → `Bot.create_direct_channel`;
- `UploadFile` → `Bot.send_document`.

Они не добавлены в JSON-only generated registry: team-scoped user read имеет
отдельные typed pagination/filter invariants, upload требует multipart, а
direct channel использует отдельный typed invariant двух identities. Остальные
high-level методы используют уже generated `GetUser`, `GetChannel`, `GetPost`,
`CreatePost`, `PatchPost` и `DeletePost`.

## Что означает unsupported

`unsupported_not_reviewed` не означает, что endpoint отсутствует в Mattermost.
Это означает, что текущая версия SDK не обещает его path/body/status semantics.
`NativeAPI` отклоняет такой `operationId` до создания HTTP session. Произвольного
`request(url)` или обхода registry нет.

`SearchFiles` и `GetSharedChannelRemotes` имеют по два endpoint-кандидата в
pinned источнике. Они классифицированы как `unsupported_upstream_collision`.
Выбирать первый по порядку файла опасно; будущая поддержка должна закрепить
точные source/method/path и отдельное runtime name.

Чтобы перевести операцию в supported:

1. Добавьте exact structural facts в `manifests/native-api-overlays.json` для
   JSON low-level операции либо в `manifests/native-api-coverage.json` для
   отдельного typed/multipart facade.
2. Добавьте validation, error и local HTTP tests.
3. Проверьте операцию на owned Mattermost stand с runtime credential.
4. Обновите документацию и evidence, затем выполните оба drift gates.

```sh
uv run --locked python -m tools.generate_native_api --check
uv run --locked python -m tools.generate_native_api_coverage --check
```

## Error contract

Клиент делает одну попытку и не следует redirect с credential.

| Результат | Исключение |
| --- | --- |
| 401 | `AuthenticationError` |
| 403 | `PermissionDeniedError` |
| 429 | `RateLimitError`, `Retry-After` сохранён |
| Другой неожиданный status, включая неожиданный 2xx/3xx | `APIError` |
| Invalid JSON после безопасного read | `ProtocolError` |
| Invalid JSON или потерянный ответ после write | `RequestOutcomeUnknown` |
| Disconnect безопасного read | `TransportError` |
| Disconnect после отправки write headers | `RequestOutcomeUnknown` |

Server prose не включается в exception text. Из metadata сохраняются только
ограниченные `error_id`, `request_id` и `retry_after`; известные credentials
редактируются. `RequestOutcomeUnknown` требует предметной reconciliation до
retry — оно не утверждает, что сервер откатил запись.

## Происхождение отчёта

Pinned OpenAPI с лицензией CC BY-NC-SA остаётся verification-only input. Coverage
report содержит только operation identifiers, counts и независимо authored
support classifications; descriptions и schemas не копируются. Runtime registry
по-прежнему генерируется только из authored overlay.

