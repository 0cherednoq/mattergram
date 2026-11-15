"""Declare one editable post with branch-aware Back, filters, and independent pagers."""

from __future__ import annotations

from typing import Any

from mattergram.fsm.state import State, StatesGroup
from mattergram_dialog import (
    ActionRow,
    Back,
    ButtonGrid,
    ButtonRow,
    Const,
    DataBlock,
    Dialog,
    Filter,
    MarkdownColumn,
    PagedTable,
    PagePager,
    SwitchTo,
    Window,
)


class Dashboard(StatesGroup):
    home = State()
    reports = State()
    orders = State()
    incidents = State()
    shared_help = State()


ORDERS = (
    {"id": "O-104", "status": "open", "owner": "Ada"},
    {"id": "O-103", "status": "closed", "owner": "Lin"},
    {"id": "O-102", "status": "open", "owner": "Max"},
    {"id": "O-101", "status": "open", "owner": "Zoe"},
)
INCIDENTS = tuple({"id": f"INC-{number}", "severity": "high" if number % 2 else "low"} for number in range(8))


async def dashboard_data() -> dict[str, Any]:
    return {"orders": ORDERS, "incidents": INCIDENTS}


dashboard = Dialog(
    Window(
        Const("## Dashboard\nChoose a section."),
        ButtonGrid(
            ActionRow(
                SwitchTo(Const("Combined reports"), id="reports", state=Dashboard.reports),
                SwitchTo(Const("Orders"), id="orders", state=Dashboard.orders),
            ),
            ActionRow(
                SwitchTo(Const("Incidents"), id="incidents", state=Dashboard.incidents),
            ),
            title="Sections",
        ),
        state=Dashboard.home,
    ),
    Window(
        Const("## Combined reports\nEach table contains its own local actions."),
        DataBlock(
            "Orders",
            PagedTable(
                MarkdownColumn("Order", "id", max_chars=12),
                MarkdownColumn("Status", "status", max_chars=12),
                MarkdownColumn("Owner", "owner", max_chars=14),
                id="reportOrderRows",
                items="orders",
                page_size=2,
                filter_by="status",
                empty=Const("No orders match this filter."),
                page_label="Page {page}/{pages} · {total} orders",
            ),
            Filter(
                id="reportOrderFilter",
                target="reportOrderRows",
                options={"All": None, "Open": "open", "Closed": "closed"},
            ),
            PagePager(id="reportOrderPager", target="reportOrderRows"),
        ),
        DataBlock(
            "Incidents",
            PagedTable(
                MarkdownColumn("Incident", "id", max_chars=14),
                MarkdownColumn("Severity", "severity", max_chars=12),
                id="reportIncidentRows",
                items="incidents",
                page_size=3,
                filter_by="severity",
                empty=Const("No incidents match this filter."),
                page_label="Page {page}/{pages} · {total} incidents",
            ),
            Filter(
                id="reportIncidentFilter",
                target="reportIncidentRows",
                options={"All": None, "High": "high", "Low": "low"},
            ),
            PagePager(id="reportIncidentPager", target="reportIncidentRows"),
        ),
        ButtonRow(
            SwitchTo(Const("Help"), id="reportHelp", state=Dashboard.shared_help),
            Back(Const("Back"), id="reportBack"),
            title="Navigation",
        ),
        state=Dashboard.reports,
        getter=dashboard_data,
    ),
    Window(
        Const("## Orders"),
        DataBlock(
            "Order list",
            PagedTable(
                MarkdownColumn("Order", "id", max_chars=12),
                MarkdownColumn("Status", "status", max_chars=12),
                MarkdownColumn("Owner", "owner", max_chars=14),
                id="orderRows",
                items="orders",
                page_size=2,
                filter_by="status",
                empty=Const("No orders match this filter."),
                page_label="Page {page}/{pages} · {total} orders",
            ),
            Filter(
                id="orderFilter",
                target="orderRows",
                options={"All": None, "Open": "open", "Closed": "closed"},
            ),
            PagePager(id="orderPager", target="orderRows"),
        ),
        ButtonRow(
            SwitchTo(Const("Help"), id="orderHelp", state=Dashboard.shared_help),
            Back(Const("Back"), id="orderBack"),
            title="Navigation",
        ),
        state=Dashboard.orders,
        getter=dashboard_data,
    ),
    Window(
        Const("## Incidents"),
        DataBlock(
            "Incident list",
            PagedTable(
                MarkdownColumn("Incident", "id", max_chars=14),
                MarkdownColumn("Severity", "severity", max_chars=12),
                id="incidentRows",
                items="incidents",
                page_size=3,
                page_label="Page {page}/{pages} · {total} incidents",
            ),
            PagePager(id="incidentPager", target="incidentRows"),
        ),
        ButtonRow(
            SwitchTo(Const("Help"), id="incidentHelp", state=Dashboard.shared_help),
            Back(Const("Back"), id="incidentBack"),
            title="Navigation",
        ),
        state=Dashboard.incidents,
        getter=dashboard_data,
    ),
    Window(
        Const("## Help\nBack returns to the branch that actually opened this screen."),
        Back(Const("Back"), id="helpBack"),
        state=Dashboard.shared_help,
    ),
)


__all__ = ["Dashboard", "dashboard"]
