# Plugin architecture и рефакторинг Game Admin

**Статус:** этапы 0–8 реализованы и contract-verified.
**Основной читатель:** maintainer, который будет добавлять plugin architecture, первый `AdminUsersPlugin` и рефакторить Game Admin.  
**Тип документа:** план реализации с точными контрактами и критериями приёмки.

## Какой результат нужен

Game Admin должен сохранить текущую семантику Mattermost, но стать проще для чтения, тестирования и расширения:

- переиспользуемые функции бота подключаются как явные Python plugins без копирования handlers между проектами;
- первым таким модулем становится `AdminUsersPlugin` для app-scoped пользователей, ролей и прав;
- slash-команды и сообщения от постоянной клавиатуры обрабатываются обычным глобальным `Router`;
- локальная навигация окна остаётся декларативной через `SwitchTo`, `Back`, `OpenForm` и другие widgets;
- прикладные действия кнопок находятся в отдельном controller, а не внутри большого замыкания;
- обработчик кнопки, которому нужен только `DialogManager`, не обязан принимать и удалять неиспользуемые `event` и `button`;
- нажатие постоянной клавиатуры создаёт новый post, а inline-действие меняет только post, которому принадлежит;
- длительный импорт или batch-проверка возвращает управление быстро и обновляет progress по `DialogRef` с монотонной revision.

План не меняет уже проверенную модель доступа, формат импорта, хранение секретов, Mattermost plugin protocol постоянной клавиатуры или native form lifecycle. Notifications, runtime settings, S3, AI, jobs и audit перечислены как будущие направления, но не входят в первый implementation slice.

## Что есть сейчас

Текущий package разделяет внешние события, dialog runtime и прикладные collaborators:

- `game_admin_router` объявлен на уровне модуля;
- `open_game_admin` обрабатывает `/game-admin`;
- `respond_to_game_admin_keyboard` принимает точный текст reply-кнопки и вызывает `DialogRuntime.respond`;
- `register_admin_panel` связывает `GameAdminHandlerContext` через `Dispatcher.workflow_data` и подключает router;
- `setup_dialogs` регистрирует один общий обработчик только для данных, которые распознаёт `DialogActions.matches`, поэтому чужие callbacks продолжают проходить по router tree.

Монолит из example устранён. Текущий размер package-модулей после этапа 6:

| Модуль | Строк | Ответственность |
| --- | ---: | --- |
| `dialog.py` | 537 | Только декларация окон, widgets и связывание getters/actions |
| `service.py` | 366 | Публичный authorization facade и короткие application use cases |
| `imports.py` | 127 | Bounded JSONL parsing, validation и persistence orchestration |
| `checks.py` | 105 | Snapshot-safe verification, redaction и aggregate history |
| `actions.py` / `getters.py` | 151 / 66 | Form/button side effects отдельно от render-only reads |
| `router.py` | 86 | Entry handlers, DI context и подключение router |

`Button` принимает полную callback-сигнатуру и короткую manager-only форму:

```python
async def on_click(event: Any, button: Button, manager: DialogManager) -> None:
    ...

async def simple_click(manager: DialogManager) -> None:
    ...
```

Существующие полные callbacks остаются совместимыми. Game Admin callbacks, которым нужен только
manager, используют короткую форму без фиктивных `event`, `button` и `del event, button`.

## Граница между Router и Dialog

Не нужно превращать каждую inline-кнопку в глобальный `@router.callback_query` handler. События принадлежат разным уровням:

| Событие | Владелец | Результат |
| --- | --- | --- |
| `/game-admin` | `game_admin_router.slash_command` | Старт dialog entry point |
| Текст `Аккаунты`, `Импорт` и другие reply-команды | `game_admin_router.message` | Новый bot-post через `DialogRuntime.respond` |
| `SwitchTo`, `Back`, pager, filter, row select | Декларативный widget | Переход или обновление текущего post |
| Прикладная inline-кнопка `Проверить` | `Button(on_click=...)` | Вызов controller и rebuild текущего post |
| Native form submit/cancel | `DialogRuntime` и callback окна | Validation, переход и rebuild текущего post |
| Callback, не принадлежащий dialog | Другой application router | Не должен поглощаться `setup_dialogs` |

