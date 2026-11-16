from __future__ import annotations

from mattergram import ComposerButton, ComposerKeyboard


def _button(identifier: str, text: str, *, primary: bool = False) -> ComposerButton:
    return ComposerButton(
        id="gameAdmin" + identifier.title(),
        text=text,
        message=text,
        style="primary" if primary else "default",
    )


def build_admin_keyboard() -> ComposerKeyboard:
    """Declare only the three primary administrator destinations."""

    return ComposerKeyboard(
        title="Администрирование игровых аккаунтов",
        rows=(
            (
                _button("overview", "Обзор", primary=True),
                _button("accounts", "Аккаунты"),
                _button("users", "Пользователи"),
            ),
        ),
    )


def build_operator_keyboard() -> ComposerKeyboard:
    """Declare primary account destinations without user administration."""

    return ComposerKeyboard(
        title="Игровые аккаунты",
        rows=(
            (
                _button("overview", "Обзор", primary=True),
                _button("accounts", "Аккаунты"),
            ),
        ),
    )
