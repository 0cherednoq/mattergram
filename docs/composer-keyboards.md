# Постоянные клавиатуры Mattermost

`mattergram` поддерживает Telegram-подобные кнопки над полем ввода через plugin
`com.mattergram.menu`. Это расширение Mattermost, а не эмуляция `reply_markup`: без установленного и
включённого plugin методы явно завершаются HTTP 404/5xx.

## Описать клавиатуру

```python
from mattergram import Bot, ComposerButton, ComposerKeyboard

keyboard = ComposerKeyboard(
    title="Администрирование игровых аккаунтов",
    rows=[
        [
            ComposerButton(
                id="accounts",
                text="Аккаунты",
                message="/accounts",
                style="primary",
            ),
            ComposerButton(id="users", text="Пользователи", message="/users"),
        ],
        [
            ComposerButton(id="imports", text="Импорты", message="/imports"),
            ComposerButton(id="checks", text="Проверки", message="/checks"),
        ],
    ],
)

async with Bot(token=BOT_TOKEN, base_url="https://chat.example.com") as bot:
    state = await bot.set_composer_keyboard(CHANNEL_ID, keyboard)
    print(state.revision)
```

Каждый row содержит 1–4 кнопки, клавиатура — 1–6 rows. `id` уникален и стабилен, `text` — подпись,
`message` — текст post, который отправит пользователь. Доступны стили `default`, `primary`, `danger`
и `disabled=True`. Кнопки постоянных основных действий держите в верхних рядах; редкое или опасное
действие лучше оставить в post/form.

```python
current = await bot.get_composer_keyboard(CHANNEL_ID)
owned = await bot.list_composer_keyboards()
await bot.delete_composer_keyboard(CHANNEL_ID)
```

Set полностью заменяет клавиатуру канала и увеличивает revision. Plugin публикует channel-scoped
WebSocket event, поэтому открытые Web/Desktop клиенты перечитывают состояние без reload. Нажатие
создаёт post от actor session — bot token и admin token никогда не попадают в web bundle.
Начиная с plugin 0.2.2 каждое состояние содержит `owner_id`. Bot account получает через list API
только созданные им клавиатуры; system administrator видит весь inventory. Это даёт приложению
возможность сверять persistent UI с актуальным access repository после перезапуска.

## Собрать bundle

Нужны Python 3.11+, Node.js 20+ и Go 1.26.7+. Из корня репозитория:

```powershell
cd plugins/mattergram-menu
npm test
npm run build
Get-FileHash ..\..\build\plugins\mattergram-menu-0.2.2.tar.gz -Algorithm SHA256
```

Архив universal для Linux `amd64`/`arm64`. Сначала проверьте его на staging с той же версией
Mattermost, что и production.

## Установить bundle на self-hosted Mattermost

Plugin устанавливается на весь server/cluster. Нужен system admin. Для контролируемой ручной
установки без обязательной подписи:

1. Включите `PluginSettings.Enable=true` и `PluginSettings.EnableUploads=true`.
2. Убедитесь, что `PluginSettings.RequirePluginSignature=false`.
3. Перезапустите Mattermost после изменения файловой конфигурации.
4. Установите и включите plugin:

```bash
mmctl plugin add mattergram-menu-0.2.2.tar.gz --force
mmctl plugin enable com.mattergram.menu
mmctl plugin list
```

