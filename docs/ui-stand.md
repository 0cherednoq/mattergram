# Проверить SDK в настоящем web-клиенте

Руководство предназначено разработчику или оператору локального UI stand.
`tools.ui_stand` создаёт отдельные setup/admin, ordinary human и runtime bot fixtures,
затем обслуживает native slash/forms/callbacks через настоящий SDK. Скрипт находится
в development tools; runtime пакет не получает административных credentials.

Проверенная среда: Windows, Docker Desktop Linux engine, Mattermost 12.0.2/11.11.0,
Chrome headless 157 и Playwright CLI 0.1.24. Это не проверка native desktop/mobile
приложений и не полный CI/OS profile. Результаты: [web evidence](reports/ui-web-12.0.2.json),
[client profile](../manifests/ui-client-profile.json).

## Подготовить стенд

Нужны работающий [native stand](native-client.md), его ignored
`build/native-stand.env`, Docker Compose, OpenSSL и Node/npm с npx.
На Windows TLS helper также находит OpenSSL в стандартном каталоге Git for Windows.
Запускайте команды из корня checkout.

```powershell
uv sync --locked
uv run --locked python -m tools.ui_stand tls
docker compose --project-name mattergram-native-stand --env-file build/native-stand.env -f infra/native-stand.compose.yml -f infra/ui-stand.compose.yml --profile ui up -d mattermost_ui
uv run --locked python -m tools.ui_stand setup
```

TLS нужно сгенерировать до Compose profile: bind mount ожидает файл `ca.pem`.
Overlay создаёт отдельные PostgreSQL/data volumes для 12.0.2 и использует ту же
owned network. Mattermost публикуется на loopback с динамическим портом.
Setup узнаёт порт через Compose и задаёт SiteURL, обязательный для slash на 12.0.2.

