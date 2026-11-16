# Установка и первый бот

Эта инструкция предназначена для разработчика, который хочет проверить базовую установку
mattergram, отправить первый пост и затем запустить обработчик WebSocket-сообщений. Текущая
версия `0.1.0a1` имеет статус pre-alpha и ещё не опубликована в PyPI, поэтому команды ниже
работают из checkout проекта.

## Что понадобится

- Python 3.11–3.14;
- доступный Mattermost и его HTTPS URL, включая deployment prefix, если он есть;
- token отдельного bot account;
- native channel ID, в котором bot уже состоит участником и может создавать посты.

Не используйте setup/admin token как runtime credential. Для автоматического создания bot,
team, channel и slash commands есть отдельный [Bootstrap](bootstrap.md); первый запуск проще
проверить на уже подготовленном bot account.

## Установите зависимости

```powershell
uv sync --locked
```

Базовая установка содержит REST, WebSocket, routing, HTTP ingress, UI и in-memory FSM.
Дополнительные зависимости подключаются только по задаче:

```powershell
uv sync --locked --extra redis
uv sync --locked --extra provisioning
```

`redis` нужен для общего FSM/dialog state нескольких процессов. `provisioning` добавляет
шифрование persistent Bootstrap store; он не нужен обычному runtime bot.

## Отправьте проверочный пост

В PowerShell задайте окружение только для текущего процесса:

Задайте `MM_URL`, `MM_BOT_TOKEN` и `MM_CHANNEL_ID`, затем выполните минимальный код ниже.
Он печатает подтверждённый native `post_id`, не печатает token и не делает автоматический retry
записи. Если ответ потерян после отправки, SDK поднимет
`RequestOutcomeUnknown`: сначала проверьте канал, а не запускайте команду вслепую ещё раз.

Эквивалентный минимальный код:

```python
import asyncio
import os

from mattergram import Bot


async def main() -> None:
    async with Bot(
        token=os.environ["MM_BOT_TOKEN"],
        base_url=os.environ["MM_URL"],
    ) as bot:
        post = await bot.send_message(os.environ["MM_CHANNEL_ID"], "Hello from mattergram")
        print(post.post_id)


asyncio.run(main())
```

Async context проверяет identity, создаёт один HTTP pool и закрывает его при обычном выходе,
ошибке или cancellation. Закрытый `Bot` терминален; для повторного lifecycle создайте новый.

## Запустите обработчик сообщений

Создайте `Router`, зарегистрируйте message-handler и запустите `Dispatcher.start_websocket()` как
показано в [руководстве Router и WebSocket](native-routing.md). Отправьте `ping` в канал, где
состоит bot. Ожидаемый результат — новый channel post `pong`.
`Dispatcher.start_websocket()` владеет переданным `Bot`, отбрасывает собственные исходящие
posts и корректно закрывает WebSocket/HTTP ресурсы при остановке.

Это не polling и не durable queue. При разрыве SDK переподключается, но exactly-once delivery
не обещается. Для важных эффектов храните application idempotency key и сверяйте состояние.

## Выберите следующий сценарий

| Задача | Руководство |
| --- | --- |
| Полный запускаемый бот | [Game Admin](game-accounts-admin.md) |
| Router, filters, middleware и FSM | [Router и WebSocket](native-routing.md) |
| Slash command и post action HTTP | [HTTP ingress](native-http.md) |
| Native form, validation и wizard | [Forms](native-forms.md) |
| Несколько таблиц/forms и live update | [Showcase](showcase.md) |
| Redis state | [Redis FSM](native-redis.md) |
| Автоматическое создание ресурсов | [Bootstrap](bootstrap.md) |
| Декларативные окна | [mattergram_dialog](dialog-porting.md) |

Единственный запускаемый пример, его permissions, minimum profile и уровень проверки перечислены в
[generated examples index](examples.md). Остальные контракты проверяются тестами и описываются в
соответствующих руководствах.

## Быстрая диагностика

- 401 — проверьте runtime bot token, а не URL или setup credential.
- 403 — bot существует, но не имеет нужного membership/permission.
- redirect — передайте конечный server root; SDK намеренно не следует redirect с credential.
- `TransportError` — проверьте DNS/TLS/сетевой путь.
- `ProtocolError` — сервер вернул форму данных вне проверенного контракта.

Подробные recovery-действия: [troubleshooting.md](troubleshooting.md). Реализованные imports и
сигнатуры: [generated public API](reference/api.md).
