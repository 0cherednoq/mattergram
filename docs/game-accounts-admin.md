# Админка игровых аккаунтов

Управление app-scoped пользователями вынесено в reusable
`mattergram.contrib.admin_users.AdminUsersPlugin`. Accounts, imports, checks и push rules находятся в
`mattergram.contrib.game_accounts_admin`. Composer keyboard только отправляет точный текст
`Пользователи` и не является зависимостью или источником авторизации users plugin.

Пакет разделён на models, ports, repository, service, import/check orchestration, getters, actions,
dialog, keyboard и router. Единственный запускаемый пример —
[`examples/game_accounts_admin.py`](../examples/game_accounts_admin.py). Каждая глобальная команда создаёт
новый bot-post; локальные inline-действия редактируют только post своего экрана. Реальные хранилище,
чтение файла, checker и доставка уведомлений намеренно передаются снаружи.

Для локальной проверки на owned Mattermost 12.0.2 используйте этот же пример. Runner заменяет только `/game-admin` и `/app-users` выбранного fixture, привязывает точного тестового администратора и держит прикладные данные в памяти процесса. Повторяемый `--operator-username <username>` явно добавляет начальных операторов при каждом запуске; локальный deploy использует `reaper`, поэтому его роль не теряется вместе с process-local repository. Его `DemoAccountVerifier` проверяет только UI-flow и не является интеграцией с игровой платформой.

## Подключить Game Admin

Импортируйте feature из публичного package; файл в `examples` является только composition root:

```python
from mattergram.contrib.game_accounts_admin import (
    GameAccountsAdminService,
    GameAdminJobs,
    build_admin_dialog,
    register_admin_panel,
)
from mattergram_dialog import DialogRegistry, setup_dialogs

jobs = GameAdminJobs(service, job_repository, job_queue)
registry = DialogRegistry(build_admin_dialog(service, jobs=jobs))

# Создайте один DialogRuntime с registry, затем подключите его к Dispatcher.
setup_dialogs(dispatcher, runtime)
register_admin_panel(dispatcher, runtime, service)
```

`service`, `job_repository` и `job_queue` создаёт composition root. `build_admin_dialog(...)` только объявляет окна и связывает getters/actions. `register_admin_panel(...)` подключает module-level router и DI context; он не создаёт service или runtime.

Текущий `MemoryAdminRepository` хранит пользователей, аккаунты, imports, checks и push routes только в памяти. Он подходит для тестов и локального demo, но не является production repository. Публичный durable protocol для этих доменных данных пока не реализован.

## Карта интерфейса

Глобальная навигация вынесена из поста в постоянный `ComposerKeyboard` над полем ввода. Раскладка `3 / 2 / 2` содержит обзор и шесть рабочих разделов:

- аккаунты и импорт;
- пользователи и push-настройки;
- история импортов и история проверок.

Клавиатура и её установка описываются декларативно:

```python
from mattergram.contrib.game_accounts_admin import build_admin_keyboard

direct = await bot.create_direct_channel(admin_user_id)
keyboard = build_admin_keyboard()
await bot.set_composer_keyboard(direct.id, keyboard)
```

Mattermost не имеет отдельной поверхности «окно бота»: личный чат с ботом является direct channel между пользователем и bot account. Поэтому keyboard технически хранится по ID этого DM, но виден только в личке конкретного app user с ботом и не устанавливается в общий team channel. `AdminUserOnboardingPlugin` создаёт такой DM при выдаче доступа, отправляет приветствие и ставит клавиатуру роли: administrator получает полное меню `3 / 2 / 2`, operator — шесть игровых разделов `3 / 1 / 2` без кнопки «Пользователи». Пользователь без enabled app-role получает ноль keyboard actions. На каждом startup bridge сверяет owner-aware inventory с repository и удаляет собственные устаревшие меню, поэтому отозванный доступ не оставляет видимую, но нерабочую кнопку после перезапуска.

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

План и фактический прогресс рефакторинга, включая Python plugin architecture, reusable
`AdminUsersPlugin`, разделение Router/dialog actions и реализованный background progress:
[game-admin-routing-refactor.md](game-admin-routing-refactor.md).

Локальные действия уже созданного post — фильтры, pager, формы и `Назад` — продолжают редактировать только этот post, потому что они являются inline callback-навигацией, а не reply keyboard. Следующее нажатие постоянной клавиатуры снова создаёт новую пару `user message → bot post`.

В посте остаются только локальные действия текущего экрана: фильтры, pager, выбор записи, проверка, редактирование, открытие формы и path-aware `Назад`. Поэтому keyboard не дублируется в каждом состоянии и не зависит от версии action-разметки уже опубликованного поста.

Кнопка импорта сначала открывает короткий post-экран с подтверждением. Это ограничение протокола Mattermost: обычное posted-сообщение от composer keyboard не содержит интерактивного `trigger_id`, необходимого для открытия native form; следующий локальный клик уже открывает форму корректно.

