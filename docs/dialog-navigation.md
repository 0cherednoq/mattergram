# Навигация и списки в `mattergram_dialog`

Это руководство для разработчика бота, которому нужна одна редактируемая карточка Mattermost с несколькими окнами, формами, фильтрами и независимой пагинацией.

Проверяемая fixture навигации: [`tests/fixtures/dialog_navigation_app.py`](../tests/fixtures/dialog_navigation_app.py).
Правила структуры, бюджета кликов, выбора пагинации, таблиц и полей формы собраны в
[`docs/dialog-ui-design.md`](dialog-ui-design.md).

## Короткий путь

Опишите состояния, соберите `Dialog` из окон и подключите переходы как обычные виджеты:

```python
from mattergram.fsm.state import State, StatesGroup
from mattergram_dialog import Back, Const, Dialog, SwitchTo, Window


class Menu(StatesGroup):
    home = State()
    orders = State()
    help = State()
    done = State()


menu = Dialog(
    Window(
        Const("Главное меню"),
        SwitchTo(Const("Заказы"), id="orders", state=Menu.orders),
        state=Menu.home,
    ),
    Window(
        Const("Заказы"),
        SwitchTo(Const("Помощь"), id="ordersHelp", state=Menu.help),
        Back(Const("Назад"), id="ordersBack"),
        state=Menu.orders,
    ),
    Window(
        Const("Помощь"),
        Back(Const("Назад"), id="helpBack"),
        state=Menu.help,
    ),
)
```

`DialogRuntime` создаёт пост при `start()`. Последующие `SwitchTo`, `Next`, `Back`, `Pager` и `Filter` заменяют текст и controls этого же поста. Runtime проверяет intent, исходное состояние и ID поста, поэтому повтор старой кнопки не выполняет переход второй раз.

## Как работает `Back`

`Back` хранит фактический путь пользователя в текущем dialog context. Например:

```text
Главное меню -> Заказы -> Помощь -> Back -> Заказы
Главное меню -> Инциденты -> Помощь -> Back -> Инциденты
```

Общее окно `Помощь` не знает, какая ветка его открыла. История хранится рядом с `widget_data`, ограничена 100 переходами и сохраняется через выбранный `DialogStorage`. Поэтому поведение одинаково для `MemoryStorage` и `RedisStorage` и переживает обработку события другим worker.

Если история пуста, `Back` совместимо возвращается к предыдущему объявленному окну. Кнопка не рендерится, когда возвращаться некуда.

## Логические ряды кнопок

Используйте `ButtonGrid` и `ActionRow`, когда количество кнопок в каждом ряду должно быть частью декларации, а не случайным результатом ширины окна:

```python
ButtonGrid(
    ActionRow(campaigns, new_campaign),
    ActionRow(pools, proxy_groups),
    ActionRow(test_email),
    ActionRow(nodes, sites),
    title="Управление",
)
```

Форма `2/2/1/2` сохраняется в render plan. На профиле Mattermost Blocks каждый `ActionRow` компилируется в отдельный `ColumnSet` с равными растягиваемыми колонками внутри одного bordered container. В legacy attachments Mattermost 11.1 нет поля для явного разрыва ряда: действия остаются в правильном порядке и в одной карточке, но клиент переносит их по доступной ширине.

Внутри `DataBlock` каждый непосредственный управляющий виджет является отдельным логическим рядом. `Filter(...)` автоматически размещается над табличным содержимым и выделяет активное значение цветом без изменения подписи; pager располагается под таблицей. Если выбор строки должен стоять рядом с пагинацией, используйте `ActionRow(PagePager(...), PageItemSelect(...))` — это один нижний ряд с пятью кнопками и native dropdown. Пара `detail_title`/`detail` у `PageItemSelect` раскрывает выбранную запись вложенной карточкой сразу под этим рядом, не открывая ещё одно окно.

## Несколько таблиц в одном окне

Каждый `PagedList` или `PagedTable` имеет собственный `id`. Страница, выбранный фильтр и размер результата хранятся под этим ID, поэтому списки не сбрасывают друг друга. Помещайте таблицу, её `Filter` и pager в один `DataBlock`: Mattermost отображает соседние attachments как отдельные карточки, поэтому вынесенные вниз controls теряют визуальную связь с объектом.

```python
Window(
    Const("## Отчёт"),
    DataBlock(
        "Заказы",
        PagedTable(
            MarkdownColumn("Заказ", "id", max_chars=12),
            MarkdownColumn("Статус", "status", max_chars=12),
            id="orderRows",
            items="orders",
            page_size=10,
            filter_by="status",
            page_label="Страница {page}/{pages}",
        ),
        Filter(
            id="orderFilter",
            target="orderRows",
            options={"Все": None, "Открытые": "open", "Закрытые": "closed"},
        ),
        ActionRow(
            PagePager(id="orderPager", target="orderRows"),
            PageItemSelect(
                id="orderSelect",
                target="orderRows",
                item_id="id",
                item_label="id",
                selection_key="selected_order_id",
                placeholder="Выбрать заказ",
                detail_title=Format("Заказ {item[id]}"),
                detail=Format("**Статус:** {item[status]}"),
            ),
        ),
    ),
    DataBlock(
        "Инциденты",
        PagedTable(
            MarkdownColumn("Инцидент", "id", max_chars=14),
            MarkdownColumn("Важность", "severity", max_chars=12),
            id="incidentRows",
            items="incidents",
            page_size=5,
            filter_by="severity",
            page_label="Страница {page}/{pages}",
        ),
        Filter(
            id="incidentFilter",
            target="incidentRows",
            options={"Все": None, "Критичные": "high", "Обычные": "low"},
        ),
        PagePager(id="incidentPager", target="incidentRows"),
    ),
    state=Menu.home,
    getter=load_report,
)
```

