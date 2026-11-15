# Mattergram Menu plugin

`com.mattergram.menu` добавляет постоянную клавиатуру над composer в Mattermost Web и Desktop.
Клавиатура описывается в Python через `ComposerKeyboard`, хранится по `channel_id` в plugin KV и
обновляется в открытых клиентах через WebSocket. Нажатие создаёт обычный post от текущего
пользователя; `mattergram` получает его штатным WebSocket dispatcher.

## Быстрый пример

```python
from mattergram import Bot, ComposerButton, ComposerKeyboard

keyboard = ComposerKeyboard(
    title="Игровые аккаунты",
    rows=[
        [
            ComposerButton(
                id="accounts",
                text="Аккаунты",
                message="/accounts",
                style="primary",
            ),
            ComposerButton(id="imports", text="Импорт", message="/imports"),
        ],
        [ComposerButton(id="check_all", text="Проверить все", message="/check_all")],
    ],
)

async with Bot(token=BOT_TOKEN, base_url=MATTERMOST_URL) as bot:
    await bot.set_composer_keyboard(CHANNEL_ID, keyboard)
```

Bot account должен состоять в целевом канале. Менять и удалять клавиатуру через plugin API могут
bot accounts и system admins; читать её могут только участники канала.

## Сборка и тесты

Требуются Python 3.11+, Node.js 20+ и Go 1.26.7+ с включённой загрузкой toolchain.

```powershell
cd plugins/mattergram-menu
npm test
npm run build
```

Готовый universal Linux bundle:
`build/plugins/mattergram-menu-0.2.0.tar.gz`. Он содержит `linux-amd64` и `linux-arm64` server
executables, webapp bundle, MIT license и license/notice files разрешённых Go-модулей. Builder
нормализует tar/gzip metadata.

## Установка

Полная production-инструкция, config-only signing key, обновление, rollback и собственный Mattermost image:
[docs/composer-keyboards.md](../../docs/composer-keyboards.md).

Для локального стенда можно использовать `demo.compose.yml`, разрешить unsigned upload и выполнить:

```powershell
mmctl plugin add ../../build/plugins/mattergram-menu-0.2.0.tar.gz --force
mmctl plugin enable com.mattergram.menu
```

Не переносите dev-настройку `RequirePluginSignature=false` в production.

## Границы

- Поддерживаются Mattermost Web и Desktop 11.9+.
- Нативные iOS/Android клиенты не исполняют webapp plugins. Для них нужен отдельный mobile plugin
  либо обычный post с Blocks/actions.
- Нажатие — ровно один обычный `POST /api/v4/posts`; неоднозначный write не повторяется.
- Удаление plugin сохраняет KV-данные Mattermost. Перед повторной установкой клавиатуры восстановятся.
