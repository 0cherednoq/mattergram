# Troubleshooting и recovery

Начинайте с типа исключения и фазы операции. Для read безопасный повтор часто допустим после
устранения причины; для write потерянный ответ может означать уже созданный ресурс. Полный
список классов находится в [generated exceptions reference](reference/exceptions.md).

## Быстрая таблица

| Симптом/исключение | Что это доказывает | Действие |
| --- | --- | --- |
| `AuthenticationError` / 401 | credential отклонён | проверьте runtime vs setup token, server root и revoke state |
| `PermissionDeniedError` / 403 | сервер определённо запретил операцию | проверьте team/channel membership и точное permission |
| `RateLimitError` / 429 | сервер ограничил запрос | учитывайте `retry_after`; не добавляйте tight retry loop |
| `TransportError` до write | соединение/read не состоялись | проверьте DNS, TLS, proxy, route и timeout |
| `RequestOutcomeUnknown` | write мог быть принят | сверяйте Mattermost по ID/natural key; только затем retry/recover |
| `ProtocolError` | wire data противоречит контракту | сохраните redacted request/error ID и сравните server profile |
| `UnsupportedCapabilityError` | выбранный profile запрещает функцию | смените проверенный profile или используйте поддерживаемый flow |
| `IngressAuthenticationError` | token/signature/scope/peer/TTL не прошли | проверьте binding, clock, callback key и реальный peer |
| `ResponseStateError` | initial HTTP response уже завершён/не привязан | завершайте slash/action ровно один раз |
| `ResponseDeadlineError` | initial response опоздал | сократите handler или explicit `defer()` до deadline |
| `ShutdownTimeoutError` | handler не завершился при shutdown | исправьте cancellation и вынесите долгую работу в managed queue |

Не логируйте raw request, `unsafe_raw()`, tokens, signed context или явно извлечённые
`SecretStr.get_secret_value()`. `APIError` уже сохраняет безопасные `status`, `error_id`,
`request_id` и `retry_after` без provider prose.

## Post не появился, а write упал

1. Если получен definite 4xx, исправьте запрос/permission; SDK не делает retry сам.
2. Если получен `RequestOutcomeUnknown`, найдите результат через отдельный read path:
   подтверждённый post ID, channel history или provisioning natural key.
3. Если объект существует, продолжайте с его native ID; не создавайте дубль.
4. Если отсутствие подтверждено авторитетным read с достаточными правами, решите retry явно.

404 для private resource или command API может скрывать недостаток доступа и сам по себе не
доказывает отсутствие. Для provisioning journal используйте `complete()` или
`confirm_absent()` только после независимой сверки, как описано в [Bootstrap recovery](bootstrap.md).

## Slash command возвращает 401/404 или timeout

- Сверьте trigger/team/method и token именно выданной команды; bot token сюда не подходит.
- Проверьте `trusted_peers`: адрес должен быть фактическим peer, а не значение forwarded header.
- Убедитесь, что proxy направляет путь на configured `path_prefix` без неожиданного redirect.
- Initial handler должен завершиться внутри `request_timeout` (по умолчанию 2.5 секунды).
- Для долгой bounded работы вызовите `await event.defer()` до deadline и затем `followup()`.
- Если событие не принадлежит ни одному handler, ожидаемый ответ — 404; проверьте Router order
  и filters.

Прямой curl к endpoint проверяет listener/parser, но не доказывает, что Mattermost может
достичь callback URL или передаёт тот же payload. Выполните native integration smoke test.

## WebSocket подключён, но handler не вызывается

- Bot должен состоять в канале и иметь read access.
- Собственные исходящие posts намеренно подавляются.
- Typed `router.message()` получает только нормализованный `posted`; другие события идут в
  `router.raw_event()`.
- Первый успешно обработавший handler останавливает поиск. Для fallthrough используйте
  `SkipHandler`, а не `None`.
- Проверьте root filters и порядок `include_router`.
- Смотрите process metrics `pending_events`, `dropped_events`, `reconnects`,
  `ignored_self_events`, не выводя event payload.

При разрыве возможны пропуски/replay. Если результат важен, сверяйте business state, а не
полагайтесь на локальный счётчик событий.

## FSM или dialog state теряется/гоняется

- MemoryStorage очищается при закрытии и не подходит для restart/multi-process.
- Redis storage и Redis event isolation должны использовать один общий deployment scope.
- Проверьте, что server root, authenticated bot ID, channel/user/thread одинаковы на входах.
- Не подставляйте post ID вместо неизвестного thread root.
- Для dialog submit убедитесь, что signed transition соответствует текущему source state;
  stale action корректно отклоняется.

См. [Redis FSM](native-redis.md), [routing/FSM scopes](native-routing.md) и
[mattergram_dialog](dialog-porting.md).

## UI/form работает на одном клиенте, но не на другом

Выберите explicit `UIProfile`; не включайте capability по номеру версии без evidence. Сверьте
[capability matrix](reference/capabilities.md) и реальные UI reports. Проверка web не означает
desktop/mobile support. Для 11.1/11.7 legacy action IDs имеют более строгие ограничения, чем
newer profiles; SDK fail-fast не переименовывает ID автоматически.

Lookup/refresh/validation относятся к form HTTP lifecycle. Обновление обычного post по REST
не редактирует уже открытую modal. Если form закрылась, поздний lookup должен быть отклонён.

## Безопасный диагностический минимум

Запишите без секретов:

- SDK version и Python version;
- configured Mattermost profile/version и deployment prefix;
- transport (`REST`, `WS`, `slash`, `action`, `form`);
- exception class, HTTP status, safe request/error ID;
- operation/handler name и local correlation ID;
- был ли write передан и выполнена ли reconciliation;
- соответствующий generated API/capability entry.

Затем воспроизведите минимальным примером из [examples index](examples.md). Для SDK regression
запустите `uv run --locked pytest`; live verifier используйте только против owned isolated stand.
