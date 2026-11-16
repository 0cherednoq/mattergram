# Автоматический Bootstrap Mattermost

Эта инструкция предназначена для разработчика или оператора, который хочет
создать объявленные bot, team, channel, membership и slash command ресурсы один
раз, а затем запускать приложение без setup credential. `Bootstrap` сверяет
текущее состояние Mattermost с декларациями, хранит ownership и issued secrets
в зашифрованном SQLite store и возвращает обычный runtime `Bot`.

## Что требуется до запуска

- Установите `mattergram[provisioning]`.
- Выделите постоянный приватный каталог для SQLite state.
- Передайте внешний Fernet key через secret manager. Store не создаёт и не
  сохраняет master key.
- Подготовьте setup API token с фактическими правами для заявленных операций.
  Название роли не заменяет проверку native permissions.
- Настройте TLS, DNS/reverse proxy и адрес callback отдельно. API token не
  разворачивает сетевую инфраструктуру.

Контракт и последовательность вызовов проверяются тестами provisioning. Для первого запуска задайте
`MM_SETUP_TOKEN`. После успешного provisioning основной контейнер может не
получать эту переменную и использовать `load_runtime()`.

## Объявите ресурсы и выполните reconciliation

```python
from pathlib import Path

from pydantic import SecretBytes

from mattergram.provisioning import (
    Bootstrap,
    BotSpec,
    ChannelSpec,
    FernetCodec,
    SQLiteProvisioningStore,
    TeamSpec,
)

store = SQLiteProvisioningStore(
    Path("/data/mattergram"),
    codec=FernetCodec(SecretBytes(master_key)),
)

bootstrap = Bootstrap(
    base_url="https://mattermost.example.com",
    token=setup_token,
    application="request-service",
    bot=BotSpec(username="request-bot"),
    store=store,
    callback_base_url="https://bot.example.com",
    team=TeamSpec(
        key="main",
        name="engineering",
        existing_policy="reuse",
    ),
    channels=(
        ChannelSpec(
            key="alerts",
            team="main",
            name="service-alerts",
            display_name="Service alerts",
            visibility="private",
            create_if_missing=True,
            existing_policy="reuse",
        ),
    ),
)

bot = await bootstrap.prepare(dispatcher)
```

`prepare()` собирает slash metadata только из подключённых Router. Декларации
обычных message command не становятся native slash command. После успешного
вызова доступны `bot.resources.teams`, `bot.resources.channels`,
`bot.resources.slash_bindings` и отчёт без секретов.

Короткая строка в `team`, `teams` или `channels` означает ссылку на существующий
ресурс с explicit reuse. Она не разрешает создание. Для создания используйте
`TeamSpec` или `ChannelSpec` с `create_if_missing=True`, полным именем,
display name и явной visibility.

## Запустите runtime без setup credential

После init job создайте тот же `Bootstrap`, передайте `token=None` и те же
application/resource declarations:

```python
runtime_bot = await bootstrap_without_setup_token.load_runtime(dispatcher)
```

`load_runtime()` читает зашифрованные runtime и command secrets, проверяет
identity неадминистративного bot account, реальные team/channel IDs и права
`read_channel`/`create_post`. Метод не создаёт и не обновляет server resources.
Если command manifest изменился, runtime завершится явным конфликтом. Сначала
запустите `prepare()` с подходящим management credential.

Setup и runtime используют разные HTTP clients, credentials и connection pools.
Setup client не передаётся в `Bot.resources` и не используется как fallback для
`send_message` или HTTP handlers.

## Подключите HTTP endpoint и readiness

Регистрация callback URL не доказывает, что Mattermost может подключиться к
listener. Создайте `HttpAdapter` из подготовленных bindings:

```python
http = HttpAdapter(
    bot,
    dispatcher,
    trusted_peers=["10.0.0.0/8"],
    slash_bindings=bot.resources.slash_bindings,
    path_prefix=bot.resources.http_path_prefix,
)
await dispatcher.start(bot, http=http, host="127.0.0.1", port=8080)
```

