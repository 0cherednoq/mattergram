# Декларативный доступ по ролям

Mattergram предоставляет маленькое core-RBAC API для одинаковой защиты message, slash и callback
handlers. Публичный контракт состоит из стандартных handler flags, одного resolver и middleware.
Отдельного policy DSL нет.

## Подключить middleware

Приложение предоставляет `RoleResolver`: по точному Mattermost user ID он возвращает одну
включённую app-role или `None`. Отсутствующий, отключённый пользователь и неизвестная роль должны
возвращать `None`. Ошибку repository скрывать как отказ нельзя: исключение resolver должно дойти до
общего error handling и наблюдаемости.

```python
from mattergram import Router
from mattergram.security import RBACMiddleware

router = Router(name="orders")
router.message.middleware(
    RBACMiddleware(role_service, denied_text="Недостаточно прав")
)
router.slash_command.middleware(
    RBACMiddleware(role_service, denied_text="Недостаточно прав")
)
```

`AdminUsersService` уже реализует `RoleResolver`. `AdminUsersPlugin` подключает middleware к своим
entry handlers сам; копировать эту настройку для `/app-users` и текста «Пользователи» не нужно.

## Объявить доступ на handler

Канонический и самый заметный для code review способ — обычный aiogram-style аргумент `flags`:

```python
from mattergram.filters import Command
from mattergram.security import REQUIRED_ROLES_FLAG


@router.message(
    Command("stats"),
    flags={REQUIRED_ROLES_FLAG: ("admin", "operator")},
)
async def stats(message, actor_role: str) -> None:
    await message.answer(f"Доступ подтверждён для роли {actor_role}")
```

Строковый literal разрешает одну роль. Tuple/list/set разрешает любую из перечисленных ролей.
Пустое значение, дубликаты и нестроковые role IDs считаются ошибкой декларации, а не отказом
пользователю.

Для decorator-style объявления доступен тот же механизм flags:

```python
from mattergram import flags


@router.message(Command("stats"))
@flags.required_roles(("admin", "operator"))
async def stats(message) -> None:
    await message.answer("Статистика")
```

Middleware пропускает handlers без `required_roles`. Для защищённого handler оно получает
`event.user_id`, разрешает роль и добавляет `actor_role` в DI data. При несовпадении роли handler не
запускается, а пользователь получает `denied_text` через обычный `event.answer()`.

## Проверить роль в service

Handler flags защищают вход, но не заменяют service authorization. Бизнес-операция остаётся
защищённой при вызове из dialog action, background worker, теста или другого router:

```python
class AccountsService:
    async def delete_account(self, actor_id: str, account_id: str) -> None:
        await self.rbac.require_role(actor_id, "admin")
        await self.repository.delete(account_id)
```

Если домен использует permissions, resolver всё равно возвращает роль для грубого handler gate, а
service проверяет точный permission. Не кодируйте `all_of`, `any_of`, ownership и resource policy в
handler flags.

## Границы

- app-role не является Mattermost system/team role;
- скрытая кнопка или keyboard не даёт доступ;
- обычный текст от composer keyboard проходит ту же проверку, что ручной ввод;
- разрешение одной entry-команды не разрешает все действия открытого dialog;
- callback/dialog actions и service methods должны повторно защищать изменяющие операции;
- `RBACMiddleware` не делает административный fallback и не превращает repository error в deny.

Готовое хранилище, UI управления ролями и last-admin invariant предоставляет optional
[`AdminUsersPlugin`](application-plugins.md#подключить-adminusersplugin). Core RBAC можно использовать
с любым собственным repository без этого plugin.
