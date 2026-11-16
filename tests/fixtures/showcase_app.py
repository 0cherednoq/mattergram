"""Run a multi-page Mattermost card with tables, forms, callbacks, and live updates.

This example intentionally uses one post as a small application surface.  Run it
with the environment documented in ``docs/showcase.md`` and invoke /showcase.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable, Coroutine, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, TypeVar, cast

from pydantic import SecretStr

from mattergram import Bot, Dispatcher, F
from mattergram.filters import CallbackData, Command
from mattergram.http import CallbackSigner, DialogManager, HttpAdapter, SlashBinding
from mattergram.types import CallbackQuery, DialogSubmission, Message, SlashCommand
from mattergram.ui import UIProfile
from mattergram.ui.actions import (
    ActionContext,
    Actions,
    Attachment,
    External,
)
from mattergram.ui.actions import (
    Button as ActionButton,
)
from mattergram.ui.actions import (
    Select as ActionSelect,
)
from mattergram.ui.blocks import compile_post_ui
from mattergram.ui.forms import (
    Checkbox,
    DatePicker,
    Form,
    MultiSelect,
    Option,
    Radio,
    Select,
    TextArea,
    TextInput,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 3
ORDER_FORM = "showcase_order"
INCIDENT_FORM = "showcase_incident"
PREFERENCES_FORM = "showcase_preferences"
SHOWCASE_FORMS = frozenset({ORDER_FORM, INCIDENT_FORM, PREFERENCES_FORM})


class CardAction(CallbackData, prefix="showcase"):
    verb: Literal["filter", "form", "page", "view"]
    target: str
    value: str


@dataclass(frozen=True, slots=True)
class Order:
    id: str
    customer: str
    status: Literal["open", "paid", "cancelled"]
    total: str
    due: str


@dataclass(frozen=True, slots=True)
class Incident:
    id: str
    service: str
    severity: Literal["p1", "p2", "p3"]
    owner: str
    age: str


@dataclass(slots=True)
class TableState:
    page: int = 0
    filter: str = "all"


@dataclass(slots=True)
class DashboardState:
    post_id: str
    channel_id: str
    owner_id: str
    view: Literal["overview", "activity", "about"] = "overview"
    orders_table: TableState = field(default_factory=TableState)
    incidents_table: TableState = field(default_factory=TableState)
    orders: list[Order] = field(default_factory=list)
    incidents: list[Incident] = field(default_factory=list)
    activity: list[str] = field(default_factory=lambda: ["Dashboard opened"])
    compact: bool = False
    digest_sources: set[str] = field(default_factory=lambda: {"orders", "incidents"})
    live_revision: int = 0
    last_live_event: str = "No WebSocket event yet"


class MissingDashboardError(LookupError):
    pass


class DashboardAccessError(PermissionError):
    pass


StateResult = TypeVar("StateResult")


class DashboardStore:
    """Concurrency-safe, single-process state for this runnable example.

    Replace this repository and its lock with durable storage plus distributed
    isolation before running multiple workers.  The rendering code does not
    depend on this storage implementation.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cards: dict[str, DashboardState] = {}

    async def create(self, state: DashboardState) -> None:
        async with self._lock:
            self._cards[state.post_id] = deepcopy(state)

    async def mutate(
        self,
        post_id: str,
        actor_id: str,
        operation: Callable[[DashboardState], StateResult],
    ) -> tuple[DashboardState, StateResult]:
        async with self._lock:
            state = self._cards.get(post_id)
            if state is None:
                raise MissingDashboardError(post_id)
            if state.owner_id != actor_id:
                raise DashboardAccessError(actor_id)
            result = operation(state)
            return deepcopy(state), result

    async def record_live_event(self, channel_id: str, summary: str) -> list[DashboardState]:
        async with self._lock:
            cards: list[DashboardState] = []
            for state in self._cards.values():
                if state.channel_id != channel_id:
                    continue
                state.live_revision += 1
                state.last_live_event = summary
                state.activity.insert(0, f"Live update #{state.live_revision}: {summary}")
                del state.activity[20:]
                cards.append(deepcopy(state))
            return cards


@dataclass(frozen=True, slots=True)
class Card:
    text: str
    attachments: tuple[Attachment, ...]


