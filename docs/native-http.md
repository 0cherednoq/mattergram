# Slash-команды и действия поста

`HttpAdapter` принимает настоящие Mattermost slash-запросы и post actions.
`Dispatcher.start(bot, http=adapter)` запускает HTTP и WebSocket в одном lifecycle.
Профиль проверен на собственном Mattermost 11.1.0; отчёт —
[http-core.json](reports/http-core.json). UI builders — [native-ui.md](native-ui.md),
managed form events и native open_dialog — [native-forms.md](native-forms.md).

## Подключить существующую slash-команду

В настройках Mattermost создайте custom slash command `request`, метод POST,
с URL вашего HTTPS reverse proxy, направляющим запросы на
`http://127.0.0.1:8080/mattergram/slash`. Возьмите **выданный этой команде token**.
Bot token и command token — отдельные credentials. SDK на этом этапе не создаёт
команду; автоматическое provisioning остаётся задачей E6.

Задайте `MM_URL`, `MM_BOT_TOKEN`, `MM_TEAM_ID`, `MM_SLASH_TOKEN` и
`MM_HTTP_TRUSTED_PEERS` — список фактических IP/CIDR отправителя через запятую.
Запустите `uv run --locked python examples/http_bot.py`. Пример проходит strict mypy;
его transport/handler путь отдельно исполнен реальным stand harness.

`SlashBinding(team_id, "request", SecretStr(token), method="POST")` проверяет
team, trigger, HTTP method и body token. Если есть `Authorization: Token …`,
он также должен совпасть. GET поддерживается отдельной binding; его query содержит
секреты, поэтому отключите access logging URL в proxy и ASGI server.
Собственный `HTTPServer` SDK отключает aiohttp access log.

Нужен доверенный сетевой путь от Mattermost до SDK. Для reverse proxy задайте
точный адрес proxy и ограничьте доступ к его integration route: доступность proxy
для произвольного клиента сама по себе не доказывает отправителя.
`Forwarded`/`X-Forwarded-For` не используются как доказательство. В production
доверие обеспечивают контролируемая сеть и/или аутентификация транспорта, например mTLS.

## Нативные события и фильтры

`router.slash_command(Command("request"))` получает `SlashCommand` и `CommandObject`
через обычный DI. `SlashCommand.text` — аргументы, `name` — trigger без `/`.
Команда не имеет придуманного post_id и не является Message.
`Command` также разбирает буквальный `/request args` в `router.message`, но это
другой transport: такой текст не становится зарегистрированной slash-командой.

`router.callback_query(RequestAction.filter())` получает `CallbackQuery` с
`user_id`, `channel_id`, `post_id`, `data` и отдельным `selected_option`.
`query.message` — `PostRef` с подтверждёнными post/channel IDs; текст и thread root
в нём отсутствуют. `CallbackData` переносит codec/filter алгоритмы aiogram;
его лимит 4096 UTF-8 bytes — ограничение SDK. Это не Telegram callback limit.

FSM по умолчанию объединяет HTTP и WS события одного user/channel. Для thread
strategy требуется настоящий root_id slash-команды или подписанный thread_id
callback-контекста; post_id не подставляется как неизвестный thread root.

## Подписать context действия

Создайте `CallbackSigner(instance_id=bot.instance_id, bot_id=actual_bot_id,
keys={"k1": secret_bytes}, active_key_id="k1")`; ключ должен содержать не менее
32 секретных bytes. Передайте signer в `HttpAdapter(callback_signer=signer, …)`.
При создании attachment action положите результат `signer.seal(data,
channel_id=channel_id, post_id=post_id, user_id=user_id, allowed_options=["yes", "no"])`
в `integration.context`, а URL callback endpoint — в `integration.url`.
Чтобы привязать действие к post_id, сначала создайте пост, затем обновите actions.
Нативную сериализацию UI builders добавляет E5.

По умолчанию TTL контекста — 900 секунд, максимум — 3600. Он привязан к instance,
bot, channel и переданным optional post/user/team/thread; смена или отзыв ключа
немедленно делает старую подпись непригодной. Для ротации храните старый ключ
в keys до истечения нужных context, а новым active key подписывайте следующие.
`allowed_options` проверяет выбор меню отдельно от прикладных data.

HMAC защищает context, но не подписывает произвольные actor fields HTTP body.
Поэтому проверка транспорта обязательна и для callback. Повторные намеренные
нажатия допускаются; подпись не даёт exactly-once delivery и не заменяет
идемпотентность бизнес-операции.

## Завершить запрос

