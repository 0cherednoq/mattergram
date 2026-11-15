"""Run the persistent local mattergram_dialog navigation demo.

The slash command creates one editable post. Use ``dialog live`` as a regular
channel message to exercise a WebSocket-triggered REST refresh of that post.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
from pydantic import SecretStr

from mattergram import Bot, Dispatcher, F
from mattergram.filters import Command
from mattergram.fsm.state import State, StatesGroup
from mattergram.fsm.storage.base import DefaultKeyBuilder
from mattergram.fsm.storage.redis import RedisEventIsolation, RedisStorage
from mattergram.http import CallbackSigner, HttpAdapter, SlashBinding
from mattergram.http import DialogManager as NativeDialogManager
from mattergram.types import DialogSubmission, Message, SlashCommand
from mattergram.ui import UIProfile
from mattergram.ui.actions import ActionContext
from mattergram.ui.capabilities import SERVER_FEATURES
from mattergram.ui.forms import Option
from mattergram_dialog import (
    ActionColumn,
    ActionRow,
    ActionTable,
    Back,
    ButtonGrid,
    ButtonRow,
    Checkbox,
    Const,
    CursorList,
    CursorPage,
    CursorPager,
    DataBlock,
    DatePicker,
    Dialog,
    DialogActions,
    DialogManager,
    DialogRegistry,
    DialogRuntime,
    DialogStorage,
    Filter,
    Format,
    FormWindow,
    MarkdownColumn,
    OpenForm,
    PagedTable,
    PageItemSelect,
    PagePager,
    RowAction,
    Section,
    Select,
    ShowMode,
    StartMode,
    SwitchTo,
    TextArea,
    TextInput,
    Window,
    setup_dialogs,
)
from mattergram_dialog.rendering import MattermostRenderer

logger = logging.getLogger(__name__)


class Demo(StatesGroup):
    home = State()
    reports = State()
    orders = State()
    incidents = State()
    activity = State()
    help = State()
    order_edit = State()
    order_delete = State()
    form = State()
    confirmation = State()


ORDERS = (
    {"id": "ORD-1042", "status": "open", "owner": "Ada"},
    {"id": "ORD-1041", "status": "paid", "owner": "Lin"},
    {"id": "ORD-1040", "status": "open", "owner": "Max"},
    {"id": "ORD-1039", "status": "cancelled", "owner": "Zoe"},
    {"id": "ORD-1038", "status": "paid", "owner": "Ivy"},
    {"id": "ORD-1037", "status": "open", "owner": "Sam"},
)
INCIDENTS = (
    {"id": "INC-88", "severity": "p1", "service": "Checkout"},
    {"id": "INC-87", "severity": "p2", "service": "Search"},
    {"id": "INC-86", "severity": "p2", "service": "Billing"},
    {"id": "INC-85", "severity": "p3", "service": "Email"},
    {"id": "INC-84", "severity": "p1", "service": "API"},
)
ACTIVITY = tuple(
    {"id": f"EVT-{index:03d}", "action": action}
    for index, action in enumerate(
        (
            "Order created",
            "Payment received",
            "Incident acknowledged",
            "Order shipped",
            "Profile updated",
            "Export completed",
            "Webhook delivered",
        ),
        start=1,
    )
)


@dataclass(slots=True)
class DemoData:
    live_revision: int = 0
    last_live_event: str = "WS-событий ещё не было"
    orders: list[dict[str, str]] = field(default_factory=lambda: [dict(order) for order in ORDERS])


@dataclass(frozen=True, slots=True)
class DemoSession:
    channel_id: str
    user_id: str
    post_id: str
    team_id: str | None


async def provision_owned_local_stand(state_path: str) -> None:
    """Register only /dialog-demo on the repository-owned local stand."""

    state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    base_url = os.environ.get("MM_URL", "http://127.0.0.1:12854").rstrip("/")
    callback_base = os.environ.get("MM_CALLBACK_BASE_URL", "http://host.docker.internal:18765").rstrip("/")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.post(
            base_url + "/api/v4/users/login",
            json={
                "login_id": state["admin_username"],
                "password": state["admin_password"],
            },
        ) as response:
            if response.status != 200 or not response.headers.get("Token"):
                raise RuntimeError("Owned local Mattermost admin login failed")
            admin_token = response.headers["Token"]
        headers = {"Authorization": "Bearer " + admin_token}
        async with session.get(
            base_url + "/api/v4/commands",
            params={"team_id": state["team_id"], "custom_only": "true"},
            headers=headers,
        ) as response:
            if response.status != 200:
                raise RuntimeError("Unable to list owned local slash commands")
            commands = await response.json()
        for command in commands:
            if command.get("trigger") != "dialog-demo":
                continue
            async with session.delete(
                base_url + "/api/v4/commands/" + command["id"], headers=headers
            ) as response:
                if response.status not in (200, 204):
                    raise RuntimeError("Unable to replace owned /dialog-demo command")
        async with session.post(
            base_url + "/api/v4/commands",
            headers=headers,
            json={
                "team_id": state["team_id"],
                "trigger": "dialog-demo",
                "method": "P",
                "url": callback_base + "/mattergram/slash",
                "display_name": "mattergram_dialog interactive demo",
                "description": "Path-aware Back, tables, pagination, filters and forms",
                "auto_complete": True,
                "auto_complete_desc": "Open the interactive mattergram_dialog demo",
            },
        ) as response:
            if response.status != 201:
                raise RuntimeError("Unable to register owned /dialog-demo command")
            command = await response.json()

    os.environ.update(
        MM_URL=base_url,
        MM_BOT_TOKEN=state["bot_token"],
        MM_TEAM_ID=state["team_id"],
        MM_SLASH_TOKEN=command["token"],
        MM_CALLBACK_BASE_URL=callback_base,
        MM_ACTION_SIGNING_KEY_HEX=secrets.token_hex(32),
        MM_HTTP_TRUSTED_PEERS="172.16.0.0/12,127.0.0.1/32",
        MM_HTTP_HOST="0.0.0.0",
        MM_HTTP_PORT=os.environ.get("MM_HTTP_PORT", "18765"),
        MM_REDIS_URL=os.environ.get("MM_REDIS_URL", "redis://127.0.0.1:47851/0"),
        MM_DIALOG_PREFIX=os.environ.get("MM_DIALOG_PREFIX", "dialog-demo-20261113"),
        MM_UI_PROFILE_VERSION=str(state.get("profile_version", state.get("server_version", "11.1.0"))),
        MM_BLOCKS_ENABLED="true" if state.get("mm_blocks_enabled") is True else "false",
    )
    logger.info("Registered /dialog-demo on the owned local Mattermost stand")


def build_dialog(data: DemoData) -> Dialog:
    async def load_data() -> dict[str, Any]:
        return {
            "orders": tuple(data.orders),
            "incidents": INCIDENTS,
            "live_revision": data.live_revision,
            "last_live_event": data.last_live_event,
        }

    def selected_order(manager: DialogManager) -> dict[str, str]:
        selected = manager.dialog_data.get("selected_order_id")
        for order in data.orders:
            if order["id"] == selected:
                return order
        raise ValueError("Выбранный заказ больше не существует")

    async def load_selected_order(manager: DialogManager) -> dict[str, str]:
        order = selected_order(manager)
        return {
            "selected_order_id": order["id"],
            "selected_order_status": order["status"],
            "selected_order_owner": order["owner"],
        }

    async def save_order(event: DialogSubmission, manager: DialogManager) -> None:
        order = selected_order(manager)
        order["status"] = event.values["order_status"]
        order["owner"] = event.values["order_owner"]
        await event.answer()
        await manager.back(show_mode=ShowMode.EDIT)

    async def delete_order(event: DialogSubmission, manager: DialogManager) -> None:
        if not event.values["confirm_order_delete"]:
            await event.field_errors({"confirm_order_delete": "Подтвердите удаление выбранного заказа"})
            return
        order = selected_order(manager)
        data.orders.remove(order)
        await event.answer()
        await manager.back(show_mode=ShowMode.EDIT)

    async def load_activity(cursor: str | None, limit: int, manager: Any) -> CursorPage:
        del manager
        offset = int(cursor or "0")
        items = ACTIVITY[offset : offset + limit]
        next_offset = offset + len(items)
        return CursorPage(items, str(next_offset) if next_offset < len(ACTIVITY) else None)

    async def save(event: DialogSubmission, manager: DialogManager) -> None:
        manager.dialog_data["title"] = event.values["title"]
        manager.dialog_data["note"] = event.values["note"]
        manager.dialog_data["priority"] = event.values["priority"]
        due = event.values["due"]
        manager.dialog_data["due"] = due.isoformat() if due is not None else "—"
        manager.dialog_data["notifications"] = event.values["notifications"]
        await event.answer()
        await manager.switch_to(Demo.confirmation, show_mode=ShowMode.EDIT)

    return Dialog(
        Window(
            Section(
                "mattergram_dialog demo",
                Const("Один пост работает как небольшое приложение с понятными блоками."),
                Format("**Live revision:** {live_revision}  \n{last_live_event}"),
                level=1,
            ),
            ButtonGrid(
                ActionRow(
                    SwitchTo(Const("Сводка"), id="reports", state=Demo.reports),
                    SwitchTo(Const("Заказы"), id="orders", state=Demo.orders),
                ),
                ActionRow(
                    SwitchTo(Const("Инциденты"), id="incidents", state=Demo.incidents),
                    SwitchTo(Const("Cursor API"), id="activity", state=Demo.activity),
                ),
                title="Разделы",
                color="#3d85c6",
            ),
            state=Demo.home,
            getter=load_data,
        ),
        Window(
            Section(
                "Сводный отчёт",
                Const("Каждая таблица и все действия над ней находятся в одном локальном блоке."),
                level=1,
            ),
            DataBlock(
                "Заказы",
                PagedTable(
                    MarkdownColumn("Заказ", "id", max_chars=12),
                    MarkdownColumn("Статус", "status", max_chars=12),
                    MarkdownColumn("Владелец", "owner", max_chars=14),
                    id="reportOrderRows",
                    items="orders",
                    page_size=2,
                    filter_by="status",
                    page_label="Страница {page}/{pages} · всего {total}",
                ),
                Filter(
                    id="reportOrderFilter",
                    target="reportOrderRows",
                    options={"Все": None, "Open": "open", "Paid": "paid"},
                ),
                ActionRow(
                    PagePager(id="reportOrderPager", target="reportOrderRows"),
                    PageItemSelect(
                        id="reportOrderSelect",
                        target="reportOrderRows",
                        item_id="id",
                        item_label="id",
                        selection_key="selected_report_order_id",
                        placeholder="Выбрать заказ",
                        detail_title=Format("Заказ {item[id]}"),
                        detail=Format("**Статус:** {item[status]}  \n**Владелец:** {item[owner]}"),
                        detail_color="#3d85c6",
                    ),
                ),
            ),
            DataBlock(
                "Инциденты",
                PagedTable(
                    MarkdownColumn("ID", "id", max_chars=12),
                    MarkdownColumn("Важность", "severity", max_chars=12),
                    MarkdownColumn("Сервис", "service", max_chars=18),
                    id="reportIncidentRows",
                    items="incidents",
                    page_size=2,
                    filter_by="severity",
                    page_label="Страница {page}/{pages} · всего {total}",
                ),
                Filter(
                    id="reportIncidentFilter",
                    target="reportIncidentRows",
                    options={"Все": None, "P1": "p1", "P2": "p2"},
                ),
                ActionRow(
                    PagePager(id="reportIncidentPager", target="reportIncidentRows"),
                    PageItemSelect(
                        id="reportIncidentSelect",
                        target="reportIncidentRows",
                        item_id="id",
                        item_label="id",
                        selection_key="selected_report_incident_id",
                        placeholder="Выбрать инцидент",
                        detail_title=Format("Инцидент {item[id]}"),
                        detail=Format("**Важность:** {item[severity]}  \n**Сервис:** {item[service]}"),
                        detail_color="#cc0000",
                    ),
                ),
            ),
            ButtonRow(
                SwitchTo(Const("Справка"), id="reportHelp", state=Demo.help),
                OpenForm(Const("Настройки"), id="configure", state=Demo.form),
                Back(Const("Назад"), id="reportBack"),
                title="Навигация",
            ),
            state=Demo.reports,
            getter=load_data,
        ),
        Window(
            Section(
                "Заказы",
                Const(
                    "Native Blocks формируют настоящую горизонтальную строку: "
                    "три поля заказа и две локальные кнопки."
                ),
                level=1,
            ),
            ActionTable(
                ActionColumn("Заказ", "id", max_chars=12),
                ActionColumn("Статус", "status", max_chars=12),
                ActionColumn("Владелец", "owner", max_chars=14),
                id="orderActions",
                title="Список заказов",
                items="orders",
                item_id="id",
                selection_key="selected_order_id",
                actions=(
                    RowAction(
                        "Edit",
                        "Изменить",
                        Demo.order_edit,
                        style="primary",
                    ),
                    RowAction(
                        "Delete",
                        "Удалить",
                        Demo.order_delete,
                        style="danger",
                    ),
                ),
            ),
            ButtonRow(
                SwitchTo(Const("Справка"), id="orderHelp", state=Demo.help),
                Back(Const("Назад"), id="orderBack"),
                title="Навигация",
            ),
            state=Demo.orders,
            getter=load_data,
        ),
        Window(
            Section(
                "Инциденты",
                Const("Фильтры находятся над таблицей, а выбор строки и пагинация — под ней."),
                level=1,
            ),
            DataBlock(
                "Список инцидентов",
                PagedTable(
                    MarkdownColumn("ID", "id", max_chars=12),
                    MarkdownColumn("Важность", "severity", max_chars=12),
                    MarkdownColumn("Сервис", "service", max_chars=18),
                    id="incidentRows",
                    items="incidents",
                    page_size=2,
                    filter_by="severity",
                    page_label="Страница {page}/{pages} · всего {total}",
                ),
                Filter(
                    id="incidentFilter",
                    target="incidentRows",
                    options={"Все": None, "P1": "p1", "P2": "p2"},
                ),
                ActionRow(
                    PagePager(id="incidentPager", target="incidentRows"),
                    PageItemSelect(
                        id="incidentSelect",
                        target="incidentRows",
                        item_id="id",
                        item_label="id",
                        selection_key="selected_incident_id",
                        placeholder="Выбрать инцидент",
                        detail_title=Format("Инцидент {item[id]}"),
                        detail=Format("**Важность:** {item[severity]}  \n**Сервис:** {item[service]}"),
                        detail_color="#cc0000",
                    ),
                ),
            ),
            ButtonRow(
                SwitchTo(Const("Справка"), id="incidentHelp", state=Demo.help),
                Back(Const("Назад"), id="incidentBack"),
                title="Навигация",
            ),
            state=Demo.incidents,
            getter=load_data,
        ),
        Window(
            Section(
                "Cursor API",
                Const("Курсоры непрозрачны: интерфейс показывает только доступные переходы."),
                level=1,
            ),
            DataBlock(
                "Лента событий",
                CursorList(
                    Format("- **{item[id]}** · {item[action]}"),
                    id="activityRows",
                    loader=load_activity,
                    page_size=3,
                    page_label="Страница {page}",
                ),
                CursorPager(id="activityPager", target="activityRows"),
            ),
            ButtonRow(Back(Const("Назад"), id="activityBack"), title="Навигация"),
            state=Demo.activity,
        ),
        Window(
            Section(
                "Общая справка",
                Const(
                    "**Назад** возвращает по фактическому пути: из Заказов — в Заказы, "
                    "из Инцидентов — в Инциденты."
                ),
                Const("Фильтры, пагинация и навигация разделены по стабильным строкам."),
                level=1,
            ),
            ButtonRow(
                Back(Const("Назад по фактическому пути"), id="helpBack"),
                title="Навигация",
            ),
            state=Demo.help,
        ),
        FormWindow(
            Select(
                id="order_status",
                label="Новый статус",
                options=(
                    Option(text="Open", value="open"),
                    Option(text="Paid", value="paid"),
                    Option(text="Cancelled", value="cancelled"),
                ),
            ),
            TextInput(id="order_owner", label="Владелец", max_length=40),
            title=Format("Редактировать {selected_order_id}"),
            introduction=Format("Сейчас: **{selected_order_status}**, владелец **{selected_order_owner}**"),
            submit_text=Const("Сохранить заказ"),
            on_submit=save_order,
            getter=load_selected_order,
            callback_id="dialog_navigation_order_edit",
            state=Demo.order_edit,
        ),
        FormWindow(
            Checkbox(
                id="confirm_order_delete",
                label="Подтверждаю",
                help_text="Выбранный заказ будет удалён из демонстрационного списка",
                default=False,
            ),
            title=Format("Удалить {selected_order_id}?"),
            introduction=Format(
                "Заказ **{selected_order_id}**, статус **{selected_order_status}**, "
                "владелец **{selected_order_owner}**."
            ),
            submit_text=Const("Удалить заказ"),
            on_submit=delete_order,
            getter=load_selected_order,
            callback_id="dialog_navigation_order_delete",
            state=Demo.order_delete,
        ),
        FormWindow(
            TextInput(id="title", label="Название", max_length=80),
            TextArea(id="note", label="Комментарий", max_length=500, optional=True),
            Select(
                id="priority",
                label="Приоритет",
                options=(
                    Option(text="Обычный", value="normal"),
                    Option(text="Высокий", value="high"),
                ),
                default="normal",
            ),
            DatePicker(id="due", label="Срок", optional=True),
            Checkbox(id="notifications", label="Уведомлять", default=True),
            title=Const("Карточка"),
            submit_text=Const("Сохранить"),
            on_submit=save,
            callback_id="dialog_navigation_demo_form",
            state=Demo.form,
        ),
        Window(
            Section(
                "Настройки сохранены",
                Format("**Название:** {title}"),
                Format("**Приоритет:** {priority}"),
                Format("**Срок:** {due}"),
                Format("**Комментарий:** {note}"),
                Format("**Уведомления:** {notifications}"),
                level=1,
            ),
            ButtonRow(
                SwitchTo(Const("В главное меню"), id="confirmationHome", state=Demo.home),
                Back(Const("Назад"), id="confirmationBack"),
                title="Навигация",
            ),
            state=Demo.confirmation,
        ),
    )


async def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    if state_path := os.environ.get("MM_LOCAL_STAND_STATE"):
        await provision_owned_local_stand(state_path)
    base_url = os.environ["MM_URL"].rstrip("/")
    callback_base = os.environ["MM_CALLBACK_BASE_URL"].rstrip("/")
    callback_url = callback_base + "/mattergram/callback"
    bot = Bot(token=os.environ["MM_BOT_TOKEN"], base_url=base_url)
    dispatcher = Dispatcher()
    key_builder = DefaultKeyBuilder(prefix=os.environ.get("MM_DIALOG_PREFIX", "dialog-demo"))
    storage = RedisStorage.from_url(os.environ["MM_REDIS_URL"], key_builder=key_builder)
    isolation = RedisEventIsolation.from_url(os.environ["MM_REDIS_URL"], key_builder=key_builder)
    dialog_storage = DialogStorage(storage, isolation)
    sessions: dict[tuple[str, str], DemoSession] = {}
    demo_data = DemoData()

    try:
        identity = await bot.get_me()
        action_ttl = float(os.environ.get("MM_ACTION_TTL_SECONDS", 7 * 24 * 60 * 60))
        signer = CallbackSigner(
            instance_id=bot.instance_id,
            bot_id=identity.id,
            keys={"demo": bytes.fromhex(os.environ["MM_ACTION_SIGNING_KEY_HEX"])},
            active_key_id="demo",
            max_ttl=action_ttl,
        )
        profile_version = os.environ.get("MM_UI_PROFILE_VERSION", "12.0.2")
        if profile_version not in {"11.11.0", "12.0.2"}:
            raise RuntimeError("dialog-demo Native Blocks require Mattermost 11.11.0 or 12.0.2")
        blocks_enabled = os.environ.get("MM_BLOCKS_ENABLED", "true").lower() == "true"
        if not blocks_enabled:
            raise RuntimeError("dialog-demo Native Blocks require MM_BLOCKS_ENABLED=true")
        ui_profile = UIProfile(
            profile_version,
            client="local-dialog-navigation-demo",
            client_features=SERVER_FEATURES[profile_version],
            client_evidence=f"Explicit Mattermost {profile_version} dialog-demo web profile",
            mm_blocks_enabled=blocks_enabled,
        )
        native_dialogs = NativeDialogManager(
            bot,
            profile=ui_profile,
            callback_url=callback_base + "/mattergram/dialog",
        )
        dialog = build_dialog(demo_data)
        registry = DialogRegistry(dialog)
        runtime = DialogRuntime(
            bot,
            dialog_storage,
            registry,
            instance_id=bot.instance_id,
            bot_id=identity.id,
            callback_url=callback_url,
            callback_signer=signer,
            ui_profile=ui_profile,
            action_ttl=action_ttl,
        )
        setup_dialogs(dispatcher, runtime)

        @dispatcher.slash_command(Command("dialog-demo"))
        async def start_demo(event: SlashCommand) -> None:
            await runtime.start(event, Demo.home, mode=StartMode.RESET_STACK)
            key = dialog_storage.key(
                instance_id=bot.instance_id,
                bot_id=identity.id,
                channel_id=event.channel_id,
                actor_id=event.user_id,
            )
            stack = await dialog_storage.load(key)
            if stack.stack.last_post_id is None:
                raise RuntimeError("Dialog demo did not create its initial post")
            sessions[(event.channel_id, event.user_id)] = DemoSession(
                event.channel_id,
                event.user_id,
                stack.stack.last_post_id,
                event.team_id,
            )
            await event.answer("Demo-карточка создана ниже")

        @dispatcher.message(F.text == "dialog live")
        async def refresh_from_websocket(message: Message) -> None:
            session = sessions.get((message.channel_id, message.user_id))
            if session is None:
                await message.reply("Сначала выполните /dialog-demo")
                return
            demo_data.live_revision += 1
            demo_data.last_live_event = f"получено WS-сообщение {message.post_id}"
            key = dialog_storage.key(
                instance_id=bot.instance_id,
                bot_id=identity.id,
                channel_id=session.channel_id,
                actor_id=session.user_id,
            )
            actions = DialogActions(
                callback_url,
                ActionContext(
                    signer,
                    session.channel_id,
                    post_id=session.post_id,
                    user_id=session.user_id,
                    team_id=session.team_id,
                    ttl=action_ttl,
                ),
                ui_profile=ui_profile,
            )
            renderer = MattermostRenderer(
                bot,
                session.channel_id,
                actions_factory=actions,
                ui_profile=ui_profile,
            )
            async with DialogManager(
                dialog_storage,
                key,
                registry,
                renderer,
                actor_id=session.user_id,
                event=message,
            ) as manager:
                await manager.show(ShowMode.EDIT)

        http = HttpAdapter(
            bot,
            dispatcher,
            trusted_peers=[
                item.strip() for item in os.environ["MM_HTTP_TRUSTED_PEERS"].split(",") if item.strip()
            ],
            slash_bindings=[
                SlashBinding(
                    os.environ["MM_TEAM_ID"],
                    "dialog-demo",
                    SecretStr(os.environ["MM_SLASH_TOKEN"]),
                )
            ],
            callback_signer=signer,
            dialogs=native_dialogs,
            own_dialogs=True,
        )
        logger.info("Dialog demo is ready: use /dialog-demo and then send 'dialog live'")
        await dispatcher.start(
            bot,
            http=http,
            host=os.environ.get("MM_HTTP_HOST", "0.0.0.0"),
            port=int(os.environ.get("MM_HTTP_PORT", "18765")),
        )
    finally:
        await dispatcher.close()
        await bot.close()
        await storage.close()
        await isolation.close()


if __name__ == "__main__":
    asyncio.run(main())