`bot.resources.report.runtime_verified` становится `True` после проверки bot
identity и channel permissions. `callback_verified` остаётся `False`.
Orchestrator должен объявлять pod ready только после запуска listener и отдельной
проверки сетевого пути от Mattermost.

## Ownership и повторные запуски

Store различает два состояния:

| Состояние | Что разрешено |
| --- | --- |
| `owned` | Сверять и обновлять поддерживаемые mutable поля при совпадении native ID и owner evidence |
| `reused` | Проверять identity/scope/privacy и использовать ресурс без изменения его свойств |

Совпадения username или trigger недостаточно для adoption. Чужая или встроенная
команда с тем же trigger вызывает `ProvisioningConflict`. Existing channel с тем
же name требует `existing_policy="reuse"`; reuse не даёт права менять display
name, purpose, header, privacy или вручную добавленных участников.

Membership работает в add-only режиме. `MemberSpec` принимает точный native
`user_id` или точный `username`; строки в `members` считаются native user IDs.
Bootstrap не создаёт пользователей, не приглашает email и не удаляет лишних
участников. Исчезновение ресурса из manifest также не удаляет server resource.

Store и OS lock удерживаются на всю setup фазу. Два процесса с одним локальным
state directory выполняют Bootstrap последовательно и не создают второй набор
ресурсов. Этот lock рассчитан на локальную файловую систему. Он не является
распределённым lock или fencing для NFS и отдельных state volumes.

## Восстановите частично завершённую запись

Bootstrap записывает durable intent до native write.

- Ответ 401, 403, 4xx conflict или 429 завершает intent как `aborted`: server
  явно отклонил запись, поэтому исправленный запуск может повторить шаг.
- Потерянный ответ, transport failure после отправки, malformed success или
  несовпадение identity после ответа оставляет `pending`/`indeterminate` intent.
  Следующий Bootstrap не повторяет resource write автоматически.
- Если native ID уже известен, intent сохраняет его без secret value.
- Потеря bot/command secret не выпускает новый token автоматически.

Для ambiguous outcome оператор сначала проверяет Mattermost через отдельный
разрешённый client. Если ресурс существует, завершите тот же intent настоящим
native ID и доступным issued secret. Если create гарантированно не состоялся,
вызовите `confirm_absent()` и повторите Bootstrap. Не используйте
`confirm_absent()` только потому, что локальный timeout истёк.

```python
async with store.session(scope) as journal:
    intent = next(
        item
        for item in journal.intents()
        if item.phase in {"pending", "indeterminate"}
    )
    journal.complete(intent, resource_id=confirmed_native_id, secret=recovered_secret)
    # Либо после независимого подтверждения отсутствия:
    # journal.confirm_absent(intent)
```

`PersistenceError.resource_id` сообщает recovery ID, если native resource был
создан, но binding/secret не удалось сохранить. Значение secret не включается в
исключение, repr моделей или provisioning report.

## Серверные настройки и поддерживаемые профили

Bootstrap принимает профили Mattermost `11.1.0`, `11.7.0`, `11.11.0` и
`12.0.2`, для которых в проекте закреплены primary references. Unknown profile
завершается fail-closed.

По умолчанию disabled или unobservable feature flag вызывает
`UnsupportedCapabilityError`. При `change_server_settings=True` разрешено только
включить `EnableCommands`, `EnableBotAccountCreation` и
`EnableUserAccessTokens`. Bootstrap отправляет `config/patch`, затем читает
client config повторно. Другие settings не включены в payload.

## Что проверено

- Native Mattermost 12.0.2: два team, два private channel, четыре commands,
  repeat без дублей, четыре metadata updates, четыре настоящих callbacks и
  четыре runtime posts с readback обычным пользователем.
- Локальная Postman simulation: 403 без write, committed-before-timeout,
  explicit recovery, foreign trigger, disabled features, ограниченный
  provisioner, runtime без setup token и два concurrent Bootstrap instances.
- Windows Python 3.11, 3.13 и 3.14 проверяются общей матрицей проекта.

Native стенд доказывает provider behavior только для записанных успешных
сценариев. Fault injection Postman доказывает поведение SDK против authored
stand-in и не утверждает, что реальный Mattermost воспроизводит тот же сбой.
