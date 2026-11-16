# Нативный клиент E2-01

Реализованы Bot, строгие User/Channel/Message, normalize_posted, get_me,
send_message и shortcuts answer/reply. Проверены локальный HTTP, Postman simulation
и отдельный Mattermost 11.1.0. Router/Dispatcher/F/Memory FSM и SDK-owned WebSocket
подключены и описаны в [native-routing.md](native-routing.md). Полного API/UI и
опубликованного релиза пока нет.

## Создать пост

Из исходников: `uv sync --locked`.

```python
import asyncio
import os
from mattergram import Bot

async def main() -> None:
    async with Bot(token=os.environ["MM_BOT_TOKEN"], base_url=os.environ["MM_URL"]) as bot:
        message = await bot.send_message(os.environ["MM_CHANNEL_ID"], "Привет!")
        await message.reply("Ответ в треде")

asyncio.run(main())
```

Минимальный вызов `Bot.send_message(...)` приведён ниже и проверяется native client suite.
Пример выполнен на собственном тестовом stand. Пакет ещё не опубликован в PyPI.
base_url — корень сервера с подпутём установки, например `https://chat.example.test/mattermost`.
Токен не помещается в URL. Редиректы и автоматические повторы отключены.

## Модели и normalizer

Message.from_wire требует native id/channel_id/user_id/message/create_at. ID не
приводятся из чисел. create_at сохраняется в миллисекундах, date — aware UTC datetime.
Пустой root_id становится None в модели и остаётся пустой строкой в raw wire snapshot.
Unknown fields сохраняются; raw возвращает отдельную копию исходных данных.

message_id равен post_id, chat.id равен channel_id, from_user.id равен user_id.
answer создаёт новый пост без унаследованного root_id. reply использует существующий
корень либо настоящий post_id исходного сообщения. Unbound shortcuts дают BotBindingError.

`normalize_posted(update, bot=bot, self_user_id=real_bot_id)` синхронно разбирает
JSON-строку data.post. Другие events и собственные posts дают None; malformed posted
даёт ProtocolError. HTTP enrichment отсутствует. channel_type/team_id сохраняются
при наличии, неизвестные username/type остаются None. Полный WS envelope доступен
как event_raw; у Message из REST event_raw равен None.

## Ошибки и ресурсы

- 401: AuthenticationError; 403: PermissionDeniedError; 429: RateLimitError.
- Остальные неожиданные HTTP statuses: APIError со status/error_id/request_id/retry_after.
  Server prose не включается в текст ошибки; известный token редактируется в metadata.
- Неверный успешный JSON/model: ProtocolError.
- Соединение не установлено или read не выполнен: TransportError.
- После передачи write-запроса ответ потерян/истёк timeout: RequestOutcomeUnknown.
  Это не доказывает отсутствие созданного post; нужна сверка перед решением о retry.
- CancelledError распространяется как отмена.

Конструктор не открывает сессию. Async context проверяет identity, владеет одним
connection pool и закрывает его при выходе, отмене или ошибке запуска. Альтернатива —
явный await bot.close(). close идемпотентен и терминален. Закрытие во время write
может дать unknown outcome. Один Bot используется в одном event loop; параллельные
запросы допустимы, совместное владение через одновременные contexts отклоняется.

## Настоящий stand

Docker Compose profile использует отдельный project mattergram-native-stand,
закреплённые images и только loopback port. Сначала выберите private IPv4 /24,
не пересекающийся с Docker и host/VPN routes. Следующая подсеть проверена на текущей
машине; другая машина может требовать другую.

```sh
uv run --locked python -m tools.prepare_native_stand --subnet 10.254.248.0/24
docker compose --env-file build/native-stand.env -p mattergram-native-stand -f infra/native-stand.compose.yml up -d
uv run --locked python -m tools.verify_native_stand
```

Harness создаёт fixtures только на собственном loopback stand. Административный
bootstrap отделён от runtime Bot; runtime получает отдельный bot token. Credentials
хранятся только в ignored build/. Не удаляйте fixture settings при сохранении volumes.
Это test setup, не production provisioning E6. Отчёт: build/reports/native-stand.json;
captured fixture и provenance: tests/fixtures/mattermost-11.1-posted*.

Проверены REST create/read, настоящий WS posted, self suppression, thread roots,
401/403 и закрытие клиента. Текущий harness также проверяет SDK-owned WebSocket,
Router/F/DI/answer, error observer и Dispatcher cleanup. Reconnect и timeout
дополнительно проверяются локальными socket tests. Остановить только этот project,
сохранив volumes:

```sh
docker compose --env-file build/native-stand.env -p mattergram-native-stand -f infra/native-stand.compose.yml stop
```

## Postman simulation

CLI 1.82.0: `postman simulation run postman/simulations/mattergram-native.sim.yaml
--port auto --output ndjson --no-history`. Возьмите выведенный port и выполните
`uv run --locked python -m tools.verify_native_simulation --base-url
http://127.0.0.1:PORT/mattermost`. Завершение — Ctrl+C в процессе simulation.

Mock построен из authored minimal contract, сверенного с pinned API fields;
vendor prose не копировалась. Guest CLI не искал owner workspace и ничего не публиковал.
Локальные файлы сохраняются в проекте; облачный контекст потребует привязки аккаунта.
Проверены identity/create/reply, 401/403/429, write-before-timeout и отсутствие retry.
Журнал подтвердил вызовы SDK, context YAML сохраняет scope и неизвестные provider
assumptions. Имитация не выдаётся за результат настоящего Mattermost.
