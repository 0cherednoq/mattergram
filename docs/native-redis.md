# Redis FSM

`RedisStorage` и `RedisEventIsolation` реализованы как optional Redis extra.
Проверены Redis 7.4.12, клиенты redis-py 5.3.0 и 8.1.0, native string keys,
TTL и блокировки. Redis-профиль также прошёл настоящий Mattermost WS → Router →
FSM transition → чтение состояния отдельным Redis-клиентом → clear.

## Подключение

Из checkout: `uv sync --locked --extra redis`. Для собранного wheel:
`python -m pip install './dist/mattergram-0.1.0a1-py3-none-any.whl[redis]'`.
Пакет ещё не опубликован. Базовая установка работает без зависимости `redis`;
классы импортируются явно из `mattergram.fsm.storage.redis`.

```python
from mattergram import Dispatcher
from mattergram.fsm.storage.redis import RedisStorage

storage = RedisStorage.from_url(redis_url, state_ttl=3600, data_ttl=3600)
isolation = storage.create_isolation()
dispatcher = Dispatcher(storage=storage, events_isolation=isolation)
try:
    await dispatcher.start_websocket(bot)
finally:
    try:
        await isolation.close()
    finally:
        await storage.close()
```

Создавайте ресурсы внутри async main. Для запуска задайте переменные `MM_URL`,
`MM_BOT_TOKEN`, `MM_REDIS_URL`, затем сообщение `start` и тема.
Пример проходит проверку типов; реальный stand проверяет те же state/DI/transport
операции отдельным harness, не выдаётся за запуск файла примера.

Всегда передавайте Redis isolation вместе с storage, если несколько процессов
обрабатывают одну область FSM. Default SimpleEventIsolation защищает только один
процесс. Прямой FSMContext вне Dispatcher тоже требует явного `async with isolation.lock(key)`
для read-modify-write.

## Ключи и данные

Storage использует native StorageKey и DefaultKeyBuilder. Namespace всегда включает
configured server/deployment, authenticated bot ID, channel/user, выбранный thread,
team, dialog_session_id и destiny. Отсутствующие области — `None`; ID остаются
строками. JSON-кодирование ключей сохраняет различия между областями и разделителями.
Две формы одного пользователя могут иметь разные dialog_session_id, а обычная
беседа — отдельный ключ без dialog_session_id. Это поддержка storage isolation;
HTTP forms runtime ещё выполняется по этапу E5.

Данные принимаются как Mapping со строковыми ключами и сохраняются JSON-объектом.
Поддерживаются upstream hooks `json_dumps`/`json_loads` и bytes/decoded Redis clients.
Повреждённые JSON/UTF-8, scalar/list вместо объекта дают StorageDataError без raw
данных в сообщении исключения. `State()` с отсутствующим значением очищает state.
Полученные данные не разделяют mutable объекты с серверным значением.

`state_ttl` и `data_ttl` задаются независимо: положительные целые секунды либо
timedelta не менее секунды. Redis использует целые секунды; дробная часть timedelta
отбрасывается клиентом. `None` означает отсутствие срока. SET обновляет TTL;
чтение его не продлевает. `set_state(None)`/`set_data({})` удаляют соответствующий
ключ, `FSMContext.clear()` вызывает обе операции. Закрытие клиента не удаляет state.
Сохранность после перезапуска Redis зависит от настройки сервера, AOF/snapshots и
доступности; наш тестовый Redis намеренно ephemeral и production persistence не обещает.

## Владение и завершение

