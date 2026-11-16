from __future__ import annotations

from dataclasses import dataclass

from mattergram import Dispatcher
from mattergram.dispatcher.router import Router
from mattergram.filters import Command
from mattergram.fsm.state import State
from mattergram.security import REQUIRED_ROLES_FLAG, RBACMiddleware
from mattergram.types import Message, SlashCommand
from mattergram_dialog import DialogRuntime, StartMode

from .service import GameAccountsAdminService
from .states import GAME_ADMIN_KEYBOARD_ROUTES, GAME_ADMIN_SLASH_ROUTES, AdminPanel

__all__ = [
    "GameAdminHandlerContext",
    "game_admin_router",
    "open_game_admin",
    "register_admin_panel",
    "respond_to_game_admin_keyboard",
]

game_admin_router = Router(name="game-accounts-admin")
ACCESS_DENIED_TEXT = "Недостаточно прав для администрирования игровых аккаунтов"
GAME_OPERATOR_ROLES = ("admin", "operator")
_rbac_middleware: RBACMiddleware | None = None


@dataclass(frozen=True, slots=True)
class GameAdminHandlerContext:
    """Dependencies injected into module-level game-admin handlers."""

    runtime: DialogRuntime
    service: GameAccountsAdminService


def _requested_slash_state(text: str) -> State:
    route = text.strip().split(maxsplit=1)
    return GAME_ADMIN_SLASH_ROUTES.get(route[0] if route else "", AdminPanel.home)


def _is_keyboard_command(event: Message) -> bool:
    return event.text.strip() in GAME_ADMIN_KEYBOARD_ROUTES


@game_admin_router.slash_command(
    Command("game-admin"),
    flags={REQUIRED_ROLES_FLAG: GAME_OPERATOR_ROLES},
)
async def open_game_admin(event: SlashCommand, game_admin: GameAdminHandlerContext) -> None:
    """Open the panel from the globally registered native slash command."""

    requested = _requested_slash_state(event.text)
    if requested == AdminPanel.users:
        try:
            game_admin.service.require_admin(event.user_id)
        except PermissionError:
            await event.answer(ACCESS_DENIED_TEXT)
            return
    await game_admin.runtime.start(
        event,
        requested,
        mode=StartMode.RESET_STACK,
    )
    await event.answer()


@game_admin_router.message(
    _is_keyboard_command,
    flags={REQUIRED_ROLES_FLAG: GAME_OPERATOR_ROLES},
)
async def respond_to_game_admin_keyboard(
    event: Message,
    game_admin: GameAdminHandlerContext,
) -> None:
    """Create a fresh panel post for one visible reply-keyboard message."""

    await game_admin.runtime.respond(event, GAME_ADMIN_KEYBOARD_ROUTES[event.text.strip()])


def register_admin_panel(
    dispatcher: Dispatcher,
    runtime: DialogRuntime,
    service: GameAccountsAdminService,
    *,
    command: str = "game-admin",
) -> None:
    """Bind dependencies and include the globally declared game-admin router."""

    if command != "game-admin":
        raise ValueError("The global game-admin router owns only the game-admin slash command")
    global _rbac_middleware

    context = GameAdminHandlerContext(runtime, service)
    current = dispatcher.workflow_data.get("game_admin")
    if current is not None and current != context:
        raise RuntimeError("Another game-admin handler context is already bound")
    dispatcher.workflow_data["game_admin"] = context
    if _rbac_middleware is None:
        _rbac_middleware = RBACMiddleware(service, denied_text=ACCESS_DENIED_TEXT)
        game_admin_router.message.middleware(_rbac_middleware)
        game_admin_router.slash_command.middleware(_rbac_middleware)
    elif _rbac_middleware.rbac.resolver is not service:
        raise RuntimeError("The global game-admin router already uses another role resolver")
    if game_admin_router.parent_router is None:
        dispatcher.include_router(game_admin_router)
    elif game_admin_router.parent_router is not dispatcher:
        raise RuntimeError("The global game-admin router is already attached to another dispatcher")
