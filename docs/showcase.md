# Многоэкранная карточка Mattermost с таблицами, формами и live-обновлениями

Это практическое руководство для разработчика, который хочет использовать пост Mattermost как
небольшой интерактивный интерфейс. Проверяемая fixture находится в
[`tests/fixtures/showcase_app.py`](../tests/fixtures/showcase_app.py). Она показывает одну карточку с двумя
таблицами, независимой пагинацией и фильтрами, несколькими группами действий, отдельными экранами,
тремя native forms и обновлением после события WebSocket.

## Что показывает пример

После `/showcase` бот создаёт один пост со следующими частями:

- таблица заказов со своим фильтром и номером страницы;
- таблица инцидентов с другим фильтром и номером страницы;
- отдельные action groups для заказов, инцидентов и навигации;
- экраны `Overview`, `Activity` и `About` внутри того же поста;
- формы создания заказа, регистрации инцидента и настройки карточки;
- live-счётчик, который меняется после сообщения `showcase live`.

Mattermost-пост не ограничен одним виджетом. Основной Markdown, несколько attachments, action groups,
select controls и native modal forms можно объединить в один сценарий. Ограничения конкретного
сервера и клиента всё равно нужно проверять через `UIProfile` и собственную client matrix.

## Перед запуском

Нужны Python 3.11+, бот Mattermost, зарегистрированная slash-команда `/showcase` и HTTP endpoint,
доступный серверу Mattermost. Request URL slash-команды должен оканчиваться на
`/mattergram/slash`.

| Переменная | Назначение |
| --- | --- |
| `MM_URL` | Base URL Mattermost, например `https://chat.example.test` |
| `MM_BOT_TOKEN` | Токен обычного bot account |
| `MM_TEAM_ID` | Team ID, к которому привязана slash-команда. Можно не задавать для binding без team scope |
| `MM_SLASH_TOKEN` | Токен, выданный зарегистрированной slash-команде |
| `MM_CALLBACK_BASE_URL` | Доступный Mattermost адрес приложения без завершающего `/` |
| `MM_HTTP_TRUSTED_PEERS` | CIDR доверенных server-to-server peers, через запятую |
| `MM_ACTION_SIGNING_KEY_HEX` | Случайный signing key, минимум 32 bytes в hex |
| `MM_SERVER_VERSION` | Точная версия из поддерживаемого `UIProfile`, по умолчанию `11.1.0` |
| `MM_HTTP_HOST` | Локальный bind host, по умолчанию `127.0.0.1` |
| `MM_HTTP_PORT` | Локальный bind port, по умолчанию `8080` |

`MM_CALLBACK_BASE_URL` используется для двух endpoint:

- `${MM_CALLBACK_BASE_URL}/mattergram/callback` принимает post actions;
- `${MM_CALLBACK_BASE_URL}/mattergram/dialog` принимает native form events.

Signing key можно создать локально:

```sh
python -c "import secrets; print(secrets.token_hex(32))"
```

Не помещайте токены и signing key в репозиторий. За reverse proxy разрешайте HTTP ingress только от
фактических адресов Mattermost. `Forwarded` и `X-Forwarded-For` не заменяют доверенный transport peer.

## Запуск

Установите зависимости проекта и запустите пример:

```sh
uv sync --locked
uv run --locked python tests/fixtures/showcase_app.py
```

В Mattermost выполните `/showcase`. Ожидаемый результат: отдельный пост с двумя таблицами и тремя
панелями controls. Нажатие pagination button меняет только выбранную таблицу. Выбор фильтра сбрасывает
на первую страницу только эту таблицу.

Для проверки live-пути отправьте в тот же канал обычное сообщение:

```text
showcase live
```

В карточке увеличится `Live revision`, а экран `Activity` получит новую запись.

## Как независимы две таблицы

Состояние каждой таблицы хранится отдельно:

```python
@dataclass(slots=True)
class DashboardState:
    orders_table: TableState = field(default_factory=TableState)
    incidents_table: TableState = field(default_factory=TableState)
```

Для `orders` и `incidents` независимо сохраняются `page` и `filter`. Обработчик получает signed
`CardAction.target`, выбирает одну таблицу и изменяет только её. При фильтрации обработчик сбрасывает
страницу выбранной таблицы в ноль, не трогая вторую.

Такой подход масштабируется на дополнительные виджеты. Давайте каждому компоненту стабильный ID и
собственный фрагмент state. Не храните один общий `page` для всей карточки.

## Как правильно обновлять карточку после callback

Post action приходит по HTTP. Внутри callback нужно сформировать полный новый текст и новые `props`,
а затем завершить запрос одним response:

```python
card = render_card(state, callback_url)
props = compile_card(card, action_context)
await query.update_message(card.text, props=props)
```

Полная замена `text + props` нужна, когда меняются controls: выбранный filter, navigation actions,
подписи или доступные варианты. `CallbackQuery` допускает один ответ. Нельзя сначала вызвать
`query.answer()`, а затем `query.update_message()` в том же callback.

Каждый `External` action в примере содержит application data, подписанную `CallbackSigner`. Подпись
привязана к instance, bot, channel и actor. Select дополнительно подписывает допустимые option values.
Transport trust и callback signature решают разные задачи, поэтому нужны оба механизма.

## Как правильно обновлять карточку в live-режиме

WebSocket Mattermost является входящим потоком событий. Он сообщает приложению, что произошло, но
не является каналом записи поста. Правильный поток выглядит так:

```text
Mattermost WebSocket event
        ↓
Dispatcher handler
        ↓
read/mutate application state
        ↓
Bot.edit_message_text → Mattermost REST PATCH
```

Пример слушает `Message` через WebSocket:

```python
@dispatcher.message(F.text == "showcase live")
async def live_event(message: Message) -> None:
    cards = await store.record_live_event(message.channel_id, "domain event")
    await asyncio.gather(*(refresh_with_rest(state) for state in cards))
```

`refresh_with_rest` вызывает `Bot.edit_message_text`. Высокоуровневый метод меняет только `message` и
сохраняет существующие `props` и actions. Это подходит, когда live-событие меняет данные таблиц или
счётчики, но набор controls остаётся прежним.

Если live-событие должно изменить сами controls, есть два безопасных варианта:

1. Создать новую карточку с новым полным набором actions и пометить старую устаревшей.
2. Выполнить reviewed low-level `PatchPost` с полными `message` и `props`, затем сверить результат.

Не отправляйте неполные `props`: так легко удалить существующие attachments или оставить actions,
которые не соответствуют отображаемому state.

Для частых событий добавьте coalescing: храните последнюю revision на post ID, объединяйте burst и
рендерьте только самый свежий snapshot. Не держите storage lock во время REST-запроса. Пример сначала
копирует state под lock, затем выполняет сеть без lock.

## Как формы возвращаются к исходной карточке

Каждая форма получает `Form.state=post_id`. Managed `DialogManager` заменяет transport state на
opaque capability, а после доверенной submission восстанавливает application state в
`event.application_state`. Обработчик использует этот post ID, чтобы найти карточку.

Формы в примере покрывают разные native input families:

- `TextInput` и `TextArea`;
- single `Select` и `Radio`;
- `MultiSelect`;
- `Checkbox`;
- `DatePicker`.

После успешной submission обработчик сначала завершает modal response через `event.answer()`, затем
планирует REST refresh карточки. В production очередь должна быть durable. Локальный пример хранит
background tasks и дожидается их при shutdown, но не переживает перезапуск процесса.

## Что заменить перед production

`DashboardStore` специально ограничен одним процессом. Для нескольких workers или восстановления
после рестарта замените его repository со следующими свойствами:

- durable state по ключу `instance/bot/channel/post/owner`;
- distributed lock или compare-and-swap на mutation;
- индекс активных карточек по channel для live fan-out;
- TTL и явное удаление state после удаления поста;
- idempotency key для business submission;
- очередь с retry policy и reconciliation для background REST writes.

Callback signature не является deduplication. Повторное разрешённое нажатие может прийти несколько
раз до истечения TTL. Бизнес-операции создания заказа или инцидента должны иметь отдельный
idempotency contract.

## Проверка без Mattermost

Offline-тесты проверяют структуру карточки, signed select options, независимость таблиц, наборы полей
форм и owner/channel isolation:

```sh
uv run --locked pytest -q tests/unit/test_showcase_example.py
uv run --locked ruff check tests/fixtures/showcase_app.py tests/unit/test_showcase_example.py
uv run --locked mypy tests/fixtures/showcase_app.py
```

Успешный результат unit suite: `4 passed`. Он доказывает rendering/state contracts, но не доказывает
визуальное поведение конкретного web, desktop или mobile клиента. Для client acceptance используйте
зафиксированную версию Mattermost и capability evidence из [native UI guide](native-ui.md).

Owned-stand verifier создаёт временную slash-команду и посты, выполняет native form, callback
pagination/filter и WS → REST update, а затем удаляет временные ресурсы:

```sh
uv run --locked python -m tools.verify_showcase_stand
```

Фактический API/HTTP/WS результат записан в [showcase report](reports/showcase.json). Проверка на
Mattermost 11.1 подтверждает server-side lifecycle и сохранение `props`, но не заменяет визуальную
проверку клиента.