CA доверяет только процесс отдельного контейнера через `SSL_CERT_FILE` и read-only
mount. Глобальное доверие Windows и отключение outgoing certificate verification
не используются. Leaf имеет SAN host.docker.internal/localhost/127.0.0.1.
[Go SystemCertPool](https://pkg.go.dev/crypto/x509#SystemCertPool) описывает выбор
дополнительного trust file; настоящий native HTTPS probe подтверждает соединение.

Setup выдаёт browser session обычному actor через настоящий browser-login API.
Server-issued cookies, включая user/CSRF state, сохраняются только в
`build/ui-browser-state.json`. Secrets и native IDs fixtures находятся в
`build/ui-stand-state.json`. Не включайте эти файлы в отчёты или дистрибутивы.

## Запустить callback host

### Выбрать 11.7 или 11.11

Для профилей других версий добавьте matrix overlay. Каждая версия
получает собственную PostgreSQL/data volume и отдельные ignored state/report files.
Пример для 11.11 (для 11.7 используйте `ui117`, `mattermost_ui117`, `11.7.0`):

```powershell
docker compose --project-name mattergram-native-stand --env-file build/native-stand.env -f infra/native-stand.compose.yml -f infra/ui-stand.compose.yml -f infra/ui-client-matrix.compose.yml --profile ui1111 up -d --wait --wait-timeout 120 mattermost_ui1111
uv run --locked python -m tools.ui_stand setup --profile 11.11.0
uv run --locked python -m tools.ui_stand serve --profile 11.11.0
```

Browser state: `build/ui1111-browser-state.json`; channel URL:
`build/reports/ui1111-setup.json`; observations: `build/reports/ui1111-stand.json`.
При завершении передайте тот же `--profile` команде `stop`. Общий TLS fixture
сохраняется в `build/ui-tls`; trust остаётся внутри отдельных контейнеров.

Исполняйте применимые сценарии: basic/dates/bounds/timezone/wizard/lookup/cancel/legacy
на обеих версиях; `modern` (text/file/child action), Blocks и imports начиная с 11.11;
advanced/labels требуют 12.0.2. Runtime capability gates не обходятся.
Команды запуска описывают workflow; фактические результаты смотрите в client profile.

### Запустить выбранную версию

```powershell
uv run --locked python -m tools.ui_stand serve
```

Host выполняет real issued-command HTTPS probe, проверяет actor/token и фиксирует
его transport peer. SDK listener принимает только этот peer. После ready скрипт
печатает публичные command/channel values. Exact command меняется при каждом запуске.
`build/reports/ui-stand.json` содержит команду и наблюдения без capabilities/tokens.

Оставьте host работающим. Во втором терминале откройте channel_url из
`build/reports/ui-setup.json` через Playwright CLI, загрузите приватный storage state,
затем перейдите к каналу. Для browser landing выберите View in Browser.
Не передавайте password/token в CLI arguments и не выводите cookies в консоль.

```powershell
$uiFixtureInfo = Get-Content -LiteralPath build/reports/ui-setup.json -Raw | ConvertFrom-Json
$uiStoragePath = (Resolve-Path build/ui-browser-state.json).Path
New-Item -ItemType Directory -Path output/playwright/ui12 -Force | Out-Null
Push-Location output/playwright/ui12
npx --yes --package @playwright/cli@0.1.24 playwright-cli -s=mattergram-ui12 open $uiFixtureInfo.channel_url --browser chrome
npx --yes --package @playwright/cli@0.1.24 playwright-cli -s=mattergram-ui12 state-load $uiStoragePath
npx --yes --package @playwright/cli@0.1.24 playwright-cli -s=mattergram-ui12 goto $uiFixtureInfo.channel_url
```

Артефакты храните под ignored `output/playwright/`. Делайте новый snapshot после
навигации, выбора dynamic option и смены формы. При stale ref остановите зависимые
команды и получите свежий snapshot. Screenshots сохраняйте с абсолютным filename.

## Выполнить сценарии

В textbox канала введите выданную команду и один из аргументов:

| Аргумент | Сценарий |
| --- | --- |
| basic | Text/textarea/select/multiselect/bool/radio; title=reject даёт field error, исправление закрывает форму |
| dates | Date/calendar, datetime config, timezone display и manual time entry |
| bounds | Узкий min/max диапазон 10–14 ноября; disabled соседние даты и отправка обеих границ |
| timezone | Asia/Kolkata, GMT+5:30, manual time отключён, 30-minute menu |
| labels | Before/after для Checkbox inline caption, Radio и CheckboxGroup |
| wizard | Project → Summary; backend хранит данные первого шага |
| lookup | Category вызывает refresh; Remote choice получает dynamic options; comment переносится в новые defaults |
| advanced | Checkbox group/matrix, collapsible, file upload, child dialogs |
| cancel | Native cancel notification |
| blocks | Все девять native Blocks, signed button/select, image, columns и collapsible |
| slack/adaptive | Supported-subset input adapters, native rendering и signed callback |
| legacy | Rich attachment, signed button/static select и disabled-button click guard |

Для stack сценария откройте child, затем ещё один child. Следующая попытка должна
дать `native_modal_stack_limit` до handler. Cancel возвращает предыдущую форму.
Проверьте сохранение parent values/file перед final submit.

Callback ephemeral confirmations в проверенном 12.0.2 отображаются в thread
исходного поста. Откройте Reply, чтобы увидеть Only visible to you.
Это отличается от ожидания нового сообщения в основном канале.

## Проверить результат и завершить

`ui-stand.json` фиксирует handler events и статусы. Чувствительные raw requests,
state и responses сохраняются только в ignored `build/ui-private-http.json`.
Public evidence должно содержать проверенные результаты, hashes и выбранные
screenshots. Оно не должно содержать этот raw capture или browser storage state.

Для файла сравните скачанные обычным actor bytes с исходным fixture. API control
session при необходимости обновляется явно; это не административный fallback runtime.
SDK сохраняет field string и global file_ids, не выдумывая универсальную per-field связь.

Закройте только собственную browser session и остановите host:

```powershell
npx --yes --package @playwright/cli@0.1.24 playwright-cli -s=mattergram-ui12 close
Pop-Location
uv run --locked python -m tools.ui_stand stop
```

Дождитесь завершения serve. Он закрывает SDK resources и удаляет созданную команду.
Docker volumes сохраняются для повторного запуска. Остальные projects/networks
не очищаются. Schema references проверяются `python -m tools.fetch_ui_references --verify`.

Дополнительные bounds/timezone/label-position/legacy cases выполнены: [отчёт](reports/ui-web-12.0.2-additional.json).
У Checkbox label_position меняет положение inline caption, заданного через placeholder;
заголовок label остаётся над полем. Проверка bounds относится к native calendar;
она не проверяет forged out-of-bound API input.

Отдельный [web 11.11 report](reports/ui-web-11.11.0.json) подтверждает записанные
сценарии этой версии. [Web 11.7 report](reports/ui-web-11.7.0.json) подтверждает
применимые формы/date/time/legacy. Fixture IDs legacy controls выбраны явно
алфавитно-цифровыми: 11.1/11.7 не маршрутизируют `_`/`-`, и SDK отклоняет такие IDs
до POST. Для расширенных IDs 11.11/12 нужен явный ui_profile.
E5-03 остаётся открытым: нужны native desktop/mobile
и дополнительные 11.11 parameter cases. Проверка viewport другого размера
не заменяет запуск другого приложения.
