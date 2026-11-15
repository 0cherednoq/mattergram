# Native forms: открытие, ответы и sessions

`DialogManager` соединяет Form declarations с native open_dialog и HTTP событиями.
Открытие, validation errors, исправление ввода, multi-step, lookup, refresh, cancel,
timeout и replay проверены на собственном Mattermost 11.1 через API и actor WebSocket.
Отдельно выполнен [web 12.0.2/Chrome 157 profile](reports/ui-web-12.0.2.json): все 13
типов полей в записанных сценариях, validation/correction, wizard, lookup/refresh,
файл и вложенные формы. Другие версии и native desktop/mobile ещё не проверены;
E5-03 остаётся открытым. [Воспроизведение web stand](ui-stand.md).

На [web 11.11](reports/ui-web-11.11.0.json) отдельно проверены десять применимых
типов полей, field error/correction, wizard, lookup/refresh, file/child submit,
parent restore, date/manual datetime и cancel. Обычный actor скачал загруженные bytes;
выданный bot token остаётся единственным runtime credential.

## Подключить

Создайте `DialogManager(bot, profile=profile, callback_url=public_url +
"/mattergram/dialog")` и передайте его как `HttpAdapter(..., dialogs=manager,
own_dialogs=True)`. Bot, Dispatcher и manager должны работать в одном loop.
Опишите реальную поддержку server/client в UIProfile; unknown не становится
автоматически supported. Уже provisioned `/request` с настоящим issued command token
можно подключить по [HTTP guide](native-http.md).

`examples/form_bot.py` показывает полное приложение с валидацией; оно type-checked,
но отдельный запуск именно этого файла не заявлен. Real harness выполняет тот же
runtime путь и проверяет actor WS/native API ответы.

```python
@router.slash_command(Command("request"))
async def request(command: SlashCommand):
    handle = await command.open_dialog(form)

@router.dialog_submit()
async def submitted(event: DialogSubmission):
    if event.values["title"] == "invalid":
        await event.field_errors({"title": "Choose another title"})
        return
    # Сначала подтвердите нужную бизнес-запись/надёжную очередь приложения.
    await event.answer()
```

`command.open_dialog` и `query.open_dialog` используют только имеющийся native user
trigger. SDK отправляет open запрос своим runtime bot token, проверяет native OK
и после этого завершает исходный HTTP ACK. Обычный WS Message такого shortcut не
имеет. Нижний уровень `bot.open_dialog(SecretStr(trigger), form, url=…, profile=…)`
доступен отдельно; это native operation без managed session/replay. Он не делает
произвольный trigger подходящим: сервер проверяет его подпись и срок.

Trigger, initial request deadline, form session TTL и response_url lifetime различны.
Долгую работу нельзя ставить перед открытием формы, если trigger успевает истечь.
Повторная попытка open в одном response controller запрещена. Неподтверждённая
native запись не повторяется автоматически.

## Выбрать допустимый ответ

| Observer | Event | Ответ |
| --- | --- | --- |
| dialog_submit | DialogSubmission | answer(), field_errors(map), validation_error(text), next(form) |
| dialog_refresh | DialogRefresh | replace_form(form), field_errors(map), validation_error(text) |
| dialog_lookup | DialogLookup | answer(options) → native items |
| dialog_cancel | DialogCancel | answer(); отсутствие handler подтверждается как cancel |
| dialog_action | DialogAction | open_dialog(child) с новым trigger либо answer() |

Все ответы синхронные и завершаются один раз. Submit/refresh/lookup/action не
получают неявный успешный ACK после handler без ответа. Ошибка/timeout также не
превращаются в success. Для lookup нельзя вернуть field_errors или generic job ACK.
Произвольный background defer форм не поддерживается.

Для managed next/refresh вызывайте соответствующий shortcut. Raw mapping с новым
form не заменяет session transition: нужно выдать и сохранить новую capability.
Native error(s) и ACK mappings допустимы в пределах response family.

## Читать значения

