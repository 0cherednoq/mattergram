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

## Отправить файл

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
```

Полный запускаемый пример: [examples/upload_document.py](../examples/upload_document.py).

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

Mattermost требует две записи: сначала multipart upload, затем создание post с
полученным `file_id`. SDK не повторяет их автоматически. Если upload прошёл, а
post не подтвердился, файл может остаться unattached; исключение содержит note
с его ID, если сервер успел вернуть ID. Выполните предметную сверку до retry.

## Ошибки записи

HTTP 401/403/429 и остальные API statuses используют общую иерархию typed
exceptions. Потеря ответа, malformed success или несовпадение возвращённого
ресурса после write дают `RequestOutcomeUnknown`. Это означает «результат не
подтверждён», а не «сервер ничего не записал».

Public signatures закреплены в `contracts/minimal_api.pyi` и runtime contract
tests. Локальный HTTP harness проверяет team-scoped query, multipart и failure
paths. Ранее восемь методов/shortcut’ов были проверены на настоящем Mattermost
11.1.0 обычным bot token; новый `get_users_in_team` пока проверен локальным HTTP
harness и demo-контрактом, но не объявляется live-проверенным на 11.1.0.