В HA загруженный bundle распространяется через настроенный Mattermost file store. Не распаковывайте
его вручную на одном node. Официальный lifecycle и требования к upload описаны в
[Mattermost: Install and manage plugins](https://docs.mattermost.com/administration-guide/configure/manage-plugins).

## Подписать для production

При `RequirePluginSignature=true` upload отключён. Создайте/импортируйте организационный GPG key,
подпишите точный неизменённый archive и экспортируйте public key:

```bash
gpg --local-user YOUR_KEY_ID \
  --personal-digest-preferences SHA256 \
  --detach-sign \
  --output build/plugins/mattergram-menu-0.2.2.tar.gz.sig \
  build/plugins/mattergram-menu-0.2.2.tar.gz

gpg --armor --export YOUR_KEY_ID \
  > build/plugins/mattergram-menu-public.asc
```

Храните private key вне image и CI artifacts. На Mattermost добавьте public key в
`PluginSettings.SignaturePublicKeyFiles`. Начиная с Mattermost 10.11, custom prepackaged plugins тоже
проверяются при startup: рядом с archive нужен `.sig`. Подробности и формат настройки:
[Mattermost plugin signing](https://docs.mattermost.com/developers/integrate/plugins/using-and-managing-plugins)
и [plugin configuration](https://docs.mattermost.com/administration-guide/configure/plugins-configuration-settings).

## Собрать отдельный Mattermost image

Да. `Dockerfile.mattermost` добавляет подписанный bundle, signature и public key в отдельный
Mattermost image как prepackaged plugin. Сначала выполните build и signing выше, затем из корня
репозитория:

```bash
docker build \
  -f plugins/mattergram-menu/Dockerfile.mattermost \
  --build-arg MATTERMOST_IMAGE=mattermost/mattermost-team-edition:12.0.2 \
  -t registry.example.com/mattermost-with-mattergram-menu:12.0.2-0.2.2 \
  .
```

До первого startup явно добавьте абсолютный путь
`/mattermost/plugin-signing/mattergram-menu-public.asc` в config-only
`PluginSettings.SignaturePublicKeyFiles` и включите
`PluginSettings.PluginStates.com.mattergram.menu.Enable`. Один только файл в
`prepackaged_plugins` не устанавливается автоматически без preconfigured enabled state. Перед
promotion проверьте image digest, startup logs и `mmctl plugin list`. Не монтируйте volume поверх
`/mattermost/prepackaged_plugins`, иначе встроенный archive будет скрыт. Public key находится в
image; private key — никогда.

## Проверить после deploy

1. `mmctl plugin list` показывает `com.mattergram.menu` версии `0.2.2` как enabled.
2. Bot account состоит в тестовом канале.
3. Вызовите `set_composer_keyboard` и затем `get_composer_keyboard`; revision равен 1 или больше.
4. Откройте канал в чистой Web/Desktop session: rows видны над composer.
5. Нажмите кнопку: создаётся один обычный post от текущего пользователя, dispatcher получает его.
6. Измените keyboard из bot process: открытая вкладка обновляется без reload.

Нативные iOS/Android клиенты webapp plugin не запускают. Не считайте responsive Web viewport
доказательством mobile support.

## Обновить или откатить

Для update загрузите bundle с тем же plugin ID и большей version, затем проверьте plugin logs и
клавиатуру на staging. `mmctl plugin add ... --force` заменяет установленную версию. Для rollback
загрузите предыдущий проверенный bundle и снова включите plugin. Plugin KV сохраняется при
disable/remove, поэтому channel keyboards переживают reinstall; явное удаление делайте через
`delete_composer_keyboard` до uninstall, если состояние сохранять не нужно.

Состояния, созданные версией 0.2.0, не имеют `owner_id`, поэтому их нельзя безопасно приписать
конкретному bot account. При первом переходе на 0.2.2 system administrator должен один раз сверить
`GET /plugins/com.mattergram.menu/api/v1/keyboards` с access repository и удалить устаревшие
channel states. System admin может выполнить DELETE без вступления в чужой DM; обычному bot
account по-прежнему требуется membership. После миграции owner-aware reconcile выполняется ботом.

Если UI исчез, проверьте по порядку: plugin enabled, server executable стартовал, user состоит в
канале, GET `/plugins/com.mattergram.menu/api/v1/channels/<id>/keyboard` возвращает 200, web bundle
не заблокирован CSP/proxy cache. При 403 bot либо не член канала, либо token принадлежит не bot/system
admin account.