def demo_state(post_id: str, channel_id: str, owner_id: str) -> DashboardState:
    return DashboardState(
        post_id=post_id,
        channel_id=channel_id,
        owner_id=owner_id,
        orders=[
            Order("ORD-1042", "Acme", "open", "$1,240", "2026-11-14"),
            Order("ORD-1041", "Globex", "paid", "$840", "2026-11-13"),
            Order("ORD-1040", "Initech", "open", "$320", "2026-11-16"),
            Order("ORD-1039", "Umbrella", "cancelled", "$95", "2026-11-12"),
            Order("ORD-1038", "Stark", "paid", "$2,100", "2026-11-11"),
            Order("ORD-1037", "Wayne", "open", "$760", "2026-11-18"),
            Order("ORD-1036", "Wonka", "paid", "$510", "2026-11-10"),
        ],
        incidents=[
            Incident("INC-88", "Checkout", "p1", "Nina", "8m"),
            Incident("INC-87", "Search", "p2", "Omar", "22m"),
            Incident("INC-86", "Billing", "p2", "Nina", "41m"),
            Incident("INC-85", "Email", "p3", "Lee", "1h"),
            Incident("INC-84", "API", "p1", "Omar", "2h"),
        ],
    )


def _escape_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(
    rows: Sequence[object],
    columns: Sequence[tuple[str, Callable[[object], object]]],
    page: int,
) -> tuple[str, int, int]:
    pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    normalized_page = min(max(page, 0), pages - 1)
    selected = rows[normalized_page * PAGE_SIZE : (normalized_page + 1) * PAGE_SIZE]
    header = "| " + " | ".join(title for title, _ in columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(_escape_cell(accessor(row)) for _, accessor in columns) + " |" for row in selected
    ]
    if not body:
        body = ["| " + " | ".join("_none_" for _ in columns) + " |"]
    return "\n".join((header, divider, *body)), normalized_page, pages


