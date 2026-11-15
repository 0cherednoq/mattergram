# Native UI schemas и compiler

Реализованы все объявленные типы native Form и Blocks,
legacy button/select и rich attachments. Compiler подключён к `Bot.send_message`.
Backend open_dialog/submissions/parser уже реализован: [native-forms.md](native-forms.md).
Slack/Adaptive Cards input conversion реализован: [точный контракт](ui-input-formats.md).
Реальный web 12.0.2 проверен на записанных сценариях: [отчёт](reports/ui-web-12.0.2.json).
Дополнительно проверены [bounds/timezone/labels/legacy](reports/ui-web-12.0.2-additional.json)
и [web 11.11](reports/ui-web-11.11.0.json). Это отдельные профили с записанными ограничениями.
На [web 11.7](reports/ui-web-11.7.0.json) проверены применимые поля, transitions,
date/time и legacy callbacks; обнаруженное ограничение route IDs проверяется до POST.
Полный E5 ещё открыт: остальные server/client profiles остаются задачей E5-03.
Обработчики готовых меню используют уже проверенный E4 HTTP ingress.

## Отправить меню

`examples/ui_menu_bot.py` показывает posted → signed native action → callback.
Ему нужны runtime bot token, доступ к каналу, настроенный callback route,
доверенный transport peer и постоянный секретный HMAC key, переданные через environment.
Setup/admin token пример не использует. Пример проверяется mypy; его полный запуск
как отдельного приложения пока не заявлен. Сам compiler/HTTP callback путь выполнен
на реальном Mattermost 11.1 в `tools.verify_http_stand`.

```python
from mattergram.ui.actions import Actions, Button, External, ActionContext

await bot.send_message(
    channel_id, "Choose an action",
    actions=Actions(items=(Button(
        name="Approve", style="primary",
        action=External(url=callback_url, data=request_action.pack()),
    ),)),
    action_context=ActionContext(signer, channel_id, user_id=actor_id),
)
```

`External.data` требует ActionContext и подписывается в момент компиляции.
`External.context` — явный native escape hatch для другого integration endpoint;
он не становится автоматически доверенным context SDK. Смешивать data и context нельзя.
Binding post_id, если он нужен, требует сначала получить ID созданного поста и затем
явно обновить actions. Если post_id не задан, подпись не ограничивает действие этим
конкретным постом. Сетевая аутентификация и бизнес-идемпотентность по-прежнему обязательны.

Attachments передаются через `attachments=(Attachment(...),)`; поддержаны native
author/title/fields/image/thumb/footer/color/timestamp/fallback и actions.
При создании None означает отсутствие компонента. API редактирования с различием
UNSET/None и shortcut-параметры ещё не добавлены.

## Выбрать профиль Blocks

`mattergram.ui.blocks` содержит Text, Image, Divider, Button, StaticSelect,
Container, Collapsible, ColumnSet и Column. Передавайте их в `blocks=`.
Button/StaticSelect получают `External` либо `OpenURL` как action. Compiler сам
создаёт допустимый технический action_id; callback data в него не подставляется.
Для известного ID используйте `action_id=…`. Для markdown `mmaction://id`
передайте соответствующий `markdown_actions={id: action}`.

`compile_post_ui` согласует дерево и registry вместе с legacy attachments/actions,
проверяет дубли IDs, missing/unused markdown entries, расположение Column,
ограничения вложенности/числа blocks/text/query. Скрытая потеря layout при fallback
не допускается: неподдержанный backend даёт ошибку до HTTP.

Нужен явный `UIProfile(server_version=…, client=…, client_features=frozenset(…),
client_evidence=…, mm_blocks_enabled=True)`. Список client_features означает
декларацию приложения с указанным доказательством; SDK не определяет версию всех
клиентов по версии сервера. По умолчанию client support и flag неизвестны,
поэтому advanced UI не отправляется. `overrides` разрешает явно выбранный эксперимент
при unknown, но не отменяет known unsupported server fields, выключенный flag или
права Mattermost. Поддержка всех реальных клиентов пока не заявлена.

