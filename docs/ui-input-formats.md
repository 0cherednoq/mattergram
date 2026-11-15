# Преобразовать Slack Blocks и Adaptive Cards

Разработчикам интеграций доступны `from_slack_blocks` и `from_adaptive_cards` из
`mattergram.ui`. Функции преобразуют описанное ниже подмножество JSON в tuple
native Block declarations. Дальше используется обычный `Bot.send_message` с
`blocks=`, `ui_profile=` и, для подписанных callbacks, `action_context=`.

Для отправки нужны профиль с подтверждёнными server/client capabilities,
включённый `MmBlocksEnabled`, runtime bot token и доступ к каналу.
[Native UI guide](native-ui.md) описывает профиль и action signing.
Для ввода данных используйте [native Form](native-forms.md).

## Начать с проверяемого примера

Выполните из checkout:

```sh
uv sync --locked
uv run --locked python examples/ui_input_formats.py
```

Пример работает без сети. Он компилирует оба формата и проверяет callback signature.
Ожидаются две строки `native blocks compiled; signed callback verified`.
Указанные в примере instance/bot/channel и UIProfile служат только offline-проверке.

Для отправки уже настроенным ботом:

```python
from mattergram.ui import from_slack_blocks
from mattergram.ui.actions import External

blocks = from_slack_blocks(
    [{"type": "actions", "elements": [{
        "type": "button",
        "action_id": "approve",
        "text": {"type": "plain_text", "text": "Approve"},
        "value": "request:approve:42",
    }]}],
    actions={"approve": External(
        url=callback_url, data="request:approve:42",
    )},
)
await bot.send_message(
    channel_id, "Request 42 needs approval", blocks=blocks,
    ui_profile=verified_profile, action_context=signing_context,
)
```