Такое разделение соответствует подходу aiogram и aiogram_dialog. Обычные входные события регистрируются в `Router`, а callback конкретного widget передаётся этому widget через `on_click`. Plugin-модель mmpy_bot полезна как пример lifecycle, metadata и dependency injection. Копировать `@listen_to` не нужно: второй routing DSL будет конкурировать с существующими `Router`, filters и middleware.

## Два разных вида plugins

Термин plugin используется для двух независимых механизмов:

| Вид | Где выполняется | Для чего нужен | Первый пример |
| --- | --- | --- | --- |
| Mattermost server/webapp plugin | В Mattermost server и Web/Desktop client | Расширение платформы, UI surface, server hooks, REST и plugin KV | `com.mattergram.menu` |
| Mattergram application plugin | В Python-процессе бота | Переиспользуемые routers, dialogs, services и lifecycle | `AdminUsersPlugin` |
| Provider/adapter | В Python-процессе или внешнем сервисе | Реализация инфраструктурного interface | repository, S3, AI, secret store |

Текущий keyboard уже является Mattermost server+webapp plugin, описанным в [`plugins/mattergram-menu/README.md`](../plugins/mattergram-menu/README.md). Python объявляет `ComposerKeyboard`, `Bot.set_composer_keyboard` вызывает typed plugin route, Go-компонент хранит state в plugin KV и публикует WebSocket revision, а Webapp-компонент рисует кнопки. Клик создаёт обычный user post, который получает штатный `Dispatcher`.

Новый Python plugin layer не заменяет этот механизм и не устанавливает Mattermost plugin из runtime бота. Установка bundle остаётся отдельной system-admin операцией. Python companion может проверить наличие и совместимую версию `com.mattergram.menu`, собрать contributions и завершить запуск явной ошибкой при отсутствии обязательной capability.

## Минимальная plugin architecture

Первый slice добавляет только contracts, которые нужны `AdminUsersPlugin` и явной сборке приложения:

```python
@dataclass(frozen=True, slots=True)
class PluginManifest:
    id: str
    version: str
    requires: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()


class MattergramPlugin(Protocol):
    manifest: PluginManifest

    def setup(self, builder: PluginBuilder) -> None:
        ...

    async def startup(self, context: PluginContext) -> None:
        ...

    async def shutdown(self, context: PluginContext) -> None:
        ...
```

`PluginBuilder` предоставляет ограниченные операции регистрации:

- `include_router(router)`;
- `include_dialog(dialog)`;
- `add_commands(*specs)`;
- `provide(interface, value)` для typed DI;
- `add_health_check(check)`.

`PluginManager` выполняет setup всех plugins до запуска Dispatcher, проверяет уникальные plugin IDs, dependency order, command conflicts, provider conflicts и keyboard button IDs. Startup выполняется по dependency order, shutdown в обратном порядке. Частично запущенный набор обязан корректно остановить уже запущенные plugins при ошибке следующего plugin.

На первом этапе не добавлять:

- автоматический поиск пакетов через Python entry points;
- hot install, unload или reload plugins в работающем процессе;
- отдельный event bus, migration engine или settings registry;
- второй набор decorators вместо `Router`;
- автоматическую установку Mattermost server plugins;
- произвольный service locator, доступный всему прикладному коду.

Plugins подключаются явным списком. Это делает состав приложения видимым в code review и не запускает код случайно установленного Python package:

```python
app = MattergramApp(
    bot=bot,
    dispatcher=dispatcher,
    plugins=[
        AdminUsersPlugin(...),
        ComposerKeyboardPlugin(...),  # optional UI adapter
    ],
)
```

## Целевая структура пакетов

Core contracts живут в библиотеке, первый reusable plugin поставляется как bundled contrib module, а старый example остаётся совместимым facade на один migration cycle:

```text
src/mattergram/
├── plugins/
│   ├── api.py                   # PluginManifest, MattergramPlugin
│   ├── builder.py               # разрешённые registration operations
│   ├── manager.py               # validation, ordering, lifecycle
│   ├── context.py               # immutable runtime context
└── contrib/
    ├── composer_keyboard/
    │   └── plugin.py            # optional UI lifecycle, без feature routing
    └── admin_users/
        ├── plugin.py            # AdminUsersPlugin
        ├── models.py            # AdminUser, RoleSpec, PermissionSpec
        ├── repository.py        # protocol, без production DB implementation
        ├── service.py           # access rules и use cases
        ├── states.py
        ├── getters.py
        ├── actions.py
        ├── dialog.py
        └── router.py

examples/
├── game_accounts_admin.py       # временные совместимые re-exports
└── game_accounts_admin_bot.py   # composition root local demo
```

