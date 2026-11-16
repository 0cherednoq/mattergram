"""Declarative list, add and edit UI for application users."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mattergram.ui.forms import Option
from mattergram_dialog import (
    ActionRow,
    ButtonRow,
    Checkbox,
    Const,
    DataBlock,
    Dialog,
    DialogManager,
    Filter,
    Format,
    FormWindow,
    OpenForm,
    PagedTable,
    PageItemSelect,
    PagePager,
    Section,
    Select,
    Window,
)
from mattergram_dialog.widgets.table import MarkdownColumn

from .actions import AdminUsersActions
from .getters import AdminUsersGetters
from .models import RoleSpec
from .states import AdminUsersPanel


def _selected_visible(data: Mapping[str, Any], manager: DialogManager) -> bool:
    selected = manager.dialog_data.get("admin_users_selected_id")
    if not isinstance(selected, str):
        return False
    table = manager.find("adminUsersPluginTable")
    page_items = getattr(table, "page_items", None)
    if not callable(page_items):
        return False
    items, _, _, _ = page_items(data, manager)
    return any(isinstance(item, Mapping) and item.get("id") == selected for item in items)


def build_admin_users_dialog(
    *,
    getters: AdminUsersGetters,
    actions: AdminUsersActions,
    roles: tuple[RoleSpec, ...],
    default_role: str,
) -> Dialog:
    """Build one reusable users dialog without embedding service implementation."""

    role_options = tuple(Option(text=role.title, value=role.id) for role in roles)
    return Dialog(
        Window(
            Section(
                "Пользователи приложения",
                Const("Роли управляют доступом только к этому боту, а не к Mattermost."),
                Const(
                    "Все активные участники команды уже добавлены.",
                    when="all_team_users_assigned",
                ),
                level=1,
            ),
            ButtonRow(
                OpenForm(
                    Const("Добавить пользователя"),
                    id="adminUsersPluginAdd",
                    state=AdminUsersPanel.add,
                    when="can_add",
                ),
                border=False,
            ),
            DataBlock(
                "Пользователи",
                PagedTable(
                    MarkdownColumn("Пользователь", "username", max_chars=24),
                    MarkdownColumn("Роль", "role_title", max_chars=20),
                    MarkdownColumn("Активен", "enabled", max_chars=8),
                    id="adminUsersPluginTable",
                    items="users",
                    page_size=8,
                    filter_by="filter",
                    empty=Const("Пользователей нет"),
                    page_label="Страница {page}/{pages} · всего {total}",
                ),
                Filter(
                    id="adminUsersPluginFilter",
                    target="adminUsersPluginTable",
                    options={"Все": None, "Активные": "enabled", "Отключённые": "disabled"},
                ),
                ActionRow(
                    PagePager(id="adminUsersPluginPager", target="adminUsersPluginTable"),
                    PageItemSelect(
                        id="adminUsersPluginSelect",
                        target="adminUsersPluginTable",
                        item_id="id",
                        item_label="username",
                        selection_key="admin_users_selected_id",
                        placeholder="Выбрать пользователя",
                        detail_title=Format("Пользователь @{item[username]}"),
                        detail=Format(
                            "**Mattermost ID:** {item[mattermost_user_id]}  \n"
                            "**Роль:** {item[role_title]} · **активен:** {item[enabled]}"
                        ),
                    ),
                ),
                OpenForm(
                    Const("Изменить выбранного"),
                    id="adminUsersPluginEdit",
                    state=AdminUsersPanel.edit,
                    when=lambda data, manager: data["can_manage"] and _selected_visible(data, manager),
                ),
                border=False,
            ),
            state=AdminUsersPanel.users,
            getter=getters.users,
        ),
        FormWindow(
            Select(
                id="mattermost_user_id",
                label="Пользователь",
                options=getters.available_user_options,
                placeholder="Выберите участника команды",
                help_text="Список перечитывается из настроенной Mattermost-команды.",
            ),
            Select(
                id="role",
                label="Роль",
                options=role_options,
                default=default_role,
                when="can_assign",
            ),
            Checkbox(id="enabled", label="Активен", default=True),
            title=Const("Добавить пользователя"),
            submit_text=Const("Добавить"),
            on_submit=actions.add,
            getter=getters.form_permissions,
            callback_id="admin_users_add",
            state=AdminUsersPanel.add,
        ),
        FormWindow(
            Select(
                id="role",
                label="Роль",
                options=role_options,
                default=lambda data, manager: data["role"],
                when="can_assign",
            ),
            Checkbox(
                id="enabled",
                label="Активен",
                default=lambda data, manager: data["enabled"],
            ),
            title=Format("Изменить @{username}"),
            introduction=Format("Mattermost ID: {mattermost_user_id}"),
            submit_text=Const("Сохранить"),
            on_submit=actions.edit,
            getter=getters.selected_user,
            callback_id="admin_users_edit",
            state=AdminUsersPanel.edit,
        ),
    )


__all__ = ["build_admin_users_dialog"]