`RedisStorage(redis=client)` и `RedisEventIsolation(redis=client)` по умолчанию
заимствуют client. `close()` запрещает новые операции SDK и оставляет внешний
client открытым. При явном `owns_client=True` владелец берёт закрытие на себя.
`from_url()` создаёт собственные client/pool и закрывает их через `aclose`.
Это следует явной модели lifecycle [redis-py asyncio](https://redis.io/docs/latest/develop/clients/redis-py/async/).

`storage.create_isolation()` заимствует клиент storage. Поэтому порядок закрытия:
завершить handlers/waiters → isolation → storage. Закрытие isolation при активном
владельце или waiter отклоняется; после завершения его можно повторить. Переданные
в Dispatcher ресурсы остаются пользовательскими: Dispatcher не закрывает их.
Закрытие owned resources идемпотентно, включая повторную попытку закрыть pool.

`from_url` задаёт socket/connect timeout 5 секунд и отключает автоматический retry
команд. `connection_kwargs` позволяет задать собственную политику. Для переданного
вручную client политика остаётся его настройкой. Не считайте потерянный ответ
подтверждением отсутствия записи: сначала сверяйте состояние/бизнес-результат.

## Lease и транзакционная стратегия

Redis isolation использует token ownership и атомарные SET-NX/Lua операции redis-py.
SDK выполняет ограниченные неблокирующие попытки и проверяет ожидающую cancellation
между командами. Это защищает от воспроизведённого случая Python 3.11/redis-py 8.1,
где завершённая команда возвращалась при pending cancellation, а upstream blocking
loop продолжал ждать. Это проверка наблюдённого сочетания, не общее утверждение обо
всех версиях Redis или Python.

Настройки по умолчанию: lease `timeout=60`, acquire `blocking_timeout=10`,
`sleep=0.1`. Меняются через `lock_kwargs`. Все значения должны быть конечными и
положительными; бесконечное ожидание, отключение ownership errors и произвольные
параметры отклоняются. `hold_timeout` по умолчанию равен 80% lease и обязан быть
короче lease. Бюджет начинается до первой SET-попытки: ожидание и сетевые задержки
не продлевают его. Просроченный acquire не запускает handler.

Timeout отменяет cooperative handler и освобождает lock; истёкший бюджет не
превращается в успех, даже если handler перехватил CancelledError и вернулся.
Повторная отмена во время release откладывается до завершения ограниченного cleanup.
Потеря ownership/ошибка release видна вызывающему коду. Если соединение потерялось
в неопределённый момент acquire/release, оставшийся lock ограничен серверным lease.

Это advisory lease для последовательной работы с ключом, а не fencing, Redis
failover/Cluster consistency или транзакция с внешней БД. Handler должен соблюдать
asyncio cancellation и укладываться в бюджет; нельзя продолжать бизнес-запись после
отмены. Выбранная стратегия: per-key isolation для FSM и идемпотентная бизнес-операция
с фиксацией результата перед переходом FSM. Для внешних side effects используйте
свой transaction/outbox и ключ дедупликации; SDK не повторяет бизнес-операцию.
State и data — разные Redis keys: обычные `clear`/`set_state`/`update_data` не являются
одной multi-command транзакцией. Приложение, которому нужна атомарность state/data
вместе с бизнес-записью, должно согласовать её в своей Redis transaction/БД.

## Проверки

```sh
uv run --locked python -m tools.prepare_native_stand --subnet 10.254.248.0/24
docker compose --env-file build/native-stand.env -p mattergram-native-stand -f infra/native-stand.compose.yml up -d --wait redis db mattermost
uv run --locked python -m tools.verify_redis_stand --mattermost --distributions
```

Для `--distributions` сначала соберите текущий wheel/sdist: `uv run --locked python -m build`.
Используйте свободный subnet; подготовка не удаляет чужие Docker networks. Порт
назначается автоматически только на loopback. Проверяющий скрипт требует точные
compose ownership labels, pinned image и PONG; пропущенные тесты не считаются успехом.
Он проверяет 13 native Redis cases и адаптированные upstream storage/isolation suites.
Без явного `MATTERGRAM_REDIS_URL` default pytest отмечает real Redis cases как skipped;
в обязательном CI Redis job они должны все выполниться.

Consumer verifier сначала устанавливает base wheel без Redis и проверяет его
работоспособность. Затем устанавливает extra и запускает configuration tests;
с реальным профилем также проверяет все 13 Redis cases без skips. Всё повторяется
для wheel из sdist вне checkout. Отчёты: `build/reports/redis-stand.json`,
`redis-tests.xml`, `distributions.json`; сохранённая сводка — `docs/reports/redis-core.json`.
Внешний GitHub CI по-прежнему не запускался.