def _last_page(item_count: int) -> int:
    return max(0, (item_count - 1) // PAGE_SIZE)


def _external(callback_url: str, verb: CardAction) -> External:
    return External(url=callback_url, data=verb.pack())


def _button(
    callback_url: str,
    identifier: str,
    name: str,
    *,
    verb: Literal["filter", "form", "page", "view"],
    target: str,
    value: str,
    style: str | None = None,
) -> ActionButton:
    return ActionButton(
        id=identifier,
        name=name,
        style=style,
        action=_external(callback_url, CardAction(verb=verb, target=target, value=value)),
    )


def _filter(
    callback_url: str,
    identifier: str,
    name: str,
    target: str,
    current: str,
    options: tuple[Option, ...],
) -> ActionSelect:
    return ActionSelect(
        id=identifier,
        name=name,
        options=options,
        default_option=current,
        action=_external(
            callback_url,
            CardAction(verb="filter", target=target, value="selected-option"),
        ),
    )


def _overview_card(state: DashboardState, callback_url: str) -> Card:
    orders = [order for order in state.orders if state.orders_table.filter in ("all", order.status)]
    incidents = [
        incident for incident in state.incidents if state.incidents_table.filter in ("all", incident.severity)
    ]
    order_table, order_page, order_pages = _table(
        orders,
        (
            ("Order", lambda item: cast(Order, item).id),
            ("Customer", lambda item: cast(Order, item).customer),
            ("Status", lambda item: cast(Order, item).status),
            ("Total", lambda item: cast(Order, item).total),
        ),
        state.orders_table.page,
    )
    incident_table, incident_page, incident_pages = _table(
        incidents,
        (
            ("Incident", lambda item: cast(Incident, item).id),
            ("Service", lambda item: cast(Incident, item).service),
            ("Severity", lambda item: cast(Incident, item).severity.upper()),
            ("Owner", lambda item: cast(Incident, item).owner),
            ("Age", lambda item: cast(Incident, item).age),
        ),
        state.incidents_table.page,
    )
    sections = [
        "# Operations workspace",
        f"Live revision: **{state.live_revision}** · {state.last_live_event}",
        "Digest sources: " + ", ".join(sorted(state.digest_sources)),
        f"## Orders · filter `{state.orders_table.filter}` · page {order_page + 1}/{order_pages}",
        order_table,
        f"## Incidents · filter `{state.incidents_table.filter}` · page {incident_page + 1}/{incident_pages}",
        incident_table,
    ]
    if state.compact:
        sections.append("_Compact mode is enabled._")

    order_actions = Actions(
        items=(
            _filter(
                callback_url,
                "orderfilter",
                "Order status",
                "orders",
                state.orders_table.filter,
                (
                    Option(text="All", value="all"),
                    Option(text="Open", value="open"),
                    Option(text="Paid", value="paid"),
                    Option(text="Cancelled", value="cancelled"),
                ),
            ),
            _button(callback_url, "orderprev", "◀ Orders", verb="page", target="orders", value="-1"),
            _button(callback_url, "ordernext", "Orders ▶", verb="page", target="orders", value="1"),
            _button(
                callback_url,
                "ordernew",
                "New order",
                verb="form",
                target="order",
                value="open",
                style="primary",
            ),
        )
    )
    incident_actions = Actions(
        items=(
            _filter(
                callback_url,
                "incidentfilter",
                "Incident severity",
                "incidents",
                state.incidents_table.filter,
                (
                    Option(text="All", value="all"),
                    Option(text="P1", value="p1"),
                    Option(text="P2", value="p2"),
                    Option(text="P3", value="p3"),
                ),
            ),
            _button(
                callback_url,
                "incidentprev",
                "◀ Incidents",
                verb="page",
                target="incidents",
                value="-1",
            ),
            _button(
                callback_url,
                "incidentnext",
                "Incidents ▶",
                verb="page",
                target="incidents",
                value="1",
            ),
            _button(
                callback_url,
                "incidentnew",
                "Report incident",
                verb="form",
                target="incident",
                value="open",
                style="danger",
            ),
        )
    )
    navigation = Actions(
        items=(
            _button(callback_url, "activity", "Activity", verb="view", target="activity", value="open"),
            _button(callback_url, "about", "About", verb="view", target="about", value="open"),
            _button(
                callback_url,
                "preferences",
                "Preferences",
                verb="form",
                target="preferences",
                value="open",
            ),
        )
    )
    return Card(
        "\n\n".join(sections),
        (
            Attachment(title="Orders controls", color="#3d85c6", actions=order_actions),
            Attachment(title="Incident controls", color="#cc0000", actions=incident_actions),
            Attachment(title="Pages and settings", actions=navigation),
        ),
    )


def render_card(state: DashboardState, callback_url: str) -> Card:
    if state.view == "overview":
        return _overview_card(state, callback_url)
    overview = _button(
        callback_url,
        "overview",
        "Back to overview",
        verb="view",
        target="overview",
        value="open",
        style="primary",
    )
    if state.view == "activity":
        lines = "\n".join(f"- {entry}" for entry in state.activity[:10])
        return Card(
            "# Activity\n\n" + (lines or "_No activity yet._"),
            (Attachment(title="Navigation", actions=Actions(items=(overview,))),),
        )
    return Card(
        "# About this card\n\n"
        "This is one Mattermost post with multiple Markdown tables, independent table state, "
        "three action groups, native modal forms, and live updates. It is not restricted to a "
        "single Telegram-style message widget.",
        (Attachment(title="Navigation", actions=Actions(items=(overview,))),),
    )


def compile_card(card: Card, context: ActionContext) -> dict[str, Any]:
    return compile_post_ui(attachments=card.attachments, context=context, message=card.text)


def order_form(post_id: str) -> Form:
    return Form(
        callback_id=ORDER_FORM,
        title="Create order",
        introduction_text="Text, select, radio, date, boolean, and long text in one native form.",
        submit_label="Create",
        state=post_id,
        elements=(
            TextInput(name="customer", label="Customer", min_length=2, max_length=80),
            Select(
                name="status",
                label="Status",
                default="open",
                options=(
                    Option(text="Open", value="open"),
                    Option(text="Paid", value="paid"),
                    Option(text="Cancelled", value="cancelled"),
                ),
            ),
            Radio(
                name="priority",
                label="Priority",
                default="normal",
                options=(
                    Option(text="Normal", value="normal"),
                    Option(text="Rush", value="rush"),
                ),
            ),
            DatePicker(name="due", label="Due date", min_date="today"),
            Checkbox(name="gift", label="Gift wrap", default=False, optional=True),
            TextArea(name="notes", label="Notes", optional=True, max_length=500),
        ),
    )


def incident_form(post_id: str) -> Form:
    return Form(
        callback_id=INCIDENT_FORM,
        title="Report incident",
        submit_label="Report",
        state=post_id,
        elements=(
            TextInput(name="service", label="Service", min_length=2, max_length=80),
            Select(
                name="severity",
                label="Severity",
                default="p2",
                options=(
                    Option(text="P1 critical", value="p1"),
                    Option(text="P2 major", value="p2"),
                    Option(text="P3 minor", value="p3"),
                ),
            ),
            MultiSelect(
                name="watchers",
                label="Notify teams",
                optional=True,
                options=(
                    Option(text="Platform", value="platform"),
                    Option(text="Support", value="support"),
                    Option(text="Security", value="security"),
                ),
            ),
            Checkbox(name="impact", label="Customer impact", default=True),
            DatePicker(name="observed", label="Observed on", default="today"),
            TextArea(name="summary", label="Summary", min_length=5, max_length=1000),
        ),
    )


def preferences_form(post_id: str, state: DashboardState) -> Form:
    return Form(
        callback_id=PREFERENCES_FORM,
        title="Dashboard preferences",
        submit_label="Apply",
        state=post_id,
        elements=(
            MultiSelect(
                name="sections",
                label="Digest sources",
                default_values=tuple(sorted(state.digest_sources)),
                options=(
                    Option(text="Orders", value="orders"),
                    Option(text="Incidents", value="incidents"),
                ),
            ),
            Checkbox(name="compact", label="Compact mode", default=state.compact, optional=True),
        ),
    )


def apply_card_action(state: DashboardState, action: CardAction, selected_option: str | None) -> None:
    if action.verb == "view":
        if action.target not in {"overview", "activity", "about"}:
            raise ValueError("Unknown card page")
        state.view = cast(Literal["overview", "activity", "about"], action.target)
        state.activity.insert(0, f"Opened {action.target} page")
        return
    table = state.orders_table if action.target == "orders" else state.incidents_table
    if action.target not in {"orders", "incidents"}:
        raise ValueError("Unknown table")
    if action.verb == "page":
        if action.target == "orders":
            item_count = sum(state.orders_table.filter in ("all", order.status) for order in state.orders)
        else:
            item_count = sum(
                state.incidents_table.filter in ("all", incident.severity) for incident in state.incidents
            )
        table.page = min(max(0, table.page + int(action.value)), _last_page(item_count))
        state.activity.insert(0, f"Changed {action.target} page to {table.page + 1}")
        return
    if action.verb == "filter":
        if selected_option is None:
            raise ValueError("Filter action has no selected option")
        allowed = (
            {"all", "open", "paid", "cancelled"} if action.target == "orders" else {"all", "p1", "p2", "p3"}
        )
        if selected_option not in allowed:
            raise ValueError("Unknown filter option")
        table.filter = selected_option
        table.page = 0
        state.activity.insert(0, f"Filtered {action.target} by {selected_option}")
        return
    raise ValueError("This action does not mutate the card")


async def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    callback_base = os.environ["MM_CALLBACK_BASE_URL"].rstrip("/")
    callback_url = callback_base + "/mattergram/callback"
    bot = Bot(token=os.environ["MM_BOT_TOKEN"], base_url=os.environ["MM_URL"])
    dispatcher = Dispatcher()
    store = DashboardStore()
    pending: set[asyncio.Task[None]] = set()

    def run_background(work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(work)
        pending.add(task)

        def finished(completed: asyncio.Task[None]) -> None:
            pending.discard(completed)
            if completed.cancelled():
                return
            error = completed.exception()
            if error is not None:
                logger.error("Background card refresh failed", exc_info=error)

        task.add_done_callback(finished)

    try:
        identity = await bot.get_me()
        signer = CallbackSigner(
            instance_id=bot.instance_id,
            bot_id=identity.id,
            keys={"current": bytes.fromhex(os.environ["MM_ACTION_SIGNING_KEY_HEX"])},
            active_key_id="current",
        )
        profile = UIProfile(
            server_version=os.environ.get("MM_SERVER_VERSION", "11.1.0"),
            client="showcase-declared-client",
            client_features=frozenset({"forms.basic", "forms.date", "forms.multi"}),
            client_evidence="Application configuration; verify the deployed clients",
        )
        dialogs = DialogManager(
            bot,
            profile=profile,
            callback_url=callback_base + "/mattergram/dialog",
        )

        def action_context(
            channel_id: str,
            user_id: str,
            *,
            post_id: str | None = None,
            team_id: str | None = None,
            thread_id: str | None = None,
        ) -> ActionContext:
            return ActionContext(
                signer,
                channel_id,
                post_id=post_id,
                user_id=user_id,
                team_id=team_id,
                thread_id=thread_id,
            )

        async def refresh_with_rest(state: DashboardState) -> None:
            # A WebSocket or form event is inbound.  Shared post mutation is a
            # REST operation. edit_message_text intentionally preserves props.
            card = render_card(state, callback_url)
            await bot.edit_message_text(state.channel_id, state.post_id, card.text)

        @dispatcher.slash_command(Command("showcase"))
        async def open_showcase(event: SlashCommand) -> None:
            await event.answer("Opening the showcase card…")
            temporary = demo_state("pending", event.channel_id, event.user_id)
            card = render_card(temporary, callback_url)
            message = await bot.send_message(
                event.channel_id,
                card.text,
                root_id=event.root_id,
                attachments=card.attachments,
                action_context=action_context(
                    event.channel_id,
                    event.user_id,
                    team_id=event.team_id,
                    thread_id=event.root_id,
                ),
            )
            temporary.post_id = message.post_id
            await store.create(temporary)

        @dispatcher.callback_query(CardAction.filter())
        async def card_action(query: CallbackQuery, callback_data: CardAction) -> None:
            if callback_data.verb == "form":
                try:
                    state, _ = await store.mutate(query.post_id, query.user_id, lambda current: None)
                except (MissingDashboardError, DashboardAccessError):
                    await query.answer("This dashboard is unavailable")
                    return
                if callback_data.target == "order":
                    form = order_form(query.post_id)
                elif callback_data.target == "incident":
                    form = incident_form(query.post_id)
                elif callback_data.target == "preferences":
                    form = preferences_form(query.post_id, state)
                else:
                    await query.answer("Unknown form")
                    return
                await query.open_dialog(form)
                return
            try:
                state, _ = await store.mutate(
                    query.post_id,
                    query.user_id,
                    lambda current: apply_card_action(current, callback_data, query.selected_option),
                )
            except (MissingDashboardError, DashboardAccessError, ValueError):
                await query.answer("This card action is unavailable")
                return
            card = render_card(state, callback_url)
            props = compile_card(
                card,
                action_context(
                    query.channel_id,
                    query.user_id,
                    post_id=query.post_id,
                    team_id=query.team_id,
                    thread_id=query.thread_id,
                ),
            )
            # A post-action callback has one response.  Replace text and props
            # together so filters, navigation, and signed controls stay in sync.
            await query.update_message(card.text, props=props)

        def is_showcase_form(event: DialogSubmission) -> bool:
            return event.form.callback_id in SHOWCASE_FORMS

        @dispatcher.dialog_submit(is_showcase_form)
        async def form_submitted(event: DialogSubmission) -> None:
            post_id = event.application_state

            def apply_form(state: DashboardState) -> None:
                values = event.values
                if event.form.callback_id == ORDER_FORM:
                    state.orders.insert(
                        0,
                        Order(
                            f"ORD-{1043 + len(state.orders)}",
                            cast(str, values["customer"]),
                            cast(Literal["open", "paid", "cancelled"], values["status"]),
                            "$0",
                            cast(date, values["due"]).isoformat(),
                        ),
                    )
                    state.activity.insert(0, f"Created order for {values['customer']}")
                elif event.form.callback_id == INCIDENT_FORM:
                    state.incidents.insert(
                        0,
                        Incident(
                            f"INC-{89 + len(state.incidents)}",
                            cast(str, values["service"]),
                            cast(Literal["p1", "p2", "p3"], values["severity"]),
                            "Unassigned",
                            "now",
                        ),
                    )
                    state.activity.insert(0, f"Reported incident for {values['service']}")
                else:
                    state.digest_sources = set(cast(list[str], values["sections"]))
                    state.compact = values["compact"] is True
                    state.activity.insert(0, "Updated dashboard preferences")

            try:
                state, _ = await store.mutate(post_id, event.user_id, apply_form)
            except (MissingDashboardError, DashboardAccessError):
                await event.validation_error("The source dashboard is unavailable")
                return
            await event.answer()
            # Return the modal response immediately.  A production application
            # should enqueue this refresh durably; this example tracks the task
            # and drains it on shutdown.
            run_background(refresh_with_rest(state))

        @dispatcher.message(F.text == "showcase live")
        async def live_event(message: Message) -> None:
            # Dispatcher received this post through Mattermost WebSocket.  WS is
            # not used to write the card; each shared-card edit is a REST PATCH.
            cards = await store.record_live_event(
                message.channel_id,
                f"message {message.post_id} received over WebSocket",
            )
            await asyncio.gather(*(refresh_with_rest(state) for state in cards))

        http = HttpAdapter(
            bot,
            dispatcher,
            trusted_peers=[
                peer.strip() for peer in os.environ["MM_HTTP_TRUSTED_PEERS"].split(",") if peer.strip()
            ],
            slash_bindings=[
                SlashBinding(
                    os.environ.get("MM_TEAM_ID"),
                    "showcase",
                    SecretStr(os.environ["MM_SLASH_TOKEN"]),
                )
            ],
            callback_signer=signer,
            dialogs=dialogs,
            own_dialogs=True,
        )
        await dispatcher.start(
            bot,
            http=http,
            host=os.environ.get("MM_HTTP_HOST", "127.0.0.1"),
            port=int(os.environ.get("MM_HTTP_PORT", "8080")),
        )
    finally:
        await dispatcher.close()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
