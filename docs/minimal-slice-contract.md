# Минимальный срез: контракт E0 → E2

Статус: E2-01 реализован (native models, normalizer и REST client); routing/WS runtime ещё предстоит подключить. Сигнатуры вынесены в
`contracts/minimal_api.pyi`. Этот файл не устанавливается как API готового SDK.
Основной дизайн v1.2 сохраняет полный будущий scope; этот документ задаёт порядок
первой реализации и её проверяемую границу.

## Источники и перенос

Реальные commits, дата получения, SHA-256 каждого файла и digest дерева находятся
в `upstream.lock.json`. Решения по 24 исходным модулям — в
`manifests/core-plan.json`; `manifests/core.json` добавляет точное происхождение,
связанные тесты, зависимости и пути к лицензиям. 18 модулей выбраны для copy/adapt,
6 явно исключены. Операция adapt требует отдельного семантического патча; решение
в manifest не означает, что модуль уже перенесён или проверен.

Полный AST-отчёт воспроизводится командой из CONTRIBUTING.md. Сохранённая сводка:
`docs/reports/upstream-inventory.json`. Inventory не исполняет доноров, учитывает
относительные imports, TYPE_CHECKING/условия, optional imports, явные и выводимые
exports. Dynamic/star imports и кандидаты runtime registration отмечаются отдельно.
Связь теста с import не доказывает покрытие поведения. Анализ не моделирует весь
порядок исполнения package __init__, getattr, plugin discovery и runtime mutations.

Один прямой импорт handler через upstream aggregates достигает 730 модулей.
Копирование полного графа запрещено: root, filters, handlers и types получают
явные нативные экспорты. Telegram models/methods/client не переносятся.

## Первый сквозной сценарий

1. Bot открывает собственную aiohttp-сессию, получает реальную identity через
   `GET /api/v4/users/me`. В URL сохраняется подпуть установки Mattermost.
2. WebSocket проходит authentication challenge с bot token. `posted` содержит
   JSON-строку post внутри `data.post`; normalizer декодирует её без REST-запросов.
3. Чужой post превращается в Message. Пост текущего bot user игнорируется по
   точному `user_id`; edit/delete не переименовываются в posted.
4. Dispatcher передаёт событие в перенесённый Router. `F.text == "ping"` выбирает
   handler, который вызывает `await message.answer("pong")`.
5. Bot выполняет `POST /api/v4/posts` с `channel_id` и `message`; проверяет ответ
   `201`, возвращает Message с настоящим post ID. Повтор входящего события о
   собственном исходящем post не запускает handler снова.
6. Отмена/ошибка запуска/остановка освобождает WS и HTTP-сессию; чужие ресурсы SDK
   не принимает во владение через неявный глобальный singleton.

Минимальный low-level scope включает CreatePost; users/me — служебный identity
запрос. Это не генерация или обещание покрытия полного REST API. HTTP callbacks,
slash/forms/provisioning и dialog выполняются по последующим этапам исходного плана.

## Модели и значения

- Все платформенные IDs — непустые строки. Не приводить int к str и не создавать
  суррогатные ID. Отсутствующий обязательный post/user/channel ID — ProtocolError.
- Message сохраняет post_id/channel_id/root_id, текст, create_at в миллисекундах,
  aware datetime UTC и исходные неизвестные wire поля. Пустой root_id означает
  отсутствие треда; team_id в DM может быть пустым или отсутствовать.
- Не выполнять скрытое обогащение: неизвестные поля User/Channel имеют None,
  реальные ID сохранены. На проверенном 11.1.0 posted содержит channel_type:
  это известное значение и team_id при наличии сохраняются. Username не выводится
  из sender_name. Полный envelope сохранён как event_raw.
- `message_id == post_id`, `chat.id == channel_id`, `from_user.id == user_id`.
- answer создаёт новый пост без автоматического root_id. reply использует
  существующий корень треда либо post_id исходного сообщения.
- Неизвестные аргументы публичных методов отклоняются обычным TypeError.
  Неподдерживаемые действия не реализуются фиктивными успешными методами.

## Routing и ошибки

Сохраняются порядок root filters, outer/inner middleware, регистрация handlers,
именованный DI, UNHANDLED, None успешного handler и явный SkipHandler. Циклы
роутеров/двойное подключение отклоняются. Фильтры не перезаписывают зарезервированные
bot/event/system context keys. Нативные event fixtures заменяют Telegram Update.

401 → AuthenticationError, 403 → PermissionDeniedError; код сервера/статус и request
ID доступны в диагностике без токенов. Повреждённый JSON/неверная форма успешного
ответа → ProtocolError. Ошибки соединения/таймауты → TransportError; неопределённый
исход записи обозначается RequestOutcomeUnknown. Не повторять CreatePost вслепую.
Отмена распространяется как CancelledError после очистки ресурсов. Для WebSocket
reconnect явно документировать возможность пропуска событий: это не durable queue.

## Приёмка E2

- Протокольный тест проходит от wire posted через настоящий выбранный Router/F
  до реального HTTP запроса к локальному тестовому серверу; проверяет тело/статус.
- На отдельном Mattermost stand: ping → pong, реальный post ID и отсутствие
  ответа на собственный исходящий post. Сохранить версию сервера и клиента.
- Проверить 401, 403, malformed post/response, подпуть URL, таймаут записи,
  отмену/закрытие и отсутствие повторного CreatePost после неоднозначного исхода.
- Отдельно проверить answer/reply на верхнем post и сообщении внутри треда.
- Перенесённые suites проверяют порядок handlers/middleware/filters и handler data;
  исключённые Telegram cases перечисляются с причиной.

Локальный stub transport или успешный импорт не закрывает пункт реального stand.

## Открытые решения и ограничения

- Сервер 11.1.0 выбран как закреплённый протокольный baseline; реальная установка
  пользователя и клиентская матрица пока неизвестны, совместимость не заявлена.
- Перед E7 решить условия использования schema/documentation. `api/README.md`
  baseline содержит CC BY-NC-SA 3.0. Runtime под MIT не получает скопированные
  описания из схемы; дальнейшая генерация требует отдельного решения по источникам.
- В developer-docs snapshot есть BSD-style LICENSE.txt; он сохранён отдельно от
  лицензии основного Mattermost репозитория и документации api/README.md.
- Доступность mattergram/mattergram-dialog проверена в PyPI и TestPyPI: четыре
  JSON endpoints вернули 404, результат в `docs/reports/package-names.json`.
  Это наблюдение, не резервирование имени; повторить перед публикацией.
- GitHub-репозиторий mattergram, Trusted Publishing и production secrets
  пока не настроены. Собственный локальный stand уже проверен. Наличие локального workflow не подтверждает внешние проверки.

## Журнал решений

2026-11-12: использованы aiogram 3.31.0 / aiogram_dialog 2.6.0, выбранные по реальным
upstream tags. В API-источниках старый mattermost-api-reference заменён текущим
каталогом api основного репозитория. Выбрана отдельная разработка native boundary
вместо копирования aggregate Telegram exports; причина подтверждена inventory.
Предварительная поддержка tooling: Python 3.11–3.14; 3.15/OS matrix SDK подтверждается
CI-05 после появления runtime dependencies и протокольных тестов.
