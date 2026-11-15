from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from mattergram.ui.forms import Option
from mattergram_dialog import (
    ActionRow,
    Button,
    ButtonRow,
    Checkbox,
    Const,
    DataBlock,
    Dialog,
    DialogManager,
    FileInput,
    Filter,
    Format,
    FormWindow,
    MultiSelect,
    OpenForm,
    PagedTable,
    PageItemSelect,
    PagePager,
    Progress,
    Section,
    Select,
    SwitchTo,
    TextArea,
    TextInput,
    Window,
)
from mattergram_dialog.widgets.table import MarkdownColumn

from .actions import GameAdminActions
from .getters import GameAdminGetters
from .jobs import GameAdminJobs
from .models import PUSH_EVENTS
from .service import GameAccountsAdminService
from .states import AdminPanel


def _selected_visible(target: str, key: str) -> Callable[[Mapping[str, Any], DialogManager], bool]:
    def condition(data: Mapping[str, Any], manager: DialogManager) -> bool:
        selected = manager.dialog_data.get(key)
        table = manager.find(target)
        items, _, _, _ = table.page_items(data, manager)
        return isinstance(selected, str) and any(item.get("id") == selected for item in items)

    return condition


def build_admin_dialog(
    service: GameAccountsAdminService,
    *,
    jobs: GameAdminJobs | None = None,
) -> Dialog:
    """Build the complete administrator panel against one application service."""

    getters = GameAdminGetters(service)
    actions = GameAdminActions(service, jobs)
    role_options = tuple(
        Option(text=label, value=value)
        for label, value in (("Администратор", "admin"), ("Оператор", "operator"), ("Наблюдатель", "viewer"))
    )
    destination_options = (
        Option(text="Канал", value="channel"),
        Option(text="Личное сообщение", value="direct"),
    )
    event_options = tuple(Option(text=value, value=value) for value in PUSH_EVENTS)
    game_options = tuple(Option(text=game, value=game) for game in service.games)

    return Dialog(
        Window(
            Section(
                "Администрирование игровых аккаунтов",
                Const("Пользователи, уведомления, импорт и проверки через reply-команды бота."),
                Format(
                    "**Аккаунты:** {accounts} · проблемных {invalid_accounts}  \n"
                    "**Администраторы:** {users} · push-настроек {push_rules}  \n"
                    "**Сводка обновлена:** {refreshed_at}"
                ),
                Const("Основные разделы доступны в keyboard-меню над полем ввода."),
                level=1,
            ),
            ButtonRow(
                Button(Const("Обновить сводку"), id="adminHomeRefresh", on_click=actions.refresh_home),
                title="Сводка",
            ),
            state=AdminPanel.home,
            getter=getters.home,
        ),
        Window(
            Section(
                "Импорт аккаунтов",
                Const(
                    "Mattermost открывает нативную форму только после интерактивного действия. "
                    "Подтвердите открытие формы кнопкой ниже."
                ),
                level=1,
            ),
            ButtonRow(
                OpenForm(
                    Const("Открыть форму импорта"),
                    id="adminImportOpen",
                    state=AdminPanel.import_accounts,
                ),
                SwitchTo(Const("Назад"), id="adminImportMenuBack", state=AdminPanel.home),
                title="Импорт",
            ),
            state=AdminPanel.import_menu,
        ),
        Window(
            Section(
                "Пользователи",
                Const("Доступ к панели выдаётся только активной роли admin приложения."),
                Const(
                    "Все активные пользователи команды уже добавлены в приложение.",
                    when=lambda data, manager: not data["has_available_users"],
                ),
                level=1,
            ),
            DataBlock(
                "Пользователи",
                PagedTable(
                    MarkdownColumn("Пользователь", "username", max_chars=24),
                    MarkdownColumn("Роль", "role", max_chars=12),
                    MarkdownColumn("Активен", "enabled", max_chars=8),
                    id="adminUsersTable",
                    items="users",
                    page_size=8,
                    filter_by="filter",
                    empty=Const("Пользователей нет"),
                    page_label="Страница {page}/{pages} · всего {total}",
                ),
                Filter(
                    id="adminUsersFilter",
                    target="adminUsersTable",
                    options={"Все": None, "Активные": "enabled", "Отключённые": "disabled"},
                ),
                ActionRow(
                    PagePager(id="adminUsersPager", target="adminUsersTable"),
                    PageItemSelect(
                        id="adminUserSelect",
                        target="adminUsersTable",
                        item_id="id",
                        item_label="username",
                        selection_key="selected_user_id",
                        placeholder="Выбрать пользователя",
                        detail_title=Format("Пользователь {item[username]}"),
                        detail=Format(
                            "**Mattermost ID:** {item[mattermost_user_id]}  \n"
                            "**Роль:** {item[role]} · **активен:** {item[enabled]}"
                        ),
                    ),
                ),
                OpenForm(
                    Const("Изменить выбранного"),
                    id="adminUserEdit",
                    state=AdminPanel.user_edit,
                    when=_selected_visible("adminUsersTable", "selected_user_id"),
                ),
            ),
            ButtonRow(
                OpenForm(
                    Const("Добавить пользователя"),
                    id="adminUserAdd",
                    state=AdminPanel.user_add,
                    when="has_available_users",
                ),
                SwitchTo(Const("Назад"), id="adminUsersBack", state=AdminPanel.home),
                title="Навигация",
            ),
            state=AdminPanel.users,
            getter=getters.users,
        ),
        FormWindow(
            Select(
                id="mattermost_user_id",
                label="Пользователь",
                options=getters.available_user_options,
                placeholder="Выберите участника команды",
                help_text="Список содержит активных людей выбранной Mattermost-команды.",
            ),
            Select(id="role", label="Роль", options=role_options, default="operator"),
            Checkbox(id="enabled", label="Активен", default=True),
            title=Const("Добавить пользователя"),
            submit_text=Const("Добавить"),
            on_submit=actions.add_user,
            callback_id="game_admin_user_add",
            state=AdminPanel.user_add,
        ),
        FormWindow(
            TextInput(id="username", label="Имя", max_length=64, default=Format("{username}")),
            Select(id="role", label="Роль", options=role_options, default=lambda data, manager: data["role"]),
            Checkbox(id="enabled", label="Активен", default=lambda data, manager: data["enabled"] == "да"),
            title=Format("Изменить {username}"),
            introduction=Format("Mattermost ID: {mattermost_user_id}"),
            submit_text=Const("Сохранить"),
            on_submit=actions.edit_user,
            getter=getters.selected_user,
            callback_id="game_admin_user_edit",
            state=AdminPanel.user_edit,
        ),
        Window(
            Section("Push-настройки", Const("Куда отправлять уведомления и на какие события."), level=1),
            DataBlock(
                "Маршруты уведомлений",
                PagedTable(
                    MarkdownColumn("Назначение", "destination", max_chars=30),
                    MarkdownColumn("События", "events", max_chars=46),
                    MarkdownColumn("Включено", "enabled", max_chars=8),
                    id="adminPushTable",
                    items="pushes",
                    page_size=6,
                    filter_by="filter",
                    empty=Const("Push-маршрутов нет"),
                    page_label="Страница {page}/{pages} · всего {total}",
                ),
                Filter(
                    id="adminPushFilter",
                    target="adminPushTable",
                    options={"Все": None, "Включённые": "enabled", "Отключённые": "disabled"},
                ),
                ActionRow(
                    PagePager(id="adminPushPager", target="adminPushTable"),
                    PageItemSelect(
                        id="adminPushSelect",
                        target="adminPushTable",
                        item_id="id",
                        item_label="destination",
                        selection_key="selected_push_id",
                        placeholder="Выбрать маршрут",
                        detail_title=Format("Push {item[destination]}"),
                        detail=Format("**События:** {item[events]}  \n**Включено:** {item[enabled]}"),
                    ),
                ),
                OpenForm(
                    Const("Изменить маршрут"),
                    id="adminPushEdit",
                    state=AdminPanel.push_edit,
                    when=_selected_visible("adminPushTable", "selected_push_id"),
                ),
            ),
            ButtonRow(
                OpenForm(Const("Добавить маршрут"), id="adminPushAdd", state=AdminPanel.push_add),
                SwitchTo(Const("Назад"), id="adminPushBack", state=AdminPanel.home),
                title="Навигация",
            ),
            state=AdminPanel.pushes,
            getter=getters.pushes,
        ),
        FormWindow(
            Select(id="destination_kind", label="Куда", options=destination_options, default="channel"),
            TextInput(id="destination_id", label="Channel ID / user ID", max_length=64),
            MultiSelect(id="events", label="События", options=event_options, default_values=PUSH_EVENTS),
            Checkbox(id="enabled", label="Включено", default=True),
            title=Const("Добавить push-маршрут"),
            submit_text=Const("Добавить"),
            on_submit=actions.add_push,
            callback_id="game_admin_push_add",
            state=AdminPanel.push_add,
        ),
        FormWindow(
            Select(
                id="destination_kind",
                label="Куда",
                options=destination_options,
                default=lambda data, manager: data["destination_kind"],
            ),
            TextInput(
                id="destination_id",
                label="Channel ID / user ID",
                max_length=64,
                default=Format("{destination_id}"),
            ),
            MultiSelect(
                id="events",
                label="События",
                options=event_options,
                default_values=lambda data, manager: data["event_values"],
            ),
            Checkbox(
                id="enabled",
                label="Включено",
                default=lambda data, manager: data["enabled_bool"],
            ),
            title=Const("Изменить push-маршрут"),
            submit_text=Const("Сохранить"),
            on_submit=actions.edit_push,
            getter=getters.selected_push,
            callback_id="game_admin_push_edit",
            state=AdminPanel.push_edit,
        ),
        Window(
            Section(
                "Игровые аккаунты",
                Const("Пароли и значения cookies не показываются ни в таблице, ни в карточке."),
                Format("{check_summary}", when="check_summary"),
                level=1,
            ),
            DataBlock(
                "Аккаунты",
                PagedTable(
                    MarkdownColumn("Игра", "game", max_chars=18),
                    MarkdownColumn("Логин", "login", max_chars=28),
                    MarkdownColumn("Статус", "status", max_chars=10),
                    MarkdownColumn("Cookies", "cookie_count", max_chars=8, align="right"),
                    id="adminAccountsTable",
                    items="accounts",
                    page_size=6,
                    filter_by="filter",
                    empty=Const("Аккаунтов нет"),
                    page_label="Страница {page}/{pages} · всего {total}",
                ),
                Filter(
                    id="adminAccountsFilter",
                    target="adminAccountsTable",
                    options={
                        "Все": None,
                        "Не проверены": "unchecked",
                        "Валидные": "valid",
                        "Невалидные": "invalid",
                        "Ошибки": "error",
                    },
                ),
                ActionRow(
                    PagePager(id="adminAccountsPager", target="adminAccountsTable"),
                    PageItemSelect(
                        id="adminAccountSelect",
                        target="adminAccountsTable",
                        item_id="id",
                        item_label="login",
                        selection_key="selected_account_id",
                        placeholder="Карточка аккаунта",
                        detail_title=Format("{item[game]} · {item[login]}"),
                        detail=Format(
                            "**Статус:** {item[status]}  \n"
                            "**Пароль:** {item[password_state]} (значение скрыто)  \n"
                            "**Cookies:** {item[cookie_count]} · домены: {item[cookie_domains]}  \n"
                            "**Последняя проверка:** {item[last_checked_at]}  \n"
                            "**Последняя ошибка:** {item[last_error]}"
                        ),
                    ),
                ),
                ActionRow(
                    Button(
                        Const("Проверить выбранный"),
                        id="adminAccountCheck",
                        on_click=actions.check_selected,
                        when=_selected_visible("adminAccountsTable", "selected_account_id"),
                    ),
                    OpenForm(
                        Const("Изменить учётные данные"),
                        id="adminAccountEdit",
                        state=AdminPanel.account_edit,
                        when=_selected_visible("adminAccountsTable", "selected_account_id"),
                    ),
                ),
            ),
            ButtonRow(
                Button(Const("Проверить все"), id="adminCheckAll", on_click=actions.check_all),
                OpenForm(Const("Импортировать"), id="adminAccountsImport", state=AdminPanel.import_accounts),
                SwitchTo(Const("Назад"), id="adminAccountsBack", state=AdminPanel.home),
                title="Навигация",
            ),
            state=AdminPanel.accounts,
            getter=getters.accounts,
        ),
        FormWindow(
            TextInput(id="login", label="Логин", max_length=150, default=Format("{login}")),
            TextInput(
                id="password",
                label="Новый пароль",
                subtype="password",
                max_length=150,
                optional=True,
                help_text="Оставьте пустым, чтобы сохранить текущий пароль",
            ),
            TextArea(
                id="cookies_json",
                label="Новые cookies JSON",
                max_length=3000,
                optional=True,
                help_text="Массив объектов domain/name/value; пусто — сохранить текущие cookies",
            ),
            title=Format("Учётные данные {login}"),
            introduction=Const("Текущий пароль и значения cookies намеренно не подставляются в форму."),
            submit_text=Const("Сохранить"),
            on_submit=actions.edit_account,
            getter=getters.selected_account,
            callback_id="game_admin_account_edit",
            state=AdminPanel.account_edit,
        ),
        FormWindow(
            Select(id="game", label="Игра", options=game_options),
            FileInput(
                id="import_file",
                label="JSONL-файл",
                help_text=(
                    'Одна строка: {"login":"...","password":"...",'
                    '"cookies":[{"domain":"...","name":"...","value":"..."}]}'
                ),
            ),
            title=Const("Импорт аккаунтов"),
            introduction=Const(
                "До 500 строк и 1 МБ. Сырые пароли/cookies не сохраняются в истории и не выводятся."
            ),
            submit_text=Const("Импортировать"),
            on_submit=actions.import_accounts,
            callback_id="game_admin_import",
            state=AdminPanel.import_accounts,
        ),
        Window(
            Section(
                "Импорт завершён",
                Format(
                    "**Статус:** {import_status}  \n**Всего:** {import_total} · "
                    "**добавлено:** {import_imported} · **отклонено:** {import_rejected}"
                ),
                Format("**Ошибки:** {import_issues}"),
                Format("{check_summary}", when="check_summary"),
                level=1,
            ),
            ButtonRow(
                Button(
                    Const("Проверить импортированные"),
                    id="adminCheckImported",
                    on_click=actions.check_imported,
                    when=lambda data, manager: data.get("import_imported", 0) > 0,
                ),
                SwitchTo(Const("Открыть аккаунты"), id="adminResultAccounts", state=AdminPanel.accounts),
                SwitchTo(
                    Const("История импортов"),
                    id="adminResultHistory",
                    state=AdminPanel.import_history,
                ),
                SwitchTo(Const("Главная"), id="adminResultHome", state=AdminPanel.home),
                title="Дальше",
            ),
            state=AdminPanel.import_result,
        ),
        Window(
            Section(
                "Фоновая операция",
                Format("**Статус:** {job_state}  \n**Этап:** {job_stage}"),
                Progress(
                    current="job_current",
                    total="job_total",
                    label=Const("Прогресс"),
                ),
                Format("**Ошибка:** {job_error_type}", when="job_error_type"),
                level=1,
            ),
            ButtonRow(
                Button(
                    Const("Отменить"),
                    id="adminJobCancel",
                    on_click=actions.cancel_job,
                    when=lambda data, manager: (
                        data.get("job_state") in {"queued", "running"}
                        and not data.get("job_cancel_requested", False)
                    ),
                ),
                SwitchTo(Const("Аккаунты"), id="adminJobAccounts", state=AdminPanel.accounts),
                SwitchTo(Const("Главная"), id="adminJobHome", state=AdminPanel.home),
                title="Управление",
            ),
            state=AdminPanel.job_progress,
        ),
        Window(
            Section("История импортов", Const("Без паролей, cookie values и сырого файла."), level=1),
            DataBlock(
                "Импорты",
                PagedTable(
                    MarkdownColumn("Дата", "created_at", max_chars=25),
                    MarkdownColumn("Игра", "game", max_chars=18),
                    MarkdownColumn("Результат", "result", max_chars=12),
                    MarkdownColumn("Статус", "status", max_chars=10),
                    id="adminImportsTable",
                    items="imports",
                    page_size=8,
                    filter_by="filter",
                    empty=Const("Импортов ещё не было"),
                    page_label="Страница {page}/{pages} · всего {total}",
                ),
                Filter(
                    id="adminImportsFilter",
                    target="adminImportsTable",
                    options={
                        "Все": None,
                        "Успешные": "completed",
                        "Частичные": "partial",
                        "Ошибки": "failed",
                    },
                ),
                ActionRow(
                    PagePager(id="adminImportsPager", target="adminImportsTable"),
                    PageItemSelect(
                        id="adminImportSelect",
                        target="adminImportsTable",
                        item_id="id",
                        item_label="created_at",
                        selection_key="selected_import_id",
                        placeholder="Детали импорта",
                        detail_title=Format("Импорт {item[id]}"),
                        detail=Format(
                            "**Игра:** {item[game]} · **результат:** {item[result]}  \n"
                            "**Статус:** {item[status]}  \n**Ошибки:** {item[issues]}"
                        ),
                    ),
                ),
            ),
            ButtonRow(
                SwitchTo(Const("Назад"), id="adminImportsBack", state=AdminPanel.home),
                title="Навигация",
            ),
            state=AdminPanel.import_history,
            getter=getters.imports,
        ),
        Window(
            Section("История проверок", Const("Отдельные и пакетные проверки аккаунтов."), level=1),
            DataBlock(
                "Проверки",
                PagedTable(
                    MarkdownColumn("Дата", "created_at", max_chars=25),
                    MarkdownColumn("Область", "scope", max_chars=28),
                    MarkdownColumn("Результат", "result", max_chars=42),
                    MarkdownColumn("Статус", "status", max_chars=10),
                    id="adminChecksTable",
                    items="checks",
                    page_size=8,
                    filter_by="filter",
                    empty=Const("Проверок ещё не было"),
                    page_label="Страница {page}/{pages} · всего {total}",
                ),
                Filter(
                    id="adminChecksFilter",
                    target="adminChecksTable",
                    options={
                        "Все": None,
                        "Успешные": "completed",
                        "Частичные": "partial",
                        "Ошибки": "failed",
                    },
                ),
                ActionRow(
                    PagePager(id="adminChecksPager", target="adminChecksTable"),
                    PageItemSelect(
                        id="adminCheckSelect",
                        target="adminChecksTable",
                        item_id="id",
                        item_label="created_at",
                        selection_key="selected_check_id",
                        placeholder="Детали проверки",
                        detail_title=Format("Проверка {item[id]}"),
                        detail=Format(
                            "**Область:** {item[scope]} · **аккаунтов:** {item[total]}  \n"
                            "**Результат:** {item[result]} · **статус:** {item[status]}"
                        ),
                    ),
                ),
            ),
            ButtonRow(
                SwitchTo(Const("Назад"), id="adminChecksBack", state=AdminPanel.home),
                title="Навигация",
            ),
            state=AdminPanel.check_history,
            getter=getters.checks,
        ),
    )