Список аккаунтов реализован отдельным reusable-компонентом `AccountList` из
`game_accounts_admin.mattermost_ui`. Компонент хранит filter/sort/page в dialog context конкретного
actor, а `GameAccountsAdminService.query_accounts(...)` применяет их до формирования payload и
возвращает не более 10 записей. Данные отображаются настоящей Markdown-таблицей с колонками
`ID`, `Логин`, `Креды`, `Cookies`, `Страна`; длинные значения ограничены до формирования сообщения.
Выбор аккаунта в page-local `static_select` не меняет dialog state: callback редактирует тот же post и
добавляет вложенную карточку сразу под pager/select. Остальные списки пока
используют `PagedTable`, `Filter`, `PagePager` и `PageItemSelect`. Формы добавления и редактирования
открываются как native forms. Максимальная глубина — два перехода ниже главного окна, `Назад`
всегда занимает один клик.

Accounts window использует один внешний `PostContainer(border=True, background="gray",
accent_color="primary")`. Заголовок, `AccountList`, выбранный `AccountCard` со вложенным `AccountCheck` и
навигация находятся в его `content`, поэтому Mattermost показывает одну общую рамку и одну accent
полосу на весь post. Список и навигация остаются borderless, а проверка является вложенным
`collapsible` внутри выбранного аккаунта, а не отдельной top-level карточкой. Её заголовок не повторяет
ID аккаунта: принадлежность выражена структурой карточки. Это Blocks-only
presentation: legacy clients сохраняют читаемый message/attachments fallback.

Карточка реализована отдельным reusable-компонентом `AccountCard` из того же пакета
`mattermost_ui`. Внешний `collapsible` при выборе раскрыт, а вложенный раздел Cookies изначально
закрыт. Карточка показывает ID, login, окружение, состояние проверки, количество и домены cookies,
но не публикует password или действующие cookie values. Вместо password выводится только индикатор
наличия; для cookies в UI передаются безопасные метаданные `domain` и `name`. Это ограничение
закреплено в `GameAccountsAdminService.account_card()`, поэтому секреты не пересекают UI boundary и
не могут случайно попасть в `props`, callback context или логи renderer. Полное раскрытие секретов
должно оставаться отдельным защищённым flow, а не режимом этой карточки.

Локальные действия карточки разделены по назначению: `Проверить` находится внизу карточки на
leading edge, а компактная группа `✏️` / `🗑️` — на trailing edge шапки выбранного аккаунта. Обе icon-only кнопки имеют
native tooltip и полный текстовый label в legacy fallback. Редактирование открывает native
`FormWindow` и после сохранения возвращает пользователя к тому же Accounts post. Корзина остаётся
последней, имеет стиль `danger` и открывает отдельную форму подтверждения с обязательным checkbox.
Перед удалением сервис повторно проверяет роль `admin/operator`; после записи очищается выбранная
карточка. Во время активной проверки destructive кнопка disabled.

Локальная кнопка `Проверить` использует signed action `account_check` и повторно проверяет права на
backend. Она ставит отдельный `CheckAccountJob` в общий Game Admin job runtime; `DialogRef` сохраняет
координаты текущего post, поэтому pending, running и итоговый статус обновляют тот же post через
`manager.show(ShowMode.EDIT)`. Вложенный `AccountCheck` показывает stage, время начала/завершения, фактические
current/total и итоговые valid/invalid/errors. Если total ещё неизвестен, интерфейс не показывает
вымышленный процент.

Repository обязан атомарно отклонять второй активный job с тем же single-flight key
`actor/account`; process-local adapter реализует этот контракт под lock. Пока проверка активна,
кнопка повторного запуска disabled, а `AccountCard` добавляет к вложенной проверке отдельное действие отмены. Состояния
UI — `pending`, `running`, `success`, `failed`, `cancelled`; ошибка выводится понятным текстом без
exception detail и секретов. После terminal state новый запуск разрешён.

| Сценарий | Клики от persistent keyboard |
|---|---:|
| Открыть историю импортов или проверок | 1 |
| Показать карточку под таблицей | 2: открыть список и выбрать аккаунт |
| Проверить или изменить выбранный аккаунт | 3 |
| Импортировать JSONL | 3 |
| Проверить только что импортированные аккаунты | 4 |
| Добавить пользователя или push-маршрут | 3 |

