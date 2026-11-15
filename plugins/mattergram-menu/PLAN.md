# План постоянного меню Mattergram

## Цель

Повторить поведение Telegram Reply Keyboard в Mattermost web/desktop: меню всегда
находится над composer, один click публикует одно видимое сообщение-команду от
текущего пользователя, а существующий `mattergram` Dispatcher обрабатывает его как
любой другой WebSocket message.

Основной сценарий занимает один click. Дополнительных окон и отдельного callback
протокола нет.

## Контракт первой версии

1. Использовать официальный `registerChannelComposerBannerComponent`, доступный с
   Mattermost 11.9.
2. Показывать четыре стабильных ряда `2 + 2 + 1 + 2`.
3. Публиковать команды только через user-session REST API; не включать bot/admin
   credential в bundle.
4. Не выполнять automatic retry для POST: после disconnect неизвестно, создал ли
   сервер сообщение.
5. Пока write выполняется, блокировать остальные кнопки. Ошибку показывать рядом с
   меню и оставлять возможность явного повторного click.
6. Показывать меню в DM/GM или в канале с явным `[mattergram-menu]` marker.
7. Не заявлять native-mobile поддержку: webapp component там не исполняется.
8. Требовать membership runtime bot в каждом целевом канале; plugin не расширяет
   права и не пересылает события в обход штатного WebSocket.

## Фазы

### M1. Dependency-free webapp slice — реализовано локально

- Manifest с minimum server 11.9.
- Composer component и scoped CSS.
- Семь фиксированных label → command mappings.
- REST create-post от actor session.
- Visibility guard, pending guard и явная ошибка.
- Unit tests и воспроизводимый `.tar.gz` builder без npm dependencies.

### M2. Настоящий Mattermost stand — выполняется

- [x] Установить unsigned development bundle на отдельный owned Mattermost 12.0.2.
- [x] Проверить real Chrome rendering всех семи кнопок в public channel с marker.
- [x] Нажать «Кампании» и подтвердить один post `/campaigns` с HTTP 201; меню остаётся над composer.
- Проверить public channel с marker, DM, GM и немаркированный channel.
- Нажать оставшиеся шесть кнопок и подтвердить ровно один post с правильным `user_id`,
  `channel_id` и `message`.
- Подключить тестовый `mattergram` Dispatcher и подтвердить семь handler routes.
- Проверить ошибку 403, потерю сети, повторный click после ошибки, тему dark/light,
  1280/1440 desktop и tablet mobile-web viewport.
- Записать server/client versions, screenshots и sanitized evidence.

### M3. Управляемая конфигурация — реализована в 0.2.0

- Server plugin хранит versioned keyboard schema по channel ID в plugin KV.
- Typed `Bot.set/get/delete_composer_keyboard` управляет rows декларативно.
- PUT/DELETE разрешены bot accounts и system admins, GET требует channel membership.
- Compare-and-set защищает revisions от lost update; channel-scoped WebSocket event обновляет UI.
- Произвольные URL/JS запрещены: кнопка публикует только объявленный обычный message от actor session.
- Осталось подтвердить полный live-сценарий 0.2.0 и HA на целевой production-версии Mattermost.

### M4. Cross-client fallback

- Для native iOS/Android публиковать Mattermost Blocks menu post с теми же командами.
- Держать одну общую command map для webapp и Python fallback.
- Проверять capability, не считать web tablet доказательством native tablet support.

## Обработчики приложения

Плагин не должен знать бизнес-логику. Он только создаёт сообщения. Приложение
регистрирует семь точных handlers:

| Команда | Действие обработчика |
| --- | --- |
| `/campaigns` | Открыть список кампаний |
| `/mailing_new` | Начать форму новой рассылки |
| `/pools` | Открыть список пулов |
| `/proxy_groups` | Открыть группы прокси |
| `/test_email` | Открыть форму тестового письма |
| `/nodes` | Открыть список нод |
| `/sites` | Открыть список сайтов |

Handlers сами выбирают `mattergram_dialog` state, проверяют права и возвращают
прикладной результат. Плагин не получает административных fallback-возможностей.

## Границы первой версии

- Команды и раскладка фиксированы в bundle до M3.
- Клавиатура также отображается над RHS/thread composer, потому что таков контракт
  hook; команда первой версии всегда создаётся в channel root.
- Реальный server rendering и REST create-post для `/campaigns` проверены на отдельном Mattermost 12.0.2; доставка команды в `mattergram` Dispatcher ещё не проверена.
- Нативное мобильное приложение требует Blocks fallback или собственную сборку
  mobile client; webapp plugin его не расширяет.