Это не означает механическое создание множества файлов по числу классов. Граница должна следовать ответственности:

- `plugin.py` объявляет manifest и регистрирует contributions;
- `router.py` переводит внешнее событие в entry state;
- `dialog.py` описывает интерфейс;
- `getters.py` готовит render data;
- `actions.py` выполняет прикладное действие и меняет dialog data/state;
- `service.py` применяет access rules, работает с repository и внешними adapters.

`mattergram.contrib.admin_users` можно вынести в отдельный distribution `mattergram-plugin-admin-users` позже, когда понадобится независимый release cycle. Первый slice не должен одновременно решать packaging нескольких distributions.

`examples/game_accounts_admin.py` следует оставить на один migration cycle. Он re-export-ит текущие публичные имена, чтобы существующие тесты, документация и runner не сломались одним переносом.

## Первый plugin: AdminUsersPlugin

`AdminUsersPlugin` управляет доступом к конкретному приложению. Он не управляет Mattermost system/team roles и не требует system-admin token в runtime.

### Ответственность

Plugin предоставляет:

- список app users с Mattermost ID, актуальным username, ролью и enabled state;
- добавление пользователя только из активных участников одной configured Mattermost team;
- повторную проверку выбранного Mattermost user ID при form submit;
- изменение роли и enabled state;
- запрет выключения, удаления или понижения последнего активного app administrator;
- server-side permission checks для каждого read/write use case;
- декларативные list/add/edit окна;
- router entry point для exact text и slash command, не зависящий от keyboard;
- repository protocol, чтобы проекты выбирали PostgreSQL, SQLAlchemy или другое durable storage;
- audit sink protocol, который первый slice может реализовать memory recorder только в tests.

Удаление пользователя лучше выразить как disable. Hard delete не входит в первый slice, потому что он ломает ссылки audit/history. Повторное добавление disabled пользователя должно реактивировать существующую запись после проверки team membership.

### Роли и permissions

Роли являются app-scoped declarations, а не строками с неявным смыслом:

```python
roles = (
    RoleSpec(
        id="admin",
        title="Администратор",
        permissions=frozenset({
            "admin_users.view",
            "admin_users.manage",
            "admin_users.assign_roles",
        }),
        protects_last_member=True,
    ),
    RoleSpec(
        id="operator",
        title="Оператор",
        permissions=frozenset({"admin_users.view"}),
    ),
    RoleSpec(id="viewer", title="Наблюдатель"),
)
```

`AdminUsersService` проверяет permission для actor перед repository read/write. Скрытая кнопка не считается защитой. Неизвестную роль, permission или Mattermost user ID нужно отклонять до записи.

Initial administrator задаётся явно через bootstrap/configuration и создаётся идемпотентно. Нельзя использовать первого вошедшего пользователя, username `admin`, Mattermost system-admin role или пустой repository как автоматический administrative fallback.

### Публичная конфигурация

```python
OPEN_USERS_TEXT = "Пользователи"

users = AdminUsersPlugin(
    repository=PostgresAdminUserRepository(pool),
    directory=MattermostTeamDirectory(bot, team_id=TEAM_ID),
    roles=roles,
    initial_admin_ids=(OWNER_USER_ID,),
    command="app-users",
    messages=(OPEN_USERS_TEXT,),
)
```

Bot account должен состоять в configured team и иметь обычное `view_members`. Setup credential может добавить bot в team во время provisioning, но не передаётся plugin runtime.

### Граница с keyboard plugin

`AdminUsersPlugin` не импортирует keyboard types, не регистрирует button IDs, не вызывает `set_composer_keyboard` и не проверяет наличие `com.mattergram.menu`. Он обрабатывает обычное входящее `Message` с exact text в разрешённом bot DM.

