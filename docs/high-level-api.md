# High-level API

`Bot` возвращает строгие `User`, `Channel` и `Message` для частых операций, а
`NativeAPI` остаётся низкоуровневым выходом к reviewed JSON operations. Этот
фасад реализован вручную: generated registry его не перезаписывает.

## Прочитать пользователя, команду и канал

```python
user = await bot.get_user(user_id)
team_users = await bot.get_users_in_team(team_id, active=True, page=0, per_page=100)
channel = await bot.get_channel(channel_id)
direct = await bot.create_direct_channel(other_user_id)
```

`get_users_in_team` возвращает одну страницу строгих `User` из native
`GET /api/v4/users?in_team=...`. Endpoint требует активную сессию и членство
runtime bot в выбранной команде; отдельная административная роль не нужна.
При кастомных ролях или access policies фактический стартовый запрос остаётся
проверкой доступа. Метод не выдаёт прикладные роли и не меняет системные
роли Mattermost. Для полного каталога вызывающая сторона листает страницы до
короткой страницы; `per_page` ограничен диапазоном 1–200.

`create_direct_channel` получает identity текущего бота через тот же runtime
token и передаёт серверу ровно два user ID. Попытка создать диалог с самим
собой отклоняется до записи. Permissions и членство определяет Mattermost.

`Bot.create_channel(...)` создаёт public/private channel от имени runtime bot account и требует
нативное Mattermost permission `create_public_channel`/`create_private_channel`:

```python
alerts = await bot.create_channel(
    team_id=team_id,
    name="game-alerts",
    display_name="Game alerts",
    purpose="Автоматические алерты",
)
```

Успешный write response проверяется по team, type и channel name. Несовпадение становится
`RequestOutcomeUnknown`, чтобы вызывающий код выполнил reconcile перед повтором и не создал дубль.

## Изменить или удалить сообщение

```python
message = await bot.send_message(channel_id, "Черновик")
edited = await message.edit_text("Готово")
await edited.delete()
```

Те же операции доступны явно:

```python
edited = await bot.edit_message_text(channel_id, post_id, "Готово")
await bot.delete_message(channel_id, post_id)
```

Перед записью клиент читает post и проверяет, что `channel_id` совпадает. Это
ловит случайно перепутанные ID, но не заменяет server-side permissions и не
устраняет гонку с параллельным изменением. Успешный ответ на edit должен
содержать тот же post/channel; delete должен вернуть native status `OK`.

## Настроить доставку post

```python
from mattergram.types import MessagePriority

await bot.send_message(
    alerts_channel_id,
    "@alice **CRITICAL: api-01 недоступен**",
    priority=MessagePriority(
        level="urgent",
        requested_ack=True,
        persistent_notifications=True,
    ),
)

await bot.send_message(
    alerts_channel_id,
    "✅ api-01 снова работает",
    silent=True,
)
```

`MessagePriority` принимает только `important` и `urgent`. Persistent
notifications разрешены только для `urgent`; priority разрешён только у root
post. Persistent notification вместе с `silent=True` отклоняется до сети.
Priority сериализуется как `metadata.priority`, а не как top-level webhook
поле. Сервер может дополнительно требовать лицензию, включённую функцию post
priority, права и конкретные `@username` в тексте. SDK не добавляет упоминания и
не отправляет обычный post как fallback при отказе capability.

`silent=True` добавляет query `silent=true`. На поддерживающем сервере такой
post остаётся видимым и приходит по WebSocket, но не создаёт обычные
desktop/push/email notifications и unread/mention counters. Mattermost
разрешает этот флаг bot/OAuth/integration/plugin отправителям; окончательное
решение и HTTP 403 принадлежат серверу.

До пяти уже загруженных Mattermost file IDs можно привязать без нового upload:

```python
await bot.send_message(channel_id, "Готовые файлы", file_ids=[first_id, second_id])
```

Порядок IDs сохраняется. Пустая последовательность означает отсутствие
вложений. SDK проверяет непустые строковые IDs и лимит до HTTP-запроса.

## Отправить один или несколько файлов

Для небольших данных используйте `BufferedInputFile`, для файла на диске —
`FSInputFile`:

```python
from mattergram.types import BufferedInputFile, FSInputFile

await bot.send_document(
    channel_id,
    BufferedInputFile(b"report contents", "report.txt"),
    text="Отчёт",
)

await bot.send_document(
    channel_id,
    FSInputFile("./archive.zip"),
    text="Архив",
)

await bot.send_documents(
    channel_id,
    [FSInputFile("./part-1.csv"), FSInputFile("./part-2.csv")],
    text="Один post с двумя файлами",
)
```

Контракт загрузки и прикрепления файлов проверяется high-level API test suite.

Один `InputFile` можно читать повторно. `BufferedInputFile` хранит переданные
immutable `bytes`; `BufferedInputFile.from_file` сразу читает весь файл в
память. `FSInputFile` при каждой попытке открывает новый binary handle, читает
его порциями без блокировки event loop и закрывает в `finally`, в том числе при
отмене. Сам путь и файл остаются собственностью вызывающего кода: SDK не
удаляет и не переименовывает их.

Размер порции по умолчанию — 64 KiB, допустимы значения от 1 байта до 1 MiB.
Имя должно быть basename длиной до 255 UTF-8 байт без slash/control symbols.
MIME type можно задать явно; иначе он определяется по имени с fallback
`application/octet-stream`.

`send_documents` принимает от одного до пяти файлов. Mattermost требует две
записи: один multipart upload со всеми файлами, затем создание ровно одного post
с полученными `file_ids` в порядке `file_infos`. `send_document` сохраняет
прежнюю сигнатуру и использует тот же общий путь.

SDK не повторяет upload или post автоматически. Malformed/частичный upload
response означает `RequestOutcomeUnknown`. Если upload подтверждён, а post не
подтвердился, файлы могут остаться unattached; исключение содержит отдельный
note для каждого полученного ID. Отмена не запускает retry, а активные потоки
закрываются их `finally`. Выполните предметную сверку до повторной записи.

## Ошибки записи

HTTP 401/403/429 и остальные API statuses используют общую иерархию typed
exceptions. Потеря ответа, malformed success или несовпадение возвращённого
ресурса после write дают `RequestOutcomeUnknown`. Это означает «результат не
подтверждён», а не «сервер ничего не записал».

Public signatures закреплены в `contracts/minimal_api.pyi` и runtime contract
tests. Локальный HTTP harness проверяет exact priority/silent/file IDs wire
contract, batch multipart и failure paths. Ранее восемь методов/shortcut’ов и
одиночный upload были проверены на настоящем Mattermost 11.1.0 обычным bot
token. E7-04 priority/silent/batch scenarios пока подтверждены контрактными и
локальными HTTP-тестами; до отдельного запуска на 11.1 и текущем 12.x они не
объявляются live-проверенными.

