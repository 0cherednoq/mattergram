from __future__ import annotations

import pytest

from examples.showcase_bot import (
    CardAction,
    DashboardAccessError,
    DashboardStore,
    apply_card_action,
    compile_card,
    demo_state,
    incident_form,
    order_form,
    preferences_form,
    render_card,
)
from mattergram.http import CallbackSigner
from mattergram.ui import UIProfile
from mattergram.ui.actions import ActionContext
from mattergram.ui.forms import Checkbox, DatePicker, MultiSelect, Radio, Select, TextArea, TextInput


def test_card_contains_two_tables_and_three_independent_control_groups() -> None:
    state = demo_state("post", "channel", "owner")
    card = render_card(state, "https://callbacks.example.test/mattergram/callback")
    signer = CallbackSigner(
        instance_id="instance",
        bot_id="bot",
        keys={"current": b"x" * 32},
        active_key_id="current",
    )

    props = compile_card(card, ActionContext(signer, "channel", user_id="owner"))

    assert "## Orders" in card.text
    assert "## Incidents" in card.text
    assert len(props["attachments"]) == 3
    assert sum(len(attachment.get("actions", ())) for attachment in props["attachments"]) == 11
    assert props["attachments"][0]["actions"][0]["default_option"] == "all"
    assert props["attachments"][1]["actions"][0]["default_option"] == "all"
    signed = props["attachments"][0]["actions"][0]["integration"]["context"]["mattergram"]
    assert signed["payload"]["allowed_options"] == ["all", "open", "paid", "cancelled"]


def test_each_table_keeps_its_own_page_and_filter() -> None:
    state = demo_state("post", "channel", "owner")

    apply_card_action(
        state,
        CardAction(verb="page", target="orders", value="1"),
        selected_option=None,
    )
    apply_card_action(
        state,
        CardAction(verb="page", target="incidents", value="1"),
        selected_option=None,
    )
    apply_card_action(
        state,
        CardAction(verb="filter", target="orders", value="selected-option"),
        selected_option="paid",
    )

    assert state.orders_table.page == 0
    assert state.orders_table.filter == "paid"
    assert state.incidents_table.page == 1
    assert state.incidents_table.filter == "all"


def test_forms_cover_distinct_native_input_families_and_retain_source_post() -> None:
    state = demo_state("post", "channel", "owner")

    order = order_form("post")
    incident = incident_form("post")
    preferences = preferences_form("post", state)

    assert order.state == incident.state == preferences.state == "post"
    assert {type(element) for element in order.elements} == {
        TextInput,
        Select,
        Radio,
        DatePicker,
        Checkbox,
        TextArea,
    }
    assert any(isinstance(element, MultiSelect) for element in incident.elements)
    assert any(isinstance(element, MultiSelect) for element in preferences.elements)
    profile = UIProfile(
        server_version="11.1.0",
        client="test-client",
        client_features=frozenset({"forms.basic", "forms.date", "forms.multi"}),
        client_evidence="unit test",
    )
    assert all(form.to_wire(profile)["elements"] for form in (order, incident, preferences))


@pytest.mark.asyncio
async def test_store_isolates_owner_and_records_live_updates_for_channel() -> None:
    store = DashboardStore()
    await store.create(demo_state("first", "channel", "owner"))
    await store.create(demo_state("second", "other-channel", "owner"))

    with pytest.raises(DashboardAccessError):
        await store.mutate("first", "another-user", lambda state: None)

    cards = await store.record_live_event("channel", "domain event")

    assert len(cards) == 1
    assert cards[0].post_id == "first"
    assert cards[0].live_revision == 1
    assert cards[0].last_live_event == "domain event"