В `AccountList` зарегистрировано 10 локальных actions: три фильтра, один sort select, стабильный pager
`5` и один account select. Глобальный ряд содержит три действия: проверить все, импортировать,
назад. Без выбранной записи получается 13 actions, с карточкой — 16, во время активной проверки с
кнопкой отмены — 17; все варианты ниже лимита 50. Фильтр и сортировка сохраняются при переходе по страницам;
смена фильтра или сортировки возвращает пользователя на первую страницу. Недоступные pager-кнопки
остаются на месте и получают `disabled=true`.

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
jobs = GameAdminJobs(service, job_repository, job_queue)
registry = DialogRegistry(build_admin_dialog(service, jobs=jobs))
setup_dialogs(dispatcher, runtime)
register_admin_panel(dispatcher, runtime, service, command="game-admin")
```

Начального администратора нужно создать явно. В runtime нет административного fallback. Сервис не позволяет выключить или понизить последнего активного администратора. `admin` управляет membership и ролями. `operator` работает с игровыми аккаунтами, импортом, проверками, историями и push-настройками, но не имеет permissions `AdminUsersPlugin`: раздел «Пользователи» скрыт, ручной текст и `/app-users` получают отказ. `viewer` имеет read-only доступ к списку пользователей в текущей встроенной policy. Все проверки выполняются service-side, независимо от видимости кнопок.

Глобальные `/game-admin` и keyboard-text handlers имеют декларацию
`flags={"required_roles": ("admin", "operator")}` и защищены core `RBACMiddleware`. Ветка управления
пользователями дополнительно требует `admin` в service, а `AdminUsersPlugin` использует собственный
role-aware entry. Общий механизм описан в [Декларативном доступе по ролям](access-control.md).

## Обязательные адаптеры

При создании `GameAccountsAdminService` передайте:

- `AccountVerifier` — выполняет реальную проверку логина, пароля и cookie для конкретной игры;
- `ImportFileReader` — по Mattermost file ID читает ровно тот файл, который загрузил администратор;
- `PushDispatcher` — отправляет событие в точный channel ID или direct-user ID;
- `TeamUserDirectory` — без кэша возвращает активных участников ровно одной
  настроенной Mattermost-команды;
- список разрешённых игр и явно созданный repository.

Для обычной Mattermost-доставки библиотека содержит `MattermostPushDispatcher`:

```python
from mattergram.contrib.game_accounts_admin import MattermostPushDispatcher

pushes = MattermostPushDispatcher(
    bot,
    mentions=("oncall-user",),  # без @
)
```

Для channel rule dispatcher добавляет явные `@mention`, отправляет обычный non-silent root post и
тем самым создаёт стандартное Mattermost notification event. Звук остаётся настройкой Web/Desktop
клиента получателя и не может быть принудительно включён bot API. Опциональный `MessagePriority`
добавляет Important/Urgent metadata, но его доступность зависит от server edition/configuration.
Direct rule сначала создаёт или находит DM через bot account.

Bot может создать alert channel обычным runtime credential, если его Mattermost role имеет
`create_public_channel` или `create_private_channel`:

```python
channel = await bot.create_channel(
    team_id=TEAM_ID,
    name="game-alerts",
    display_name="Game alerts",
    purpose="Автоматические события игровых аккаунтов",
)
```

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

## Фоновые импорты и batch-проверки

Импорт, проверка всех аккаунтов и проверка конкретного импорта используют feature-specific jobs. Callback авторизует actor, создаёт job, сохраняет `DialogRef`, переключает post в `AdminPanel.job_progress` и возвращает управление. Worker публикует `queued/running/completed/failed/cancelled` snapshots с монотонной revision.

```python
from mattergram.contrib.game_accounts_admin import (
    CoalescingDialogJobUpdater,
    GameAdminJobRunner,
)
from mattergram_dialog import CoalescingDialogUpdater

updates = CoalescingDialogUpdater(runtime, interval=1.0)
runner = GameAdminJobRunner(
    service,
    job_repository,
    CoalescingDialogJobUpdater(updates),
)

# Queue consumer получает job_id из durable transport.
finished = await runner.run(job_id)
```

`GameAdminJobRepository` обязан применять compare-and-swap по `revision`, запрещать изменение terminal snapshot и хранить terminal history независимо от post. `GameAdminJobQueue` является producer-side портом; способ получения, lease, acknowledgement и retry определяет production transport. Повторная доставка допустима только вместе с repository contract, который не позволяет двум workers применить одну и ту же revision.

`EphemeralMemoryGameAdminJobRepository` и `EphemeralMemoryGameAdminJobQueue` существуют только для тестов и owned local demo. Они теряют задания при перезапуске и не подходят для нескольких процессов. Local demo запускает отдельный process-local consumer через `run_ephemeral_job_worker(...)`; не копируйте этот adapter в production.

Отмена queued job становится terminal немедленно. Running job проверяет cancellation между bounded единицами работы. Если post удалён или `DialogRef` относится к старому intent, runner прекращает UI updates, но завершает операцию и сохраняет terminal history. Он не создаёт новый post автоматически.

## Проверки и масштабирование

Доступны три команды: проверить выбранный аккаунт, все аккаунты или аккаунты конкретного импорта. Проверка одного выбранного аккаунта выполняется inline. Batch-проверки идут через `GameAdminJobs`. Результат внешнего checker применяется только если запись всё ещё существует и credentials не изменились во время запроса.

Post-часть работает на legacy actions Mattermost 11.1, но точное расположение рядов гарантируется только подтверждённым Blocks-профилем. `FileInput` требует подтверждённую capability `forms.file` целевой версии. Базовый `/game-admin` ранее выполнен на owned Mattermost 12.0.2 с HTTP 200 и созданием интерактивного Blocks-поста. Новый job-progress flow проверен contract-тестами и type checking, но после подключения worker не перезапускался на live stand; не считайте Web/Desktop/mobile поведение этого окна подтверждённым.
