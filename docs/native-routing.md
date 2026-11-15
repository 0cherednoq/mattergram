# Router, WebSocket и Memory FSM

Минимальный бот уже работает: SDK получает `posted` по собственному WebSocket,
пропускает сообщение через Router/F и отправляет ответ. Этот путь, self suppression,
401/403 и закрытие ресурсов проверены на отдельном Mattermost 11.1.0.
Memory и optional Redis FSM проверены; Redis setup — [native-redis.md](native-redis.md).
HTTP slash/callback ingress — [native-http.md](native-http.md), backend forms —
[native-forms.md](native-forms.md). Остальные typed WS normalizers остаются впереди.

## Запустить бота

Из checkout выполните `uv sync --locked`. Задайте `MM_URL` (корень сервера,
включая подпуть установки) и `MM_BOT_TOKEN`. Добавьте bot user в нужный канал.
Запустите `uv run --locked python examples/routing_bot.py` и отправьте `ping`.
Пакет пока не опубликован. Токен берётся из окружения и не печатается.

```python
from mattergram import Bot, Dispatcher, F, Router
from mattergram.types import Message

router = Router()

@router.message(F.text == "ping")
async def ping(message: Message) -> None:
    await message.answer("pong")

dispatcher = Dispatcher()
dispatcher.include_router(router)
# Inside your async main():
# await dispatcher.start_websocket(Bot(token=token, base_url=server_url))
```

`start_websocket` владеет переданным Bot: проверяет identity, запускает startup,
подключает WS, а при завершении закрывает WS и HTTP pool. Bot и Dispatcher после
этого закрыты терминально; для нового запуска создайте новые экземпляры.
`await dispatcher.stop()` запрашивает остановку; затем дождитесь задачи
`start_websocket`. Отмена этой задачи также запускает cleanup и распространяет
`CancelledError`. Startup идёт от родителя к детям; shutdown — в обратном порядке
для роутеров, чей startup был начат. Ошибка shutdown не пропускает оставшиеся callbacks.

## Порядок и данные обработчика

Перенесены алгоритмы aiogram 3.31.0: root filters, outer/inner middleware,
обход роутеров, handlers в порядке регистрации и именованный DI. Первый
успешный handler завершает поиск; `None` считается обработанным результатом.
`SkipHandler` продолжает поиск; `UNHANDLED` означает отсутствие обработчика.
Для дальнейших действий подключайте дочерние Router через `include_router`.

Handler может запросить `bot`, `dispatcher`, `state`, `raw_state`, `event_context`
и application data из `Dispatcher(..., service=service)` по имени аргумента.
Application context и результаты фильтров не могут подменить reserved keys:
`bot`, `event`, `event_update`, `event_context`, `event_raw`, `event_router`,
`handler`, `dispatcher`, `state`, `raw_state`, `exception`, `router`.
Для захвата regex используется `F.text.regexp(...).as_("match")`.

Исключение обработчика поступает в `router.error()`: `ErrorEvent.event` содержит
исходное событие, `ErrorEvent.exception` — исключение. Если error observer не
обработал ошибку, она выходит из Dispatcher. Например, фильтр
`lambda event: isinstance(event.exception, PermissionDeniedError)` выбирает 403.

`Event.context` содержит server instance, реальный bot ID, источник, UTC receive
time и локальный correlation UUID. Этот UUID не является ID события Mattermost.
`Message.event_raw` сохраняет полный envelope. События кроме `posted` доступны
как `RawEvent` через `router.raw_event()`; регистрация других observers сама
по себе не означает наличие typed normalizer или HTTP endpoint.

## FSM и конкуренция

MemoryStorage и SimpleEventIsolation включены по умолчанию. Для Redis передавайте RedisStorage и его RedisEventIsolation явно; несколько процессов требуют общего lock, а не локальной изоляции. В handler доступны
`await state.set_state(Flow.waiting)`, `get_data`, `update_data` и `clear`.
State/StatesGroup сохраняют upstream алгоритм; состояние может служить фильтром
`@router.message(Flow.waiting)`.

Ключ всегда содержит configured server/deployment и authenticated bot ID.
USER_IN_CHANNEL разделяет channel/user; CHANNEL исключает user; GLOBAL_USER
исключает channel; USER_IN_THREAD и CHANNEL_THREAD добавляют настоящий root/post ID.
Отсутствующая область хранится как `None`, числовые или вымышленные IDs не добавляются.
Team scope зарезервирован в StorageKey для будущих профилей; текущие стратегии
не делят channel/user state по необязательной WS team metadata.
DefaultKeyBuilder кодирует все области в JSON, избегая коллизий разделителей.

Параллельные `feed_event` одного session key сериализуются. После отмены waiter
ссылки на locks освобождаются. Данные MemoryStorage копируются глубоко; закрытие
удаляет данные. MemoryStorage не переживает рестарт. Переданные пользователем
storage/isolation остаются в его владении; Dispatcher закрывает только свои defaults.
Разделяемое внешнее storage требует соответствующего разделяемого isolation.

## Backpressure, reconnect и остановка

WS использует одну ограниченную очередь (`queue_capacity=64`) и один последовательный
worker. При заполнении reader ожидает место. После разрыва соединения выполняется
reconnect с экспоненциальной задержкой и jitter до 30 секунд. Auth/protocol ошибки
останавливают запуск. WS не является durable queue: возможны пропуски при разрыве,
серверный replay и exactly-once не заявлены.

При остановке SDK отменяет reader, отбрасывает ещё не начатые события и ждёт
текущий handler до `shutdown_timeout` (по умолчанию 10 секунд). Затем отменяет
его и сообщает ShutdownTimeoutError в группе cleanup errors. Handler должен
сотрудничать с asyncio cancellation. Shutdown callbacks тоже имеют этот timeout.
`pending_events` показывает текущую очередь; `dropped_events`, `reconnects`,
`ignored_self_events` — явные счётчики процесса.
Ошибка запуска сохраняется основной, типы дополнительных cleanup errors добавляются
в exception notes без provider prose. `close()` при активных ручных feeds отклоняется:
сначала дождитесь их завершения.

## Доказательства и границы

`uv run --locked pytest` проверяет native local HTTP/WS, routing/FSM и адаптированные
upstream suites. Их source/target/patch hashes и исключения находятся в
`manifests/ported-tests.json`; `python -m tools.verify_behavioral_port` проверяет replay.
`python -m tools.promote_core --check` сверяет maintained core с audited extraction.
Wheel и wheel из sdist проверяются отдельно вне checkout.

Для настоящего стенда используйте инструкции [native-client.md](native-client.md).
`tools.verify_native_stand` теперь проверяет SDK-owned WS → Router/F/DI → answer,
read-back, собственное исходящее WS-событие, error observer и lifecycle. Credentials
остаются только в ignored `build/`. Внешний GitHub CI и Linux/macOS запуск пока
не подтверждены; локальный workflow не заменяет эти результаты.
