# Админка игровых аккаунтов

Управление app-scoped пользователями вынесено в reusable
`mattergram.contrib.admin_users.AdminUsersPlugin`. Local demo временно делит прежнюю in-memory
repository через compatibility adapter; accounts, imports, checks и push rules остаются в
`examples/game_accounts_admin.py`. Composer keyboard только отправляет точный текст
`Пользователи` и не является зависимостью или источником авторизации users plugin.

[`examples/game_accounts_admin.py`](../examples/game_accounts_admin.py) — декларативный каркас админки с Telegram-подобной reply-клавиатурой. Каждая глобальная команда создаёт новый bot-post; локальные inline-действия редактируют только post своего экрана. Пример покрывает пользователей, push-маршруты, импорт игровых аккаунтов, проверки и обе истории. Реальные хранилище, чтение файла, checker и доставка уведомлений намеренно передаются снаружи.

Для локальной проверки на owned Mattermost 12.0.2 используйте [`examples/game_accounts_admin_bot.py`](../examples/game_accounts_admin_bot.py). Runner заменяет только `/game-admin` и `/app-users` выбранного fixture, привязывает точного тестового администратора и держит прикладные данные в памяти процесса. Его `DemoAccountVerifier` проверяет только UI-flow и не является интеграцией с игровой платформой.

## Карта интерфейса

Глобальная навигация вынесена из поста в постоянный `ComposerKeyboard` над полем ввода. Раскладка `3 / 2 / 2` содержит обзор и шесть рабочих разделов:

- аккаунты и импорт;
- пользователи и push-настройки;
- история импортов и история проверок.

Клавиатура и её установка описываются декларативно:

```python
from examples.game_accounts_admin import build_admin_keyboard

direct = await bot.create_direct_channel(admin_user_id)
keyboard = build_admin_keyboard()
await bot.set_composer_keyboard(direct.id, keyboard)
```

Mattermost не имеет отдельной поверхности «окно бота»: личный чат с ботом является direct channel между пользователем и bot account. Поэтому keyboard технически хранится по ID этого DM, но виден только в личке конкретного администратора с ботом и не устанавливается в общий team channel. Для нескольких администраторов runner создаёт отдельный DM и устанавливает ту же декларацию в каждый такой канал.

`build_admin_keyboard()` возвращает обычный `ComposerKeyboard`: каждая кнопка имеет стабильный `id`, видимый `text`, такой же обычный `message` без slash (`Аккаунты`, `Импорт` и так далее) и стиль. `register_admin_panel(...)` регистрирует native `/game-admin` для team channels и отдельный exact-text message-handler для bot DM. Handler вызывает `DialogRuntime.respond(message, state)`: каждое принятое keyboard-сообщение сбрасывает активный stack и создаёт новый bot-post. Поэтому история явно показывает отдельно доставленную пользовательскую команду и отдельно результат её обработки; предыдущий post не является точкой отказа.

Handlers объявлены глобально на модульном router в том же стиле, что и в aiogram:

```python
game_admin_router = Router(name="game-accounts-admin")

@game_admin_router.slash_command(Command("game-admin"))
async def open_game_admin(event: SlashCommand, game_admin: GameAdminHandlerContext) -> None:
    ...

@game_admin_router.message(_is_keyboard_command)
async def respond_to_game_admin_keyboard(
    event: Message,
    game_admin: GameAdminHandlerContext,
) -> None:
    ...
```

`GameAdminHandlerContext` приходит через стандартный dispatcher DI. `register_admin_panel(dispatcher, runtime, service)` только кладёт этот immutable context в `dispatcher.workflow_data` и один раз подключает `game_admin_router`; сами handlers больше не создаются внутри setup-функции и не замыкают зависимости. Обычные прикладные callback handlers также можно объявлять глобально через `@router.callback_query(...)`. Декларативные кнопки `mattergram_dialog` остаются на общем runtime callback handler, установленном `setup_dialogs(...)`.

