"""Global entry events for the Admin Users dialog."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from mattergram.dispatcher.router import Router
from mattergram.filters import Command
from mattergram.types import Message, SlashCommand
from mattergram_dialog import DialogRef, StartMode

from .service import AdminUsersService
from .states import AdminUsersPanel


@runtime_checkable
class AdminUsersRuntime(Protocol):
    async def start(
        self,
        event: SlashCommand,
        state: object,
        data: object = None,
        *,
        mode: StartMode = StartMode.NORMAL,
    ) -> DialogRef | None: ...

    async def respond(
        self,
        event: Message,
        state: object,
        data: object = None,
    ) -> DialogRef: ...


def build_admin_users_router(
    *,
    command: str,
    messages: tuple[str, ...],
) -> Router:
    router = Router(name="admin-users")

    async def open_users(
        event: SlashCommand,
        admin_users_service: AdminUsersService,
        admin_users_runtime: AdminUsersRuntime,
    ) -> None:
        try:
            await admin_users_service.list_users(event.user_id)
        except PermissionError:
            await event.answer("Недостаточно прав для просмотра пользователей приложения")
            return
        await admin_users_runtime.start(
            event,
            AdminUsersPanel.users,
            mode=StartMode.RESET_STACK,
        )
        await event.answer()

    async def is_users_message(event: Message) -> bool:
        return event.chat.type == "D" and event.text in messages

    async def respond_users(
        event: Message,
        admin_users_service: AdminUsersService,
        admin_users_runtime: AdminUsersRuntime,
    ) -> None:
        try:
            await admin_users_service.list_users(event.user_id)
        except PermissionError:
            await event.answer("Недостаточно прав для просмотра пользователей приложения")
            return
        await admin_users_runtime.respond(event, AdminUsersPanel.users)

    open_users.__name__ = "open_admin_users"
    respond_users.__name__ = "respond_admin_users"
    router.slash_command.register(
        open_users,
        Command(command),
        description="Управление пользователями и ролями приложения",
        triggers=(command,),
    )
    router.message.register(respond_users, is_users_message)
    return router


__all__ = ["AdminUsersRuntime", "build_admin_users_router"]