| Операция | Slash | Post action, профиль 11.1 |
| --- | --- | --- |
| `await event.answer("text")` | ephemeral response | `ephemeral_text`, проверен на WS адресата |
| `answer(..., ephemeral=False)` | in_channel response | отсутствует в сигнатуре callback |
| `await query.update_message("text", props=…)` | нет исходного поста | native update |
| `await event.answer()` | допустимый JSON ACK | `{}` ACK |
| `await event.navigate(url)` | native goto_location | UnsupportedCapabilityError |
| `await query.error(text)` | не применимо | UnsupportedCapabilityError |

Сервер 11.1 не содержит goto_location/error в модели post-action response.
Новые web docs описывают более широкие возможности; baseline определяется
закреплённым исходным кодом и реальным сервером. `HTTPCapabilities` оставляет
эти два флага false. Для другого профиля их можно включать только после отдельной
проверки соответствующего сервера и клиента.

Начальный response завершается один раз. Повторное завершение — ResponseStateError.
Обычный handler должен завершиться в `request_timeout` (default 2.5 секунды,
включая чтение body). Сам `answer` не отделяет оставшуюся работу от этого deadline.
Короткий handler с `None` получает JSON ACK; необработанное событие — HTTP 404.
Handler может вернуть native response mapping вместо shortcut; смешивание mapping
и уже завершённого shortcut даёт ошибку. Неизвестные response fields отклоняются.

Для отдельной managed работы вызовите `await command.defer()` и затем
`await command.followup("ready")`. Только explicit defer разрешает ранний ACK.
Задачи ограничены `max_deferred` (16), `deferred_timeout` (60 секунд), drain и
cancellation при shutdown. Это память процесса. Если job должен пережить сбой,
приложение обязано сохранить его в надёжную очередь **до ACK**.
Forms lookup/validation не маршрутизируются в такую очередь.

`response_url` — SecretStr capability. SDK разрешает только настроенный origin,
deployment prefix и `/hooks/commands/<one segment>`, не посылает Bearer/cookies,
не следует redirect и не повторяет запись. Один handle резервирует максимум
пять попыток за 30 минут от получения; неопределённый исход расходует попытку.
Сервер остаётся источником истины для квоты между workers и восстановленными handles.
Пять реальных отправок проверены; реальный 30-минутный интервал и шестой запрос
на стороне провайдера не измерялись. Потерянный ответ означает
RequestOutcomeUnknown: перед повтором нужно сверить результат.
Request timeout, response_url lifetime и trigger lifetime — разные параметры.

## Lifecycle и ASGI

`Dispatcher.start` владеет Bot/Dispatcher: identity → router startup → HTTP → WS;
при остановке прекращает HTTP admission, останавливает reader, drain/cancel работу,
вызывает router shutdown и закрывает ресурсы. Частичная ошибка bind также закрывает
созданные ресурсы. Python handler должен поддерживать cooperative cancellation;
превышение shutdown budget видно как ShutdownTimeoutError.

`ASGIAdapter(adapter)` можно смонтировать в существующее приложение. Его lifespan
запускает/останавливает adapter, но Bot и Dispatcher остаются у владельца приложения.
После drain их нужно явно закрыть. Router hooks и WS в таком режиме управляются
владельцем приложения. Disconnect отменяет незавершённый запрос без отправки ACK.
aiohttp `adapter.create_app()` имеет такое же borrowing правило.

Body limit default 65536 bytes, max_inflight 64. Дублирующиеся auth/content-type
headers, form keys и JSON keys отклоняются. Ответы ошибок не включают исходный payload.
`event.raw` редактирует известные чувствительные поля; неизвестные поля могут тоже
содержать секреты. Не логируйте payload целиком. `unsafe_raw()` — явный доступ
к полному снимку для контролируемого хранения. `model_dump` не включает
tokens, trigger, аргументы, selected_option и context.

## Воспроизвести доказательства

```sh
uv run --locked pytest tests/unit/test_http_events.py tests/integration/test_http_ingress.py tests/integration/test_dispatcher_ws.py tests/ported/test_filters
uv run --locked python -m tools.verify_behavioral_port
uv run --locked python -m tools.verify_http_stand
postman simulation run postman/simulations/mattergram-native.sim.yaml --port 4010 --no-history
# В другом терминале:
uv run --locked python -m tools.verify_http_simulation --base-url http://127.0.0.1:4010/mattermost
```

Stand подготавливается по [native-client.md](native-client.md). Harness создаёт
только свои команды, использует отдельно control admin и runtime bot, удаляет
созданные команды в finally. В stand разрешён callback только на
`host.docker.internal`; Linux получает host-gateway mapping.
Secret capture остаётся в ignored build/, публичные fixtures обезврежены.
Postman mock моделирует faults; его успешный запуск доказывает поведение SDK
при этих faults, а provider acceptance подтверждает отдельный реальный стенд.
CI HTTP step добавлен; внешний GitHub CI пока не запускался.
