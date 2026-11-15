# Production deployment

Это руководство предназначено для оператора, который разворачивает runtime с REST/WS,
slash/actions/forms HTTP ingress, Redis state и optional Bootstrap. Оно описывает границы
реализованного SDK, а не готовый Helm chart или облачную платформу.

## Разделите setup и runtime

```text
one-shot init job                         long-running runtime
setup credential                         runtime bot token
        │                                        │
        ▼                                        ▼
Bootstrap -> encrypted local store -> load_runtime()
        │                                        │
Mattermost resources              REST + WebSocket + HTTP ingress
```

Init job получает management credential, выполняет `Bootstrap.prepare()` и сохраняет issued
runtime/command secrets в `SQLiteProvisioningStore`. Runtime запускает
`Bootstrap.load_runtime()` без setup token. Не монтируйте admin/setup secret в основной
контейнер «на всякий случай»: SDK не использует его как fallback.

Подробные declarations, ownership и recovery: [bootstrap.md](bootstrap.md). Encryption,
permissions и journal: [provisioning-store.md](provisioning-store.md).

## Persistent данные и secrets

| Данные | Где хранить | Требование |
| --- | --- | --- |
| Fernet master key | внешний secret manager/mounted secret | не рядом с SQLite; сохранять между deploy |
| Provisioning SQLite + lock | private persistent local volume | один trusted owner; не NFS/UNC |
| Runtime bot/command secrets | encrypted provisioning store | не логировать и не помещать в image |
| Callback signing keys | secret manager | минимум 32 secret bytes; controlled rotation |
| FSM/dialog state | Redis для multi-process | общий storage и общий event isolation |

Local store lock сериализует процессы только на одном filesystem. Он не является distributed
lock/fencing между разными volumes. Для нескольких replicas выполняйте Bootstrap отдельным
single-writer init job, а runtime pods загружайте после успешного reconciliation.

## Сетевой путь

Runtime открывает исходящие HTTPS/WSS соединения к Mattermost и принимает integration HTTP
через reverse proxy:

```text
Mattermost --HTTPS--> reverse proxy --private HTTP/ASGI--> mattergram HttpAdapter
mattergram --HTTPS/WSS----------------------------------> Mattermost
```

Для standalone runtime можно вызвать:

```python
await dispatcher.start(bot, http=http, host="127.0.0.1", port=8080)
```

Listener держите на loopback/private interface, TLS завершайте на управляемом reverse proxy.
В `trusted_peers` укажите фактический peer proxy/Mattermost. SDK не считает
`X-Forwarded-For` доказательством источника. Ограничьте integration routes сетевой политикой;
для production используйте контролируемую сеть и при необходимости mTLS.

При встраивании `ASGIAdapter` его lifespan владеет только HTTP adapter. Bot, Dispatcher,
router hooks и WebSocket остаются у приложения: их нужно запустить и закрыть явно. Не запускайте
одновременно standalone listener и ASGI lifecycle над одним adapter.

## Startup, readiness и shutdown

Standalone `Dispatcher.start()` выполняет identity → router startup → HTTP admission →
WebSocket. При остановке admission закрывается, reader прекращается, текущая работа получает
bounded drain/cancellation, router shutdown идёт в обратном порядке, затем закрываются pools.

Разделяйте probes:

- liveness: процесс/event loop не завис;
- readiness: listener поднят, bot identity проверен, необходимые channel permissions доступны;
- external callback check: Mattermost действительно достигает публичного callback URL.

`bot.resources.report.runtime_verified=True` подтверждает runtime identity/permissions, но
`callback_verified` остаётся false до отдельной сетевой проверки. Не объявляйте pod ready лишь
потому, что TCP listener bind прошёл.

При termination сначала снимите readiness и admission, затем дождитесь `dispatcher.stop()` и
задачи lifecycle. Handler должен поддерживать asyncio cancellation. `ShutdownTimeoutError`
означает, что он не завершился в budget; повторная доставка/частичный внешний эффект всё ещё
возможны.

## Scaling и delivery semantics

- WebSocket — не durable queue; reconnect не гарантирует отсутствие пропусков или replay.
- HTTP signature/token аутентифицирует контекст, но не делает business operation exactly-once.
- Deferred jobs живут в памяти процесса; важную работу сохраняйте в durable queue до ACK.
- Один Redis state без соответствующего Redis event isolation не защищает read-modify-write.
- `RequestOutcomeUnknown` требует reconciliation по provider state, а не автоматического retry.
- Dashboard live updates должны иметь application version/idempotency и coalescing при высокой
  частоте; process-local store из showcase — только пример.

Несколько runtime replicas требуют отдельного решения для duplicate events и application
effects. Mattergram предоставляет bounded transport/lifecycle, но не заменяет transactional
outbox, distributed job queue или business database fencing.

## Минимальный deployment checklist

1. Зафиксируйте Mattermost/server/UI profiles и версию SDK.
2. Выполните init job с setup secret и persistent encrypted store.
3. Уберите setup secret из runtime; загрузите только runtime resources.
4. Подключите Redis storage/isolation, если процессов больше одного или state должен пережить
   рестарт.
5. Настройте TLS, callback URL, trusted peers, body/inflight limits и access-log redaction.
6. Выполните реальный slash/action/form smoke test через Mattermost, а не только прямой curl.
7. Проверьте graceful shutdown и recovery для неопределённой записи.
8. Сохраняйте версию, manifest fingerprint и evidence рядом с release.

## Что ещё не подтверждено

Проект проверен локально на Windows/Python 3.11, 3.13 и 3.14 и на закреплённых Mattermost
profiles. Внешний GitHub CI, Linux/macOS production execution, orchestration templates,
distributed Bootstrap lock и native desktop/mobile acceptance не заявлены. Эти ограничения
нельзя превращать в обещания deployment support без отдельного прогона.