Keyboard является необязательным UI adapter. Он делает только одно: публикует от имени пользователя обычный Mattermost post с настроенным текстом. Button ID остаётся внутренней частью UI schema и не попадает в Dispatcher.

```text
keyboard click ─┐
manual typing ──┼─> ordinary Mattermost post ─> Message ─> Router ─> AdminUsersService
another UI ─────┘
```

Composition root связывает обе стороны локальной константой. Зависимость направлена от приложения к двум независимым modules:

```python
OPEN_USERS_TEXT = "Пользователи"

users = AdminUsersPlugin(
    repository=repository,
    directory=directory,
    roles=roles,
    initial_admin_ids=(OWNER_USER_ID,),
    messages=(OPEN_USERS_TEXT,),
)

keyboard = ComposerKeyboardPlugin(
    targets=admin_direct_channels,
    keyboard=ComposerKeyboard(
        title="Администрирование",
        rows=((
            ComposerButton(
                id="adminUsers",
                text="Пользователи",
                message=OPEN_USERS_TEXT,
            ),
        ),),
    ),
)
```

Если keyboard plugin не установлен или выключен, пользователь всё ещё может вручную написать `Пользователи`, а slash entry продолжает работать. Установка keyboard не должна менять или перерегистрировать handlers `AdminUsersPlugin`.

Поскольку тот же текст может вручную отправить любой участник, наличие кнопки не является доказательством права доступа. Router ограничивает surface точным bot DM, а `AdminUsersService` независимо проверяет actor permissions перед каждым read/write.

`ComposerKeyboardPlugin` получает целую готовую `ComposerKeyboard` от composition root и один владеет `set_composer_keyboard` для заданного channel. Feature plugins не собирают клавиатуру по частям и не знают, существует ли этот UI adapter.

### Что не входит в AdminUsersPlugin v1

- создание, удаление или блокировка Mattermost accounts;
- изменение Mattermost system/team roles;
- LDAP/SCIM lifecycle;
- dynamic role editor;
- per-channel ACL editor;
- notification delivery;
- редактирование `.env` или secrets;
- production repository implementation внутри core package.

## Публичный callback API кнопки

### Поддерживаемые сигнатуры

`Button` поддерживает ровно две явные формы callback:

```python
async def simple_click(manager: DialogManager) -> None:
    ...

async def full_click(
    event: CallbackQuery,
    button: Button,
    manager: DialogManager,
) -> None:
    ...
```

Полная форма остаётся совместимой с aiogram_dialog и нужна, когда обработчик читает actor callback, отвечает ephemeral ACK или использует metadata widget. Короткая форма является ergonomic extension `mattergram_dialog` для большинства прикладных действий.

Не добавлять произвольную диспетчеризацию по любому числу аргументов. В частности, формы с нулём, двумя или четырьмя параметрами и `*args` должны отклоняться.

### Валидация и вызов

`Button.__init__` один раз анализирует callback через `inspect.signature` и сохраняет готовый adapter.
Форма callback на каждом клике повторно не определяется.

Требования к adapter:

1. Поддержать обычные функции, bound methods, `functools.partial` и callable objects, если их итоговая сигнатура однозначна.
2. Разрешить sync и async результат так же, как текущий `Button`.
3. При неподдерживаемой сигнатуре немедленно поднять `TypeError` с именем callback и двумя допустимыми формами.
4. Не ловить `TypeError` из тела прикладного callback и не пробовать вызвать его повторно с другой сигнатурой. Иначе реальная ошибка бизнес-кода превратится в двойной side effect.
5. Сохранить текущий порядок: проверить одноразовый action token, вызвать callback, затем один раз перестроить окно.

Предлагаемые typing contracts:

```python
from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias

ButtonResult: TypeAlias = Awaitable[None] | None
SimpleButtonClick: TypeAlias = Callable[[DialogManager], ButtonResult]
FullButtonClick: TypeAlias = Callable[[Any, "Button", DialogManager], ButtonResult]
ButtonClick: TypeAlias = SimpleButtonClick | FullButtonClick
```

Публично экспортировать только те aliases, которые действительно полезны авторам приложений. Внутренний compiled adapter не должен становиться частью public API.

## Как будет выглядеть Game Admin

Navigation-only callbacks нужно удалить полностью и заменить widgets. Например, `open_user_edit` не нужен:

```python
OpenForm(
    Const("Изменить"),
    id="adminUserEdit",
    state=AdminPanel.user_edit,
    when=selected_user_visible,
)
```

Прикладные действия находятся в controller с явной зависимостью:

```python
from dataclasses import dataclass

from mattergram_dialog.api.protocols.manager import DialogManager


@dataclass(frozen=True, slots=True)
class GameAdminActions:
    service: GameAccountsAdminService

    async def refresh_home(self, manager: DialogManager) -> None:
        manager.dialog_data["home_refreshed_at"] = now_label()

    async def check_selected(self, manager: DialogManager) -> None:
        account_id = manager.dialog_data.get("selected_account_id")
        if not isinstance(account_id, str):
            return
        run = await self.service.check_account(manager.actor_id, account_id)
        manager.dialog_data["check_summary"] = format_check_summary(run)

```

Imperative transition допустим только когда target действительно выбирается по результату
прикладного действия. Если переход статичен, использовать `SwitchTo`, `Back` или `OpenForm`.

Dialog builder получает готовые collaborators и остаётся декларативным:

```python
def build_admin_dialog(
    *,
    actions: GameAdminActions,
    getters: GameAdminGetters,
) -> Dialog:
    return Dialog(
        Window(
            Format("{check_summary}"),
            Button(
                Const("Проверить выбранный"),
                id="adminCheckSelected",
                on_click=actions.check_selected,
                when=selected_account_visible,
            ),
            OpenForm(
                Const("Изменить"),
                id="adminAccountEdit",
                state=AdminPanel.account_edit,
                when=selected_account_visible,
            ),
            state=AdminPanel.accounts,
            getter=getters.accounts,
        ),
        # Остальные окна объявляются тем же способом.
    )
```

Сборка feature остаётся явной:

```python
service = GameAccountsAdminService(...)
actions = GameAdminActions(service)
getters = GameAdminGetters(service)
dialog = build_admin_dialog(actions=actions, getters=getters)

registry.include(dialog)
register_admin_panel(dispatcher, runtime, service)
```

Не хранить `service`, `runtime` или bot token в изменяемых module globals. Module-level router допустим для одного Dispatcher на процесс. Если появится подтверждённая потребность подключать feature к нескольким Dispatcher в одном процессе, добавить router factory отдельной задачей.

## Progress импорта и проверок

Текущий inline batch подходит только для малого набора. Длительная операция должна работать так:

1. Callback проверяет права и входные данные.
2. Сервис создаёт durable job и возвращает `job_id`.
3. Callback сохраняет `job_id`, начальные counters и `DialogRef`, затем быстро завершает обработку.
4. Worker публикует snapshots через `CoalescingDialogUpdater` и `DialogRuntime.update(ref, data, revision=n)`.
5. `revision` монотонно растёт. Устаревший snapshot становится no-op.
6. Terminal snapshot содержит `succeeded`, `failed` или `cancelled` и агрегированные counters. Credentials и исходные строки импорта в него не входят.
7. Если пользователь уже открыл новый reply-command post и старый `DialogRef` устарел, worker сохраняет terminal result в history и прекращает UI updates для старого post.

Использовать существующие [`Progress`](../examples/dialog_progress.py), `DialogRef`, `CoalescingDialogUpdater` и revision guard. Не создавать второй несовместимый progress runtime внутри Game Admin.

## Нужны ли новые presets

На этом этапе не добавлять application-specific `AdminCrudWindow` или `GameAccountPreset` в библиотеку. Один пример не доказывает стабильный общий контракт.

После рефакторинга оценить три узких кандидата:

- helper для точного mapping `reply text -> State`, если тот же pattern появится ещё в двух приложениях;
- общий job-progress composition поверх уже существующих `Progress`, `DialogRef` и `CoalescingDialogUpdater`;
- reusable selected-row action group для `PagedTable`, если совпадут selection, visibility и reset semantics.

Preset переносится в публичную библиотеку только после второго независимого использования и поведенческих тестов. До этого он остаётся локальным helper в contrib plugin.

## Что отложено после AdminUsersPlugin

Архитектура должна оставить место для следующих modules, но первый slice не добавляет их classes, protocols или таблицы БД:

