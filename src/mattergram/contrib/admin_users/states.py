from mattergram.fsm.state import State, StatesGroup


class AdminUsersPanel(StatesGroup):
    users = State()
    add = State()
    edit = State()


__all__ = ["AdminUsersPanel"]