`event.values` — schema-directed FormValues. `MISSING` из
`mattergram.ui.form_values` отличается от None, пустой строки/массива и false.
Defaults формы не подставляются вместо отсутствующего ввода.

В native web lookup select может прийти объектом `{label, value}` вместо строки.
Manager разбирает такой объект только для lookup; label остаётся недоверенной display
metadata, value проверяется по схеме. Обычные submit/refresh сохраняют строгие
primitive wire types. При самостоятельном разборе используйте
`parse_form_values(form, payload, partial=True, source="lookup")`.
`FormValues.raw` сохраняет исходный объект.

Text остаётся str. Native numeric text subtype может прислать JSON number; parser
сохраняет domain str и исходное wire значение отдельно. NumberInput с выдуманными
min/max/step не появляется. Checkbox принимает boolean или явные lowercase строки
true/false; truthiness строки false не используется. Multiselect/checkbox group —
массивы, а не безусловный split любой строки по запятой. Matrix приходит массивом
`row:col1,col2` и проверяется по rows/columns/row_selection схемы.

Date превращается в datetime.date, RFC3339 datetime — в aware datetime с сохранённым
offset. Relative configuration не вычисляется в timezone процесса. Дополнительные
бизнес-ограничения и контекстное разрешение relative bounds остаются у приложения;
это не замена серверной проверки бизнес-данных.

`event.file_ids` хранит подтверждённый top-level список там, где он есть в native
payload. FormValues.file_ids включает подтверждённые files предыдущих принятых шагов.
File field string сохраняется, а связь отдельных файлов с отдельным FileInput не
выдумывается. Metadata/ownership проверяются отдельными native API при необходимости.

Неверные текущие поля дают native errors до бизнес-handler. Lookup/refresh допускают
частичный ввод без required errors для ещё не заполненных полей. У lookup доступны
query и selected_field; выбранное поле должно быть declared dynamic select.
Refresh принимается только для select с refresh=True. Служебные query/selected_field
не должны совпадать с именами полей managed lookup/refresh формы.

## Sessions, шаги и повторная доставка

Wire state — случайная capability `mgd1.<local-session-id>.<256-bit-secret>`.
Прикладной `Form.state` доступен как `event.application_state`, а не заменяется
поддельным provider ID. Session связана с instance/bot/user/channel/team, callback,
revision и разрешённой operation. Transport peer проверяется до capability.

Session record содержит lossless declaration snapshot, ранее принятые значения и
результаты переходов. Next/refresh выдаёт новую capability. Повтор того же принятого
перехода возвращает сохранённый ответ; иной submit/refresh/action со старым step state отклоняется.
Значения предыдущих принятых шагов сохраняются сервером, поэтому backend не зависит
от недоказанного сохранения всех значений клиентом. Подмена prior values вне полей
текущего шага отклоняется. Это не обещание конкретного rendering/preservation клиента.

Native web adapter сохраняет opening callback_id при замене формы. Managed wire и
capabilities поэтому используют исходный callback ID; `event.callback_id` отражает
transport, а `event.form.callback_id` содержит текущую прикладную декларацию.
Новый state/revision всё равно выдаётся на каждом переходе.

Lookup и cancel этого клиента используют opening state даже после перехода.
Opening capability сохраняет только эти session operations: scope, callback,
expiry, active/finished state и declared dynamic field по-прежнему проверяются.
Это не разрешение повторять stale submit/refresh/action. После закрытия lookup
отклоняется; уже завершённый submit/cancel не запускает handler повторно.

Next/replace_form переносит совместимые значения в defaults новых same-name/type
полей, если приложение default не задавало. Явные None, пустые значения и false
имеют приоритет. Изменившиеся options/type/limits могут сделать старое значение
невыразимым; такое значение не переносится. Snapshot сохраняет model_fields_set,
поэтому повторное использование event.form учитывает новые правки пользователя.
Collapsible обрабатывается рекурсивно. Global file_ids не превращаются в default
конкретного FileInput без подтверждённой связи.

