from __future__ import annotations

from datetime import UTC, datetime

ACCOUNT_STATUS_LABELS = {
    "unchecked": "Не проверялся",
    "valid": "Действителен",
    "invalid": "Недействителен",
    "error": "Ошибка проверки",
}
ROLE_LABELS = {
    "admin": "Администратор",
    "operator": "Оператор",
    "viewer": "Наблюдатель",
}
RUN_STATUS_LABELS = {
    "completed": "Завершён",
    "partial": "Частично",
    "failed": "Ошибка",
}
CHECK_STATUS_LABELS = {
    "pending": "Ожидает запуска",
    "running": "Выполняется",
    "success": "Успешно завершена",
    "failed": "Ошибка",
    "cancelled": "Отменена",
}
CHECK_STAGE_LABELS = {
    "queued": "В очереди",
    "starting": "Запуск",
    "verify": "Проверка доступности",
    "completed": "Завершено",
    "failed": "Завершено с ошибкой",
    "cancelled": "Отменено",
}
PUSH_EVENT_LABELS = {
    "import.completed": "Импорт завершён",
    "import.failed": "Ошибка импорта",
    "account.check_failed": "Ошибка проверки аккаунта",
    "account.invalid": "Недействительный аккаунт",
}
PUSH_DESTINATION_LABELS = {
    "channel": "Канал",
    "direct": "Личное сообщение",
}


def format_datetime(value: str | None, *, empty: str) -> str:
    """Format an ISO timestamp for the demo's explicit UTC presentation."""

    if value is None:
        return empty
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return "неверное значение времени"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%d.%m.%Y · %H:%M UTC")


def format_check_scope(value: str) -> str:
    if value == "all":
        return "Все аккаунты"
    if value.startswith("account:"):
        return "Аккаунт " + value.removeprefix("account:")
    if value.startswith("import:"):
        return "Импорт " + value.removeprefix("import:")
    return "Неизвестная область"


__all__ = [
    "ACCOUNT_STATUS_LABELS",
    "CHECK_STAGE_LABELS",
    "CHECK_STATUS_LABELS",
    "PUSH_DESTINATION_LABELS",
    "PUSH_EVENT_LABELS",
    "ROLE_LABELS",
    "RUN_STATUS_LABELS",
    "format_check_scope",
    "format_datetime",
]