План следующего рефакторинга, включая минимальную Python plugin architecture, первый reusable `AdminUsersPlugin`, разделение Router и dialog actions, короткую callback-сигнатуру без `del event, button`, background progress и критерии совместимости: [game-admin-routing-refactor.md](game-admin-routing-refactor.md). Документ описывает планируемое состояние и не подменяет текущий API.

Локальные действия уже созданного post — фильтры, pager, формы и `Назад` — продолжают редактировать только этот post, потому что они являются inline callback-навигацией, а не reply keyboard. Следующее нажатие постоянной клавиатуры снова создаёт новую пару `user message → bot post`.

В посте остаются только локальные действия текущего экрана: фильтры, pager, выбор записи, проверка, редактирование, открытие формы и path-aware `Назад`. Поэтому keyboard не дублируется в каждом состоянии и не зависит от версии action-разметки уже опубликованного поста.

Кнопка импорта сначала открывает короткий post-экран с подтверждением. Это ограничение протокола Mattermost: обычное posted-сообщение от composer keyboard не содержит интерактивного `trigger_id`, необходимого для открытия native form; следующий локальный клик уже открывает форму корректно.

Списки реализованы через `PagedTable`, локальный `Filter`, `PagePager` и `PageItemSelect`. Выбор строки раскрывает карточку в том же post, поэтому отдельный экран просмотра не нужен. Формы добавления и редактирования открываются как native forms. Максимальная глубина — два перехода ниже главного окна, `Назад` всегда занимает один клик.

| Сценарий | Клики от persistent keyboard |
|---|---:|
| Открыть историю импортов или проверок | 1 |
| Открыть карточку аккаунта | 2 |
| Проверить или изменить выбранный аккаунт | 3 |
| Импортировать JSONL | 3 |
| Проверить только что импортированные аккаунты | 4 |
| Добавить пользователя или push-маршрут | 3 |

В списке аккаунтов локальные ряды имеют форму `5 / 6 / 2`: пять фильтров, pager вместе с выбором строки, затем две операции выбранного аккаунта. Глобальный ряд содержит три действия: проверить все, импортировать, назад. При выбранной записи это 16 actions, что заметно ниже лимита 50 actions на post.

### Пользователи выбранной Mattermost-команды

Форма добавления не принимает Mattermost ID и username вручную. При каждом
открытии она читает активных участников одной настроенной команды без кэша,
исключает bot accounts и уже назначенных пользователей приложения и показывает
короткий select `@username — Имя Фамилия`. В submission хранится только
стабильный Mattermost `user.id`; перед записью сервис повторно читает каталог и
убеждается, что выбранный человек всё ещё доступен в этой команде. Username
берётся из ответа Mattermost, поэтому его нельзя рассинхронизировать с ID.

Каталог использует страницы:

```http
GET /api/v4/users?in_team=<team_id>&active=true&page=0&per_page=100
Authorization: Bearer <runtime-bot-token>
```

Встроенный form source `data_source="users"` ищет видимых пользователей
сервера и не является строгой границей конкретной команды. Для team-scoped
назначения используйте `Bot.get_users_in_team` и явные options, как в примере.
Для небольшого административного состава статический select, заново собранный
при открытии формы, проще dynamic lookup и не требует кэша. Для большой команды
замените его на server-side lookup с тем же `in_team` и обязательной повторной
проверкой ID на submit.

Панель работает в DM, поэтому team ID нельзя выводить из direct channel. Он
передаётся приложению явно из конфигурации/provisioning; team ID из произвольной
form submission не считается доверенным.

## Граница доступа

Вход регистрируется отдельной slash-командой и до старта dialog требует точного `Mattermost user_id` включённого администратора:

```python
dialog = build_admin_dialog(service)
registry.include(dialog)
register_admin_panel(router, runtime, service, command="game-admin")
```

Начального администратора нужно создать явно. В runtime нет административного fallback. Сервис не позволяет выключить или понизить последнего активного администратора. Роли `operator` и `viewer` сохранены для прикладной авторизации остального бота; этот административный интерфейс доступен только роли `admin`.

## Обязательные адаптеры