Текст сообщения остаётся notification fallback. Адаптеры отправляют
`props.mm_blocks` и `props.mm_blocks_actions`; исходные `props.blocks`/`props.cards`
на сервер не пересылаются. Поэтому compiler применяет те же ограничения,
подписи и gates, что и к native declarations. [Mattermost reference](https://docs.mattermost.com/developers/integrate/reference/mm-blocks)
описывает native wire и совместимые входные форматы платформы.

## Привязать действия

`actions` содержит native `External` или `OpenURL` по source action ID.
Slack использует `action_id`, Adaptive Cards использует `id`.
IDs должны соответствовать native `[A-Za-z0-9_-]{1,64}` и быть уникальны во всём
входе, включая разные cards. Callback data в ID не упаковывается.

| Source | Привязка |
| --- | --- |
| Slack button без `url` | Обязательный `External` |
| Slack static_select | Обязательный `External`; options участвуют в подписи |
| Adaptive `Action.Submit` | Обязательный `External`; in-post inputs запрещены |
| Slack button с `url`, Adaptive `Action.OpenUrl` | `OpenURL` из абсолютного HTTP(S) URL; явный binding необязателен |

При явном URL binding должны совпадать URL и тип `OpenURL`; дополнительный query
map не допускается. Отсутствующий ID допустим только для URL navigation: native
compiler создаёт технический ID. Эта navigation не реализует Slack click telemetry.

Embedded Slack `value` или строковый Adaptive `data` должны точно совпасть с
`External.data`. Объект Adaptive `data` должен точно совпасть с
`External.context`, включая типы JSON: `true` отличается от `1`.
Объекты не превращаются в подписанную строку автоматически. Для подписанной
бизнес-модели заранее используйте её строковый serializer и `External.data`.

`External.context` сохраняет явную native escape hatch семантику. Такое значение
не становится доверенной callback data SDK. SDK HTTP ingress принимает свой
подписанный context; native context предназначен для другого endpoint с собственной
аутентификацией. Неиспользованные bindings, дубли и несовпавшие data дают ошибку.

## Подмножество Slack Block Kit

Таблица перечисляет все допустимые свойства кроме обязательного `type`.
Любое другое свойство, включая `block_id`, `confirm`, `expand`, `option_groups`,
вызывает `UIConversionError`. Адаптер не реализует Slack interaction endpoint
и не восстанавливает Slack block identity.

| Source | Допустимые свойства | Native результат |
| --- | --- | --- |
| section | text, fields (1..10), accessory | Container; fields попарно в ColumnSet, нечётный остаток полной ширины |
| header | text (plain_text, одна строка) | Text с heading markup |
| markdown | text (строка mrkdwn) | Text |
| divider | нет | Divider |
| image | image_url, alt_text, title (plain_text) | Image size=large |
| actions | elements (1..25) | Horizontal Container |
| button | action_id, text (plain_text), style (primary/danger), url, value | Button и action registry |
| static_select | action_id, placeholder (plain_text), options (1..100), initial_option | StaticSelect; initial_option совпадает с полной объявленной option |

Section accessory принимает image (size=small), button или static_select.
Option содержит только `text` (plain_text) и `value` (непустая строка).
Дубликаты option values отклоняются native моделью.
Input, context, rich_text, multi-select, datepicker и другие типы в этом адаптере
не поддерживаются. Для известных native возможностей используйте native declarations.

Text objects принимают `type`, `text` и применимый `emoji` либо `verbatim` с
настоящим JSON bool. Plain text экранируется для native Markdown там, где renderer
интерпретирует Markdown; option/placeholder/title Image остаются обычными строками.
Emoji shortcodes в plain_text отклоняются: каталог и режим emoji не определяются
по версии Mattermost. Unicode emoji допустимы.

Default mrkdwn renderer преобразует обычные `*bold*`, `_italic_`, `~strike~`,
inline/fenced code, HTTP(S) links `<url|label>`, bare HTTP(S) URLs и quote lines.
Он декодирует Slack `&lt;`/`&gt;`/`&amp;`, сохраняя отличие literal entities от
настоящих angle tokens. Это ограниченная грамматика, сложные вложенные форматы
могут отклоняться. Полная эквивалентность Slack auto-linking не заявляется.
[Slack text reference](https://docs.slack.dev/reference/block-kit/composition-objects/text-object/)
различает plain_text, mrkdwn и автоматический разбор workspace references.

Slack user/channel/broadcast/date tokens, bare `@name`/`#channel`, не-HTTP links
и незакрытые tokens требуют явного `mrkdwn_renderer`. Передайте callable
`str -> str`, который получает исходный mrkdwn и возвращает native Markdown,
например с проверенным сопоставлением Slack user ID и Mattermost user ID.
Callable отвечает за смысл этого преобразования; SDK не выдумывает соответствие IDs.
Исключения callable превращаются в ошибку без текста исходного исключения.

## Подмножество Adaptive Cards

Корневой объект принимает `type="AdaptiveCard"`, обязательный `version`
от `1.0` до `1.5`, `body`, `actions` и необязательный `$schema` с HTTP(S)
`adaptivecards.io/schemas/adaptive-card.json`. Отсутствующий body означает пустой
список. Другие root properties, включая refresh/fallbackText/speak, отклоняются.

| Source | Допустимые свойства кроме type | Native результат |
| --- | --- | --- |
| TextBlock | text, обязательный wrap=true, size (Small/Default), isSubtle (bool) | Text, small/default и is_subtle |
| Container | items | Container |
| ColumnSet | columns | ColumnSet |
| Column внутри ColumnSet | items, width (auto/stretch; default stretch) | Column |
| Image | url, altText, size, style, horizontalAlignment, width, height | Image |
| ActionSet (1.2+) | actions | Horizontal Container |
| Action.Submit | id, title, data, associatedInputs, style, tooltip, isEnabled, mode | Button с External binding |
| Action.OpenUrl | id, title, url, style, tooltip, isEnabled, mode | Button с OpenURL |

TextBlock сохраняет native Markdown. Режим обрезки без `wrap=true` отвергается:
у native Text нет соответствующего поля. [Microsoft TextBlock reference](https://learn.microsoft.com/en-us/adaptive-cards/schema-explorer/text-block)
описывает wrap=false как обрезку текста.

Image size принимает Auto/Small/Medium/Large/Stretch (default Auto), style
default/person, horizontalAlignment Left/Center/Right. Width/height поддержаны
с версии 1.1 только как положительные целые строки вида `120px` (до шести цифр).
Numeric/weighted/percentage widths и другие layout properties отклоняются.
Pixels передаются в native max_width/max_height согласно выбранному протоколу;
точные размеры и перенос на клиентах требуют отдельной UI-проверки.

Action style с версии 1.2: default/positive/destructive преобразуются в
default/primary/danger. AssociatedInputs с версии 1.3 принимает auto/none,
но input elements в посте отсутствуют. Tooltip, isEnabled и mode требуют 1.5.
IsEnabled инвертируется в disabled. Mode допускает только primary.
Action titles экранируются как plain text. Action.ShowCard/Execute/ToggleVisibility,
Input.*, requires/fallback, selectAction и неизвестные свойства отклоняются.

## Разобрать ошибку

`UIConversionError` наследует ValueError и содержит `path`, `reason`.
Например `$.cards[0].body[0].wrap` указывает на отсутствие выразимого режима
переноса. Сообщение содержит authored reason и путь, без source values.
Не исправляйте ошибку удалением неизвестного поля без проверки его смысла.
Выберите native declaration либо другую форму взаимодействия.

Вход ограничен 10000 JSON values, 64 уровнями и суммарно 1 MiB UTF-8 строк.
Циклы Python, нестроковые keys и нечисловые/бесконечные JSON numbers отвергаются.
Обычный compiler дополнительно ограничивает итоговые blocks, nesting, text и registry.
Преобразование создаёт отдельные declarations; исходные JSON и action contexts
после преобразования не могут изменить скопированное дерево.

## Проверить контракт

```sh
uv run --locked pytest tests/unit/test_ui_importers.py tests/integration/test_native_http.py
uv run --locked python -m tools.fetch_ui_references --verify
```

Pinned Mattermost translator references для 11.11.0/12.0.2 записаны в
`manifests/ui-input-profile.json`; downloader проверяет их hashes в ignored build.
Реализация адаптеров authored и не копирует vendor translator source.
Она явно отвергает случаи, которые vendor renderer может молча пропустить.
Проверки покрывают native wire/registry/HTTP, а rendering web/desktop/mobile
остаётся отдельной задачей E5-03.
