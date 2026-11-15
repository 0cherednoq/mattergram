# Модель Mattermost в mattergram

Эта страница объясняет разработчику, какие native сущности стоят за знакомыми именами
`Bot`, `Message`, `chat`, `answer` и `reply`. Mattergram сохраняет удобный aiogram-style
programming model, но не скрывает границы Mattermost.

## Иерархия и идентификаторы

```text
Mattermost server / deployment prefix
├── Team
│   └── Channel
│       ├── root Post
│       │   └── reply Post (root_id -> root Post)
│       └── independent Post
└── User / bot account
    └── team and channel memberships
```

Все native IDs остаются непустыми строками. SDK не превращает их в числа и не выдумывает
ID там, где transport их не прислал. `base_url` — корень конкретной инсталляции, включая
необязательный prefix, например `https://chat.example.test/mattermost`. Он входит в
instance/storage scope: два разных настроенных server roots не объединяются автоматически.

| Сущность | Что важно приложению |
| --- | --- |
| Server | REST base, WebSocket endpoint, версия/capabilities и отдельный credential scope |
| Team | Область membership и native slash command; не является `chat` |
| Channel | Место posts; public/private/direct тип проверяется сервером |
| Post | Сообщение с `id`, `channel_id`, `user_id`, `message`, `create_at`, optional `root_id` |
| Thread | Root post плюс replies, у которых `root_id` указывает на root |
| User | Native account; bot — отдельный user с runtime token |

## `Message`, `chat`, `answer` и `reply`

`Message.post_id` и совместимый alias `message_id` равны native post ID. `Message.chat`
возвращает `Channel`, а `chat.id` равен `channel_id`. Это удобные aliases, не Telegram chat
model: team и channel остаются разными сущностями.

```python
@router.message(F.text == "status")
async def status(message: Message) -> None:
    await message.answer("Новый независимый post")
    await message.reply("Reply в существующем или новом thread")
```

- `answer()` создаёт независимый post и не наследует thread root.
- `reply()` использует текущий `root_id`, а для root post начинает thread от настоящего
  `post_id`.
- `edit_text()` patch-ит только поле `message`; пропущенные props/files сохраняются.
- `delete()` выполняет native soft-delete при наличии разрешения.

Shortcut работает только у модели, связанной с конкретным `Bot` через REST/normalizer.
Сконструированный вручную `Message` без binding поднимет `BotBindingError`.

## События приходят по разным transport

Одинаковое слово «команда» может означать разные вещи:

| Вход | Модель | Ответ |
| --- | --- | --- |
| WebSocket `posted` | `Message` | новый post/reply через REST |
| Native slash HTTP request | `SlashCommand` | один initial HTTP response, optional bounded follow-up |
| Post action HTTP request | `CallbackQuery` | ACK, ephemeral text или update общего post |
| Native form callback | `DialogSubmission`, lookup/refresh/cancel events | form-specific response lifecycle |
| Остальной WebSocket event | `RawEvent` | application-specific handling |

`SlashCommand` не имеет выдуманного post ID. `CallbackQuery.message` — ограниченный `PostRef`,
а не загруженный `Message`: в нём нет текста или thread root, если они не были подтверждены
подписанным контекстом. Каждый initial HTTP response завершается ровно один раз.

## Shared post, ephemeral response и form — разные surfaces

- Channel post видят участники канала; его можно читать/редактировать через REST по native ID.
- Ephemeral slash/action response адресован actor и имеет ограниченный integration lifecycle.
- Native form открывается отдельным `open_dialog` request и возвращает typed submission;
  это не ещё один message widget внутри post.

Для live dashboard сначала публикуется shared post, затем WS-событие меняет application
state, после чего bot выполняет REST patch этого же post. Такой поток показан в
[showcase](showcase.md). WebSocket доставляет событие, но сам не является API редактирования.

## State scope

FSM key всегда включает настроенный server/deployment и authenticated bot ID. Стратегия
добавляет channel/user, а thread strategies — подтверждённый root/post ID. Team metadata не
подставляется в key, если выбранная стратегия этого не определяет. Для нескольких процессов
используйте общий [Redis storage и isolation](native-redis.md), а не process-local MemoryStorage.

## Wire snapshots и совместимость

`User`, `Channel` и `Message` строго проверяют обязательные поля, сохраняют неизвестные wire
fields и отдают отделённую копию через `.raw`. Время post хранится как native milliseconds;
`.date` — aware UTC `datetime`. Пустой wire `root_id` нормализуется в `None`.

Typed model не означает, что любая функция любого Mattermost доступна. Выберите явный server/UI
profile и сверяйте [generated capability matrix](reference/capabilities.md). Web-проверка одной
версии не доказывает поддержку desktop/mobile.