Перед business handler record получает running claim. Terminal success сохраняется
перед HTTP ответом: повтор не запускает business handler. После cancellation, timeout,
необработанной ошибки или прерывания claimed операции session становится uncertain;
другая replica не запускает её снова автоматически. Сначала сверьте бизнес-результат,
затем приложение может вызвать `reconcile(state, response={})` для известного успеха
или `reconcile(state)` для явно разрешённого повтора. Для recovery по безопасному
local ID есть `reconcile_session(session_id, …)`. Эти методы не являются HTTP routes.
Validation error сохраняет возможность исправления ввода.

Это **не атомарная транзакция** с бизнес-БД, fencing или абсолютная exactly-once
доставка. Приложение отвечает за свою идемпотентность/транзакцию/надёжную очередь.
Replay records действуют в пределах живой session/capability; после expiry запрос
отклоняется. Default TTL 900 секунд, managed steps 32, nonterminal response history 16,
capability history 512. Исчерпание лимита даёт явный отказ, не вытесняет живой результат.

## Вложенные формы

DialogButton указывает на manager.callback_url. Manager добавляет отдельную opaque
action capability к native string context. `DialogAction.application_context`
берётся из сохранённого объявления: подмена произвольных client context полей не
изменяет эту trusted конфигурацию. Новый trigger приходит от native dialog execute.

Root имеет depth 1; допускаются максимум три native модальных уровня. Новая child
не открывается поверх ещё живой child той же parent. Child не допускается к новой
работе после неактивной parent; её lifetime не превышает parent TTL. Уже начатые
внешние бизнес-операции cancellation не откатывает. Полный logical stack/workflow
extension относится к mattergram_dialog; он не равен стеку native модальных окон.

## Memory, Redis и ownership

Default MemoryDialogStore ограничен 1024 живыми records и применяет портированный
SimpleEventIsolation. Он не переживает restart и не разделяется между replicas.
Для shared/restart replay используйте `RedisDialogStore.from_url(url, ttl=1800,
prefix=…)` из `mattergram.http.redis_dialogs` и `mattergram[redis]`.

Manager заимствует переданный store; закройте его после drain всех использующих его
workers. from_url владеет Redis pool/isolation, constructor заимствует ресурсы.
Record TTL должен быть не меньше session TTL; expiry дополнительно проверяется
SDK. Нужны согласованные часы и одинаковая конфигурация профиля у replicas.
Redis advisory lease не даёт fencing бизнес-записи. Подробнее — [Redis guide](native-redis.md).

HttpAdapter заимствует manager по умолчанию; own_dialogs=True закрывает manager
после drain. Закрывается только default owned Memory store; custom store остаётся
за владельцем приложения. Shutdown учитывает и запросы, ожидающие session lock до
dispatch. ASGI disconnect отменяет handler без отправки успешного ACK.

## Диагностика и доказательства

Normal repr/dumps/raw events скрывают submission, state, trigger и file IDs.
`event.values.raw` и `unsafe_raw()` — явный доступ к чувствительным данным;
не отправляйте их в обычные логи. Неизвестные future wire fields также могут содержать
секреты. Redis/session snapshots требуют защищённого backend.

`manifests/forms-profile.json` хранит versioned primary references и границы.
`contracts/forms-ingress.openapi.json` описывает authored ingress contract.
Captured fixtures обезврежены и имеют provenance. Отчёт —
[forms-core.json](reports/forms-core.json); private captures остаются в ignored build/.

```sh
uv run --locked pytest tests/unit/test_form_values.py tests/integration/test_dialogs.py
uv run --locked python -m tools.verify_http_stand
# Для Redis задайте MATTERGRAM_REDIS_URL и запустите mandatory profile:
uv run --locked python -m tools.verify_redis_stand
# С отдельно запущенной локальной Postman simulation:
uv run --locked python -m tools.verify_form_simulation --base-url http://127.0.0.1:4010/mattermost
```