| Будущий модуль | Назначение | Почему отложен |
| --- | --- | --- |
| `NotificationsPlugin` | Destinations, typed events, templates, retry/outbox | Сначала нужен второй реальный plugin и подтверждённый event contract |
| `SettingsPlugin` | Runtime settings, validation, secret rotation metadata | `.env` не является runtime store, нужен отдельный trust model |
| `JobsPlugin` | Durable jobs, cancellation и progress | Требует выбранной queue/storage стратегии |
| `AuditPlugin` | Общий immutable audit trail | В v1 достаточно узкого `AuditSink` у AdminUsers |
| S3/AI integrations | `ObjectStorage` и `AIClient` providers | Это providers, а не UI plugins; contracts следует выводить из реальных consumers |
| Dynamic plugin discovery | Python entry points и allowlist | Явный список безопаснее и достаточен для первого slice |

Secrets нельзя читать обратно через административный UI. Когда появится `SettingsPlugin`, secret fields должны быть write-only и храниться через внешний secret store либо encrypted repository. Изменение process environment и запись `.env` из бота не поддерживаются.

## Этапы реализации

Фактический прогресс на 16 ноября 2026:

- этап 0: добавлены contract tests manifests, dependency graph, lifecycle rollback, conflicts и
  обязательных capabilities;
- этап 1: `mattergram.plugins` реализует явный `PluginManager` поверх существующих
  `Dispatcher`, `DialogRegistry`, command declarations и named DI;
- этап 2: `mattergram.contrib.composer_keyboard.ComposerKeyboardPlugin` устанавливает целую
  declaration только в проверенные direct channels, подтверждает read-back и восстанавливает
  прежнее состояние при rollback/shutdown;
- local Game Admin composition root использует новый keyboard plugin, но живой stand после этой
  замены ещё не запускался;
- этап 3: `mattergram.contrib.admin_users.AdminUsersPlugin` реализует app-scoped roles/permissions,
  repository/directory protocols, explicit initial admins, last-admin invariant, list/add/edit dialog,
  slash и exact-DM-text entry без импорта keyboard API. Game Admin подключает plugin через временные
  adapters к общей in-memory repository;
- этап 4: `Button` принимает полную и manager-only callback-сигнатуры, компилирует adapter при
  создании widget, отклоняет неоднозначные формы до первого клика и не повторяет callback при
  `TypeError` из прикладного кода;
- этап 5: edit-переходы пользователя, push-маршрута и аккаунта выражены через `OpenForm` с
  прежними widget IDs и states; оставшиеся refresh/check callbacks принимают только manager;
- этап 6: оставшийся feature разделён на package-модули models/ports/repository/service,
  import/check orchestration, getters/actions/dialog/keyboard/router; старый example сохранил
  совместимые re-exports;
- этап 7: добавлены durable repository/queue ports, явно ephemeral local adapters, typed jobs для
  import/check, cooperative cancellation, terminal history и progress через существующие
  `CoalescingDialogUpdater`/`DialogRef`; local demo запускает process-local worker;
- этап 8: public guides обновлены фактическими imports и job contracts; repository skill
  `mattergram-bot-ux` разделяет архитектуру, components, verification и background jobs по отдельным
  references и не объявляет общий `JobsPlugin` либо другие отложенные APIs реализованными.

### Этап 0. Зафиксировать contracts

До runtime-кода добавить contract tests для:

- manifest validation и уникального plugin ID;
- dependency ordering и cycle detection;
- rollback startup lifecycle;
- router/dialog/command/provider conflicts;
- отсутствие platform-specific keyboard contracts в generic `PluginBuilder`;
- отсутствующей обязательной Mattermost capability.

Новые contracts не объявляются публичными до прохождения tests и документационного drift check.

### Этап 1. Минимальный PluginManager

Реализовать `PluginManifest`, `MattergramPlugin`, `PluginBuilder`, `PluginContext` и `PluginManager`. Подключение остаётся явным, без entry-point discovery.

`PluginManager` использует существующие `Dispatcher.include_router`, dialog registry, provisioning command specs и DI. Он не создаёт собственный routing engine.

### Этап 2. ComposerKeyboardPlugin

Обернуть существующую typed keyboard интеграцию в application plugin:

1. Принять готовую `ComposerKeyboard` только от composition root.
2. Проверить schema и duplicate button IDs до HTTP write.
3. Проверить наличие `com.mattergram.menu`.
4. Установить keyboard только в явно разрешённые direct channels.
5. Не регистрировать feature handlers и не интерпретировать отправляемый текст.

Native Go/Webapp bundle и его protocol не переписывать.

Contract test обязан доказать, что `AdminUsersPlugin` работает при полном отсутствии `ComposerKeyboardPlugin`, если пользователь вручную отправляет тот же exact text.

### Этап 3. AdminUsersPlugin

Извлечь из Game Admin только users/roles область:

1. Models, role/permission declarations и repository protocol.
2. `AdminUsersService` с server-side authorization и last-admin invariant.
3. Uncached `TeamUserDirectory` и повторная validation на submit.
4. Getters, actions, dialog и router.
5. Slash entry и exact reply-keyboard entry.
6. Memory repository только для tests/local demo.
7. Compatibility adapter для текущего `GameAccountsAdminService` на migration cycle.

Не переносить вместе с пользователями accounts, imports, checks, notifications или push rules.

### Этап 4. Короткая сигнатура Button

Изменить `src/mattergram_dialog/widgets/message.py`:

1. Добавить строгие typing aliases.
2. Компилировать adapter при создании `Button`.
3. Сохранить полную callback-сигнатуру без изменений.
4. Добавить manager-only форму.
5. Обновить docstrings и public exports, если aliases выбраны публичными.

Проверки:

- async и sync manager-only callback;
- полная legacy callback;
- bound method, partial и callable object;
- отказ для 0, 2, 4 и variadic positional parameters;
- `TypeError` внутри callback пробрасывается и callback вызывается ровно один раз;
- action token остаётся одноразовым.

### Этап 5. Удаление лишних imperative callbacks

В `build_admin_dialog` заменить статические переходы на `SwitchTo`, `Back` и `OpenForm`. Оставить callbacks только для записи данных, вызова service или условного перехода.

Проверки:

- прежние widget IDs и state names остаются стабильными;
- сценарий формы `Пользователи -> Добавить -> закрыть -> Назад` не получает outdated action;
- повторный клик старой одноразовой action корректно отклоняется.

### Этап 6. Разделение оставшегося Game Admin

После извлечения users plugin разделить оставшиеся accounts/imports/checks models, service, getters, actions, dialog и router малыми патчами. После каждого переноса оставить совместимый re-export в `examples/game_accounts_admin.py` и прогнать связанные тесты.

Не объединять механический перенос файлов с изменением поведения service. Сначала сохранить green tests, затем отдельно декомпозировать import/check orchestration.

### Этап 7. Background jobs

Ввести job repository/queue adapter для production implementation. Memory queue допустима только в local demo и должна быть явно помечена как недолговечная.

Добавить contract tests на monotonic revisions, terminal history, cancellation и устаревший `DialogRef`.

Этот этап не означает реализацию общего `JobsPlugin`. Сначала проверить job contract на импорте и batch-проверках, затем решать, достаточно ли evidence для отдельного reusable plugin.

### Этап 8. Документация и skill

Статус: завершён. `game-accounts-admin.md`, `dialog-ui-design.md`, `application-plugins.md`,
documentation manifest/evidence и repository skill синхронизированы с contract-verified API.
Новый job-progress flow после worker integration не объявляется live-verified до повторного прогона
на Mattermost stand.

После стабилизации API:

- обновить [`game-accounts-admin.md`](game-accounts-admin.md) реальным, проверенным кодом;
- обновить [`dialog-ui-design.md`](dialog-ui-design.md) правилом `Router entry events, declarative widget navigation, controller business actions`;
- добавить отдельный public guide по подключению `MattergramPlugin` и `AdminUsersPlugin` после стабилизации imports;
- обновить `mattergram-dialog-ux` skill только после появления тестов и финальных import paths;
- не добавлять в skill планируемые APIs как уже доступные.

## Совместимость и инварианты

Рефакторинг считается совместимым, только если сохраняются все условия:

- существующая полная `Button` callback-сигнатура работает без изменений;
- widget IDs, form callback IDs и state names не меняются без отдельной migration note;
- slash и exact-text handlers не поглощают посторонние сообщения;
- generic dialog handler не поглощает callbacks с чужим data format;
- каждый принятый reply-keyboard text создаёт новый bot-post;
- inline widget меняет только свой post;
- keyboard остаётся только в direct channel администратора с bot account;
- passwords и cookie values не попадают в render data, progress, errors или history;
- runtime не получает administrative fallback;
- один router instance не подключается молча ко второму Dispatcher;
- plugin setup не выполняет network writes, startup side effects начинаются только после полной validation;
- plugin IDs, commands, provider bindings и keyboard routes не конфликтуют;
- `AdminUsersPlugin` не меняет Mattermost system/team roles;
- `AdminUsersPlugin` не отключает и не понижает последнего активного administrator;
- `AdminUsersPlugin` не импортирует composer keyboard API и не получает button callback;
- только composition root и `ComposerKeyboardPlugin` знают о keyboard UI;
- один configured owner вызывает `set_composer_keyboard` для конкретного channel.

## Критерии готовности

- [x] Plugin contracts и lifecycle реализованы поверх существующих Router/Dispatcher/Registry.
- [x] Dependency cycles и registration conflicts отклоняются до startup side effects.
- [x] Partial startup корректно вызывает shutdown уже запущенных plugins.
- [x] `ComposerKeyboardPlugin` принимает целую declaration от composition root и выполняет один keyboard write на target.
- [x] `AdminUsersPlugin` работает без установленного keyboard при ручной отправке exact text.
- [x] Button ID не участвует в feature routing, handler получает только обычный `Message`.
- [x] `AdminUsersPlugin` подключается к новому боту без копирования Game Admin handlers.
- [x] Roles и permissions являются явными app-scoped declarations.
- [x] Последний enabled administrator защищён service-level invariant.
- [x] Team membership повторно проверяется на submit обычным runtime bot token.
- [x] `AdminUsersPlugin` не импортирует composer keyboard API и не получает button callback.
- [x] `Button` принимает manager-only и полную callback-сигнатуры.
- [x] Неподдерживаемые сигнатуры отклоняются при создании widget.
- [x] В Game Admin нет `del event, button` для `Button` callbacks.
- [x] Статическая навигация выражена widgets, а не ручными manager calls.
- [x] `build_admin_dialog` содержит декларацию, но не service implementation.
- [x] Getters и actions тестируются отдельно без запуска HTTP ingress.
- [x] Старые imports из `examples.game_accounts_admin` продолжают работать на migration cycle.
- [x] Unit, integration, Ruff, strict mypy и documentation drift проходят для этапов 0–6.
- [ ] Local browser smoke подтверждает новый post для reply keyboard и same-post update для inline action.
- [ ] Background progress отдельно проверяет monotonic revisions и terminal history.
- [x] `STATUS.md` различает planned, implemented и live-verified состояние.
- [x] Notifications, settings, S3, AI и dynamic discovery не выданы за реализованные plugins.

## Источники архитектурных решений

- [aiogram_dialog README: Dialog подключается в router tree, затем вызывается setup](https://github.com/Tishka17/aiogram_dialog/blob/develop/README.md?plain=1)
- [aiogram_dialog simple example: module-level callbacks передаются widgets через on_click](https://github.com/Tishka17/aiogram_dialog/blob/develop/example/simple.py)
- [aiogram-dialog-menus skill](https://github.com/brianmok888/aiogram-dialog-menus-skill/blob/main/SKILL.md), вторичный workflow reference, не runtime source of truth
- [mmpy_bot plugins](https://github.com/attzonko/mmpy_bot/blob/main/docs/plugins.rst), reference для plugin lifecycle и dependency injection
- [Mattermost plugins overview](https://developers.mattermost.com/integrate/plugins/overview), граница server и webapp components
- [Mattermost plugin SDK](https://developers.mattermost.com/integrate/plugins/), hooks, UI extensions и custom REST endpoints
- [Mattermost plugin management and security](https://docs.mattermost.com/developers/integrate/plugins/using-and-managing-plugins), installation, signing и trust boundary
- [Текущий composer keyboard plugin](composer-keyboards.md)
- [`mattergram_dialog` porting boundary](dialog-porting.md)
- [Навигация и списки](dialog-navigation.md)
- [Progress background job](../examples/dialog_progress.py)