При создании `GameAccountsAdminService` передайте:

- `AccountVerifier` — выполняет реальную проверку логина, пароля и cookie для конкретной игры;
- `ImportFileReader` — по Mattermost file ID читает ровно тот файл, который загрузил администратор;
- `PushDispatcher` — отправляет событие в точный channel ID или direct-user ID;
- `TeamUserDirectory` — без кэша возвращает активных участников ровно одной
  настроенной Mattermost-команды;
- список разрешённых игр и явно созданный repository.

Runtime bot не должен получать system-admin роль ради user picker. Во время
provisioning setup-учётка с `add_user_to_team` добавляет bot account в нужную
команду через `POST /api/v4/teams/{team_id}/members`. После этого обычный bot
token читает участников: endpoint требует активную сессию и членство в этой
команде, но не system-admin. Стартовый probe обязан проверить доступ реальным
запросом и завершиться ошибкой, а не включать административный fallback.

Роли `admin/operator/viewer` принадлежат конкретному приложению и хранятся в
его repository. Они не изменяют Mattermost system/team roles, пароль, статус
учётной записи или членство пользователя в команде. Удаление/деактивация
пользователя в Mattermost должна отдельно приводить к отказу прикладного входа
или последующей сверке repository.

`MemoryAdminRepository` предназначен только для тестов и локального demo: данные исчезнут при перезапуске, а секреты находятся в памяти процесса. Для production замените его долговечным хранилищем с шифрованием на уровне приложения или KMS, аудитом доступа и ротацией ключей. Не записывайте пароль, cookie или исходные строки импорта в логи и историю.

Checker является внешней границей, а не заглушкой платформы. Сервис передаёт ему `AccountCredentials`, сохраняет только статус и короткое summary, удаляя из summary совпадения с текущим паролем и значениями cookie. Исключение checker'а превращается в безопасное имя класса ошибки без текста исключения.

## Формат импорта

Импорт принимает один JSONL-файл до 1 MiB и 500 непустых строк. Каждая строка независима:

```json
{"login":"alice","password":"secret","cookies":[{"domain":"game.example","name":"sid","value":"secret-cookie"}]}
```

Обязательны `login` и `password`; `cookies` — необязательный массив объектов `domain`, `name`, `value`. Пара `game + login` уникальна. Ошибка строки не отменяет корректные строки: результат становится `partial`, а история содержит только номера строк и причины, но не исходные credentials.

В карточке аккаунта показываются игра, login, статус, время последней проверки и безопасная ошибка. Пароль отображается только как «сохранён, значение скрыто». Для cookie выводятся количество и уникальные домены; имена и значения не попадают в render data. Форма редактирования не подставляет текущий пароль и cookie: пустое поле означает «оставить без изменений».

## Push-события

Один маршрут выбирает назначение (`channel` или `direct`), точный ID и любое сочетание событий:

- `import.completed`;
- `import.failed`;
- `account.check_failed`;
- `account.invalid`.

Push-текст содержит только игру/login или агрегированные счётчики. Dispatcher должен сам обеспечивать retry/idempotency в соответствии с транспортом; пример не притворяется durable delivery.

## Проверки и масштабирование

Доступны три команды: проверить выбранный аккаунт, все аккаунты или аккаунты конкретного импорта. Результат внешнего checker применяется только если запись всё ещё существует и credentials не изменились во время запроса. Для сотен аккаунтов не запускайте весь batch внутри callback: положите job в durable queue, обновляйте декларативный `Progress` через `DialogRuntime.update`, а terminal snapshot отправляйте немедленно. Текущий inline batch подходит для небольшого административного набора.

Post-часть работает на legacy actions Mattermost 11.1, но точное расположение рядов гарантируется только подтверждённым Blocks-профилем. `FileInput` требует подтверждённую capability `forms.file` целевой версии. Контракт проверен unit-тестами и type checking; `/game-admin` дополнительно выполнен на owned Mattermost 12.0.2 с HTTP 200 и созданием интерактивного Blocks-поста.
