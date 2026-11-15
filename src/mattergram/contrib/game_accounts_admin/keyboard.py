from __future__ import annotations

from mattergram import ComposerButton, ComposerKeyboard


def build_admin_keyboard() -> ComposerKeyboard:
    """Declare the persistent global navigation for the administrator panel."""

    def button(identifier: str, text: str, *, primary: bool = False) -> ComposerButton:
        return ComposerButton(
            id="gameAdmin" + identifier.title(),
            text=text,
            message=text,
            style="primary" if primary else "default",
        )

    return ComposerKeyboard(
        title="Администрирование игровых аккаунтов",
        rows=(
            (
                button("overview", "Обзор", primary=True),
                button("accounts", "Аккаунты"),
                button("import", "Импорт"),
            ),
            (button("users", "Пользователи"), button("pushes", "Push-настройки")),
            (button("imports", "История импортов"), button("checks", "История проверок")),
        ),
    )
