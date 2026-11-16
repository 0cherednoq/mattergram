from __future__ import annotations

from mattergram.fsm.state import State, StatesGroup


class AdminPanel(StatesGroup):
    home = State()
    import_menu = State()
    users = State()
    user_add = State()
    user_edit = State()
    pushes = State()
    push_add = State()
    push_edit = State()
    accounts = State()
    account_edit = State()
    account_delete = State()
    import_accounts = State()
    import_result = State()
    job_progress = State()
    import_history = State()
    check_history = State()


GAME_ADMIN_SLASH_ROUTES: dict[str, State] = {
    "overview": AdminPanel.home,
    "accounts": AdminPanel.accounts,
    "import": AdminPanel.import_menu,
    "users": AdminPanel.users,
    "pushes": AdminPanel.pushes,
    "imports": AdminPanel.import_history,
    "checks": AdminPanel.check_history,
}


GAME_ADMIN_KEYBOARD_ROUTES: dict[str, State] = {
    "Обзор": AdminPanel.home,
    "Аккаунты": AdminPanel.accounts,
}