`items` принимает ключ из getter-данных или getter списка. `filter_by` принимает имя поля либо функцию. `Filter.options` является фиксированным allowlist: callback не может передать произвольное значение. Выбор фильтра всегда возвращает соответствующий список на первую страницу.

Окно и один `DataBlock` могут содержать больше пяти controls. Mattermost 11.1 не задаёт серверный лимит в пять legacy actions, а клиент переносит кнопки по доступной ширине. Поэтому фильтры, pager, edit и delete одного объекта остаются в одном attachment и не разбиваются на отдельные карточки.

## Формы в том же маршруте

Добавьте `OpenForm`, направленный на `FormWindow`. Открытие формы записывает исходное окно в историю. Managed `FormWindow` всегда сериализуется с `notify_on_cancel=true`: закрытие через `×`, кнопку Cancel или другой пользовательский cancel обязано вернуть callback в runtime. Runtime возвращает context назад, поэтому уже показанный пост и его подписанные действия остаются корректными. Низкоуровневый `mattergram.ui.Form` сохраняет нативный default `notify_on_cancel=false`; это не отменяет более строгий lifecycle-контракт `FormWindow`. После submit обработчик может перейти в обычное окно с `ShowMode.EDIT`, и подтверждение заменит ту же карточку:

```python
async def save(event, manager) -> None:
    manager.dialog_data["title"] = event.values["title"]
    await event.answer()
    await manager.switch_to(Menu.done, show_mode=ShowMode.EDIT)
```

## Подключение runtime

```python
action_ttl = 7 * 24 * 60 * 60
signer = CallbackSigner(..., max_ttl=action_ttl)
runtime = DialogRuntime(
    bot,
    DialogStorage(storage, isolation),
    DialogRegistry(menu),
    instance_id=bot.instance_id,
    bot_id=bot_id,
    callback_url=callback_url,
    callback_signer=signer,
    action_ttl=action_ttl,
)
setup_dialogs(dispatcher, runtime)
```

`action_ttl` задаёт срок жизни подписанных кнопок. Значение по умолчанию — 15 минут для обратной совместимости. Для долгоживущего поста настройте TTL под реальный срок работы карточки и создайте `CallbackSigner(max_ttl=...)` с не меньшим пределом. Длинный TTL не отменяет проверки actor/channel/post, текущего intent/state и отклонение повторного callback, поэтому старая кнопка после перехода всё равно не выполняется второй раз. Планируйте ротацию signing key и не делайте TTL бессрочным.

Чтобы `ButtonGrid` рендерился точными рядами, передайте в `DialogRuntime(ui_profile=...)` проверенный профиль с `client_features`, содержащим `blocks`, и `mm_blocks_enabled=True`. Без доказанного Blocks-профиля runtime использует portable legacy attachments и сохраняет логический порядок рядов без обещания точной геометрии.

Для интерактивного Blocks-поста одной поддержки рендера недостаточно: сервер должен разрешать боту заменять `mm_blocks_actions` при REST-редактировании собственного поста. При каждом переходе runtime подтверждает callback пустым ответом, дожидается завершения серверной обработки action и обновляет тот же пост через REST вместе с полным `props`. Так Mattermost не затирает новый реестр старой копией поста, и следующая пагинация или навигация остаётся рабочей. Этот путь проверен end-to-end на Mattermost 12.0.2; конкретную версию сервера всё равно нужно проверять capability-probe, а не только сравнением номера версии.

Используйте `RedisStorage` и `RedisEventIsolation`, если callbacks одного пользователя могут попасть в разные процессы. Isolation защищает весь цикл load, transition, render и save от потерянных обновлений.

## Live-обновления по WebSocket

WS-событие не является пользовательским callback и не имеет response hook для атомарного обновления исходного поста. В таком handler загрузите актуальные данные и выполните REST edit по сохранённому `last_post_id`. Передавайте новые `props` вместе с текстом, чтобы не потерять controls. Проверяемый шаблон находится в [`tests/fixtures/showcase_app.py`](../tests/fixtures/showcase_app.py) и описан в [`docs/showcase.md`](showcase.md).

## Проверенные границы

- `Back` возвращает по фактическому пути и восстанавливается из Redis.
- Две таблицы в одном окне сохраняют независимые cursor/filter states.
- Переходы и submit формы редактируют один и тот же Mattermost post.
- Устаревший или повторный callback отклоняется до изменения состояния.
- Набор из более чем пяти controls остаётся в одном объектном блоке.
- `ButtonGrid` сохраняет объявленную форму рядов; Blocks-компилятор выдаёт отдельный `ColumnSet` для каждого ряда.

Live-сценарий выполнен на owned Mattermost 11.1 через реальный HTTP callback, actor WebSocket и Redis. Его результат сохранён в [`docs/reports/dialog-navigation.json`](reports/dialog-navigation.json).

Это не полная копия всех Telegram widgets из `aiogram_dialog`. Telegram keyboards, media widgets и message-input semantics не переносятся. `mattergram_dialog` предоставляет нативные Mattermost posts, actions и forms с похожей декларативной моделью состояния.