Четыре точных server profiles привязаны к primary source commits/hashes в
`manifests/ui-profile.json`: 11.1.0, 11.7.0, 11.11.0, 12.0.2. Это protocol schema
evidence, а не четыре выполненных UI стенда. На 11.1/11.7 Blocks отклоняются.

## Объявить Form

`mattergram.ui.forms` содержит Form и TextInput, TextArea, Select, MultiSelect,
Checkbox, Radio, CheckboxGroup, CheckboxMatrix, DatePicker, DateTimePicker,
FileInput, DialogButton и Collapsible. `Form(...).to_wire(profile)` выдаёт native
dialog JSON. Поля задаются через `elements=(…, …)`; runtime также принимает list,
но typed signatures используют tuples. `label` сериализуется как `display_name`.
Неизвестные kwargs и неприменимые поля отклоняются.

MultiSelect остаётся wire type select с multiselect=true. Его typed default_values
превращаются в подтверждённый для defaults comma format; это **не правило парсинга
любого submission**. Checkbox default сериализуется как native строка true/false.
CheckboxMatrix получает MatrixConfig и typed row → columns defaults; при сериализации
используется native `row:col1,col2;row2:col1`, с проверкой delimiters и row selection.
FileInput не выдумывает связь между отдельным элементом и общим submission.file_ids.

DatePicker сохраняет относительные выражения как строки, не вычисляя их в timezone
процесса. DateTimePicker принимает явный offset у абсолютного default, проверяет
делитель 1440 для time_interval и выравнивание default. location_timezone передаёт
IANA key серверу; проверяется синтаксис, наличие зоны остаётся серверной проверкой.

Serializer учитывает три структуры datetime config: старые top-level параметры
11.1; mixed top-level bounds и вложенные config с allow_manual_time_entry в 11.7;
современный datetime_config с manual_time_entry в 11.11/12.0.2. В 12 не отправляются
удалённые поля. Лимит title — 24 UTF-8 bytes у первых трёх профилей и 80 у 12.0.2.
Nested field names проверяются глобально; Collapsible ограничен тремя уровнями.

JSON schemas в `contracts/ui-*-declarations.schema.json` описывают **Python declarations**,
а не готовый wire конкретной версии. Type-specific serializer и profile выбирают wire.
UI model defaults могут содержать введённые значения/секреты: не логируйте dumps.

Legacy ID проверяется отдельно от Blocks action ID. Маршрут 11.1/11.7 принимает
только ASCII letters/digits; 11.11/12 также принимает `_` и `-`. Для расширенного
ID передайте явный `ui_profile=UIProfile("11.11.0")` или соответствующий 12 profile
в `Bot.send_message`; при ручной сериализации — `to_wire(profile=profile)`.
Без profile доступны алфавитно-цифровые IDs либо отсутствующий ID, который выдаст
сам Mattermost. Неподдерживаемый ID отклоняется до HTTP-записи и не переименовывается.
Это свойство server route; клиентские Blocks flags для legacy ID не требуются.
`ui-legacy-declarations.schema.json` описывает Attachment/Actions declarations;
выбор версии проверяет runtime compiler.

## Проверить

```sh
uv run --locked python -m tools.fetch_ui_references
uv run --locked python -m tools.fetch_ui_references --verify
uv run --locked pytest tests/unit/test_ui.py tests/integration/test_native_http.py
uv run --locked pytest tests/unit/test_ui_importers.py
uv run --locked python -m tools.verify_http_stand
```

Reference downloader проверяет SHA-256 и не перезаписывает изменённые файлы.
Source хранится только в ignored build/ui-refs, не исполняется и не входит в пакет.
Первоначальные проверки schemas сохранены в `docs/reports/ui-schemas.json`.
Backend forms: `docs/reports/forms-core.json`; input adapters: `docs/reports/ui-input-core.json`.
