# Миграция с aiogram на mattergram

Mattergram сохраняет знакомые `Bot`, `Dispatcher`, `Router`, `F`, middleware, DI, FSM и часть
dialog concepts, но transport и UI остаются Mattermost-native. Миграция — это перенос
application flow, а не глобальная замена импортов.

## Сначала разделите portable и platform-specific код

Оставьте без изменений domain services, validators, repositories и большую часть async
business logic. В отдельный слой вынесите:

- Telegram update/message/callback types;
- chat/user/message IDs и thread assumptions;
- polling/webhook startup;
- keyboards, callback data и message editing;
- media/file IDs и formatting;
- command registration и deployment credentials.

После этого переносите один end-to-end scenario: вход → filters/DI → state → ответ → recovery.

## Карта API и semantics

| aiogram-подход | mattergram | Важное отличие |
| --- | --- | --- |
| `Bot(token=...)` | `Bot(token=..., base_url=...)` | server root обязателен; один owner/event loop/pool |
| `Dispatcher`, `Router`, `F` | те же основные имена | только реализованные observers/transports; первый handler завершает поиск |
| polling | `Dispatcher.start_websocket(bot)` | SDK-owned Mattermost WS, не durable queue |
| `message.answer()` | `Message.answer()` | новый channel post, thread не наследуется |
| `message.reply()` | `Message.reply()` | native thread через root post ID |
| `chat.id` | `message.channel_id` / `message.chat.id` | team и channel — разные сущности, ID строки |
| message command `/x` | `Command("x")` над `Message` | не создаёт native slash integration |
| webhook command | `SlashCommand` + `HttpAdapter` | issued command token, peer trust и single HTTP response |
| callback query | signed `CallbackQuery` | native post action, separate `selected_option`, actor/scope binding |
| inline/reply keyboard | Blocks/attachments/actions | другой UI schema и client capability profile |
| FSM memory/Redis | `MemoryStorage` / `RedisStorage` | key включает server+bot+native channel/user/thread scope |
| aiogram_dialog windows | `mattergram_dialog` | native post/form renderers; Telegram media/keyboards исключены |

Проверяйте точные signatures в [generated API reference](reference/api.md), а не по памяти об
aiogram. Mattergram kwargs строгие; неизвестный registration argument не игнорируется.

## Команды: message text и native slash — не одно и то же

Этот handler реагирует на literal `/request` в channel post:

```python
@router.message(Command("request"))
async def request_message(message: Message) -> None:
    await message.answer("received from WebSocket")
```

Native `/request` создаётся в Mattermost и вызывает HTTP endpoint:

```python
@router.slash_command(Command("request"))
async def request_slash(event: SlashCommand, command: CommandObject) -> None:
    await event.answer(f"Received: {command.args or '(empty)'}")
```

Второму варианту нужны command binding/token, trusted transport и своевременный HTTP response.
Декоратор сам не создаёт server resource. Соберите metadata через command manifest или
используйте [Bootstrap](bootstrap.md).

## Callback и UI

Не переносите Telegram inline keyboard JSON. Объявите Mattermost Blocks или legacy actions,
выберите `UIProfile` и подпишите application context через `CallbackSigner`. Подпись связывает
instance, bot, channel и optional post/user/team/thread; она не заменяет проверку transport и
business idempotency.

Native form — отдельная modal surface. Ввод нескольких полей, lookup, refresh, validation и
wizard реализуйте через [managed forms](native-forms.md), а не через последовательность
Telegram message-input widgets. Для сложных post/form flows используйте
[mattergram_dialog](dialog-porting.md).

## Formatting, files и IDs

- Используйте Mattermost Markdown; Telegram HTML/parse modes не переносятся.
- Все IDs — native strings. Не делайте `int(chat_id)` и не генерируйте фиктивный update ID.
- `Message.create_at` — milliseconds; `.date` возвращает aware UTC datetime.
- Upload принимает `InputFile`, затем создаёт post с native file ID. Telegram `file_id`, URL
  downloader и media groups не являются частью этого API.
- Direct message сначала требует native direct channel через `create_direct_channel(user_id)`.

## Lifecycle и ошибки

Используйте один явный владелец ресурсов:

```python
bot = Bot(token=token, base_url=base_url)
dispatcher = Dispatcher(storage=storage, events_isolation=isolation)
await dispatcher.start_websocket(bot)
```

`CancelledError` распространяется, shutdown bounded, закрытый client нельзя переоткрыть.
Записи не повторяются автоматически. Особенно обработайте `RequestOutcomeUnknown`: это не
обычный network retry, а сигнал сверить provider state.

HTTP interactions имеют deadline и завершаются один раз. Если работа должна продолжиться,
используйте explicit `defer()` или сохраните durable job до ACK. Background task в памяти не
переживает рестарт.

## Пошаговый migration checklist

1. Зафиксируйте один сценарий и его ожидаемый application effect.
2. Замените Telegram IDs на явные server/team/channel/post/user/thread значения.
3. Выберите transport: WS message, native slash, post action или form.
4. Перенесите Router/F/middleware/DI и проверьте порядок/fallthrough.
5. Выберите Memory или общий Redis storage+isolation.
6. Пересоберите UI из Mattermost declarations и explicit profile.
7. Разделите setup credential и runtime bot token.
8. Добавьте recovery для deadline, duplicate event и unknown write outcome.
9. Прогоните contract tests, затем owned Mattermost и нужные реальные clients.
10. Зафиксируйте evidence; web acceptance не выдавайте за mobile/desktop.

Inventory перенесённых и исключённых `aiogram_dialog` semantics находится в
[dialog porting guide](dialog-porting.md). Общие ограничения и проблемы — в
[troubleshooting](troubleshooting.md).
