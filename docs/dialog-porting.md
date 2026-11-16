# mattergram_dialog

Практическое руководство по `SwitchTo`, path-aware `Back`, нескольким таблицам, фильтрам и независимой пагинации: [dialog-navigation.md](dialog-navigation.md).

`mattergram_dialog` — отдельный типизированный runtime для многошаговых Mattermost-сценариев.
E8-02 сохраняет проверенные идеи `aiogram_dialog` (контексты, стек, переходы, getters и
text widgets), но использует нативные посты и формы Mattermost.

Источник адаптации: `aiogram_dialog 2.6.0`, commit
`2c2fca8eab9532fba9c13f31be7404ec872c8a88`, Apache-2.0. Manifest содержит 40 явных
решений: 27 адаптированных модулей и 13 исключений. Все адаптации воспроизводятся из
закреплённых JSON patches; итоговый audit имеет 0 findings.

## Быстрый пример

Полные декларации и сценарии переноса проверяются в `tests/ported/`.

```python
from mattergram.fsm.state import State, StatesGroup
from mattergram_dialog import Dialog, FormWindow, Window
from mattergram_dialog.widgets.form import Checkbox, TextInput
from mattergram_dialog.widgets.message import OpenForm
from mattergram_dialog.widgets.text import Const

class Settings(StatesGroup):
    menu = State()
    form = State()

settings = Dialog(
    Window(
        Const("Settings"),
        OpenForm(Const("Edit"), id="edit", state=Settings.form),
        state=Settings.menu,
    ),
    FormWindow(
        TextInput(id="title", label="Title"),
        Checkbox(id="enabled", label="Enabled"),
        title=Const("Settings"),
        submit_text=Const("Save"),
        state=Settings.form,
    ),
)
```

`DialogRegistry` проверяет уникальность состояний. `DialogStorage` хранит stack/context в
существующем `BaseStorage`, а блокировка берётся из `BaseEventIsolation`; один и тот же код
работает с `MemoryStorage` и `RedisStorage`.

```python
storage = DialogStorage(fsm_storage, event_isolation)
key = storage.key(
    instance_id=instance_id,
    bot_id=bot_id,
    channel_id=event.channel_id,
    actor_id=event.user_id,
    thread_id=event.thread_id,
)
renderer = MattermostRenderer(bot, event.channel_id, open_form=event.open_dialog)

async with DialogManager(storage, key, registry, renderer, actor_id=event.user_id, event=event) as manager:
    await manager.start(Settings.menu)
```

Изменения внутри `async with` сохраняются только при успешном выходе. Исключение откатывает
stack/context; isolation не даёт двум workers потерять read-modify-write обновление.

## Post и form backends

- `Window` создаёт или редактирует пост через `MattermostRenderer`.
- `FormWindow` строит существующий `mattergram.ui.forms.Form` и открывает его через
  `SlashCommand.open_dialog()` или `CallbackQuery.open_dialog()`.
- `TextInput`, `Checkbox` и `DateTimePicker` — тонкие декларативные оболочки над нативными
  form fields; wire-сериализация остаётся в `mattergram.ui.forms`.
- `OpenFormActions` компилирует `OpenForm` в подписанные native post actions. Ему нужны
  callback URL и существующий `ActionContext`. Callback handler извлекает целевое состояние
  через `OpenFormActions.target_from_event(event)`, создаёт renderer с
  `open_form=event.open_dialog` и вызывает `manager.switch_to(..., show_mode=ShowMode.FORM)`.

Если `Window` содержит controls, но renderer не получил `actions_factory`, он поднимает
`RendererUnavailable`: неработающая кнопка не отправляется молча. `ShowMode.EDIT` заменяет
текст и `props` одного поста вместе, поэтому старые controls не остаются на новой странице.
Наборы больше пяти controls автоматически разбиваются на несколько native attachments.

Submit handler формы вызывается через `await manager.submit(event)`. Он может читать
`event.values`, менять `dialog_data` и выполнять `switch_to`, `next`, `back` или `done`.

## HTTP ingress binding

`DialogRuntime` создаёт одинаковый storage key для slash, callback и form events. После
объявления registry подключите только принадлежащие dialog runtime события:

```python
runtime = DialogRuntime(
    bot,
    DialogStorage(storage, isolation),
    DialogRegistry(settings),
    instance_id=bot.instance_id,
    bot_id=bot_id,
    callback_url=callback_base + "/mattergram/callback",
    callback_signer=signer,
)
setup_dialogs(dispatcher, runtime)

@dispatcher.slash_command(Command("settings"))
async def settings_command(event: SlashCommand) -> None:
    await runtime.start(event, Settings.menu)
    await event.answer()
```

Signed transition включает target, intent, source state и ID исходного поста. Повтор кнопки
от старого окна получает ephemeral ответ и не выполняет переход повторно. `FormWindow →
FormWindow` использует `DialogSubmission.next()`, а cancel возвращает context в окно,
открывшее форму. Filters runtime пропускают чужой signed callback к следующему Router handler.

E8-03 подтвердил этот binding на owned Mattermost 11.1: один post прошёл ветки Orders и
Incidents через общее Help-окно, path-aware Back вернул правильную ветку, pager/filter
обновили карточку, native form открылась через actor WebSocket, а submit отредактировал тот
же post в confirmation. Terminal replay выполнил business effect один раз; независимый
Redis client восстановил финальный state/value.
[Публичный отчёт](reports/dialog-scenario.json), receipt —
`manifests/dialog-scenario-tests.json`.

## Сохранённые и исключённые semantics

Поддерживаются `start`, `switch_to`, `next`, `back`, `done`, вложенный result,
`dialog_data`, `start_data`, `Const`, `Format`, `Multi`, `Or`, `Case`, `Progress`, `Button`,
`ActionTable`, динамические form defaults, `DialogRef`, monotonic background
`DialogRuntime.update`, `CoalescingDialogUpdater`, расширяемый `WidgetAction`/
`WidgetActionResult` protocol, `when`, getters,
actor allowlist и bounded stack.

Не переносятся Telegram inline/reply keyboards, message input, media/file IDs, markup,
link preview и background chat loading. Открытая Mattermost modal не имеет протокола
произвольного фонового редактирования; runtime этого не обещает. Native lookup/refresh,
validation и modal lifecycle остаются в `mattergram.http.dialogs` и HTTP events.

## Воспроизводимость и проверки

```powershell
python -m tools.build_manifest --check `
  --plan manifests/dialog-plan.json `
  --inventory build/inventory/aiogram_dialog.json `
  --output manifests/dialog.json
python -m tools.extract_core `
  --manifest manifests/dialog.json `
  --output build/port-staging/dialog-e8-02-final
python -m tools.audit_port `
  --manifest manifests/dialog.json `
  --stage build/port-staging/dialog-e8-02-final `
  --report build/reports/dialog-audit.json
python -m tools.verify_dialog_extraction
```

`manifests/dialog-ported-tests.json` закрепляет девять выбранных donor suites и нативную
behavioral suite. Тесты проверяют transitions, nested result, rollback, конкурентную
изоляцию, Memory/real Redis, text widgets, документированный API и typed Form output.
