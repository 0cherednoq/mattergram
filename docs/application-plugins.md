# Application plugins Mattergram

`mattergram.plugins` собирает переиспользуемые Python-модули поверх существующих `Router`,
`DialogRegistry`, деклараций provisioning и named DI. Это не Mattermost server plugin и не второй
routing engine: plugin объявляет обычные routers, dialogs, commands и providers, а
`PluginManager` проверяет и подключает их одним composition step.

## Объявить plugin

```python
from mattergram import Router
from mattergram.plugins import PluginBuilder, PluginContext, PluginManifest

router = Router(name="audit")


class AuditPlugin:
    manifest = PluginManifest(id="example.audit", version="1.0.0")

    def setup(self, builder: PluginBuilder) -> None:
        builder.include_router(router)

    async def startup(self, context: PluginContext) -> None:
        # Runtime resources and network checks begin only after validation.
        pass

    async def shutdown(self, context: PluginContext) -> None:
        pass
```

`setup()` должен только объявлять contributions. В нём нельзя выполнять HTTP-запросы, создавать
background tasks или менять внешнее состояние. Все manifests и contributions проверяются до их
подключения к приложению. `startup()` выполняется в dependency order, `shutdown()` — в обратном;
ошибка частичного startup останавливает уже запущенные plugins.

Dependencies задаются стабильными ID:

```python
manifest = PluginManifest(
    id="example.reports",
    version="1.0.0",
    requires=("example.audit",),
)
```

Отсутствующая dependency, цикл, повторный plugin ID, одинаковый router, dialog state, command
trigger или DI provider отклоняются до startup side effects.

## Собрать приложение

```python
from mattergram import Bot, Dispatcher
from mattergram.plugins import PluginManager
from mattergram_dialog import DialogRegistry

bot = Bot(token=BOT_TOKEN, base_url=MATTERMOST_URL)
dispatcher = Dispatcher()
registry = DialogRegistry()

plugins = PluginManager(
    bot=bot,
    dispatcher=dispatcher,
    dialog_registry=registry,
    plugins=(AuditPlugin(),),
)
context = plugins.setup()
```

`setup()` регистрирует lifecycle manager в `Dispatcher`, поэтому отдельные ручные вызовы startup и
shutdown при обычном `dispatcher.start(...)` не нужны. Registry после setup можно передать в один
общий `DialogRuntime`; plugin layer не создаёт свой dialog runtime и не перехватывает чужие events.

Явные команды plugins доступны как `context.command_manifest`. Его нужно передавать тому же
provisioning composition root, который регистрирует остальные slash commands.

## Передать typed provider в handlers

`ProviderKey[T]` связывает статический тип с именем стандартного Mattergram DI:

```python
from mattergram.plugins import ProviderKey

AUDIT_SERVICE: ProviderKey[AuditService] = ProviderKey("audit_service")


def setup(self, builder: PluginBuilder) -> None:
    builder.provide(AUDIT_SERVICE, self.service)
```

После успешного setup handler может принять параметр `audit_service`, а lifecycle hook — получить
то же значение через `context.provider(AUDIT_SERVICE)`. Системные DI names и уже занятые application
names нельзя заменить.

## Подключить composer keyboard

`ComposerKeyboardPlugin` — необязательный UI adapter для установленного Mattermost plugin
`com.mattergram.menu`:

```python
from mattergram import ComposerButton, ComposerKeyboard
from mattergram.contrib.composer_keyboard import (
    MENU_CAPABILITY,
    ComposerKeyboardPlugin,
)

OPEN_USERS_TEXT = "Пользователи"

keyboard = ComposerKeyboardPlugin(
    targets=(ADMIN_BOT_DM_CHANNEL_ID,),
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

plugins = PluginManager(
    bot=bot,
    dispatcher=dispatcher,
    dialog_registry=registry,
    plugins=(keyboard,),
    capabilities=(MENU_CAPABILITY,),
)
plugins.setup()
```

Target проверяется через Mattermost как direct channel до первой записи. Startup читает прежнее
состояние, устанавливает готовую declaration и подтверждает read-back; rollback и normal shutdown
восстанавливают прежнюю keyboard. Указанная capability — явное deployment expectation, а реальный
GET к plugin route доказывает, что server plugin доступен runtime bot.

Keyboard adapter не регистрирует handlers и не интерпретирует button ID. Нажатие создаёт обычный
user post. Feature router должен независимо обработать точный текст и снова проверить права actor;
ручной ввод того же текста обязан работать без keyboard plugin.

## Текущие границы

- Plugins перечисляются явно; Python entry-point discovery, hot reload и runtime install отсутствуют.
- Mattermost server/webapp bundle устанавливает system administrator отдельно от bot runtime.
- `AdminUsersPlugin`, settings, notifications, jobs, S3 и AI plugins ещё не входят в реализованный API.
- Наличие кнопки не является авторизацией.
- Production code не должен хранить secrets в provider values, render data или health errors.

Полный план следующего среза: [Plugin architecture и рефакторинг Game Admin](game-admin-routing-refactor.md).
