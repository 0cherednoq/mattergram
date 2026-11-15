"""Real owned Mattermost form API scenarios, reused by the existing HTTP stand.

This exercises native API and actor WS deliveries, not browser rendering.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from typing import Any

from pydantic import SecretStr

from mattergram import Bot, Dispatcher
from mattergram.http import DialogManager
from mattergram.types import DialogCancel, DialogLookup, DialogRefresh, DialogSubmission, SlashCommand
from mattergram.ui import UIProfile
from mattergram.ui.capabilities import SERVER_FEATURES
from mattergram.ui.form_values import MISSING
from mattergram.ui.forms import (
    Checkbox,
    DatePicker,
    DateTimePicker,
    Form,
    MultiSelect,
    Option,
    Select,
    TextInput,
)
from tools._common import ROOT, write_json


class RecordingDialogs(DialogManager):
    def __init__(self, bot: Bot) -> None:
        super().__init__(
            bot,
            profile=UIProfile(
                "11.1.0",
                client="owned-native-api-harness",
                client_features=SERVER_FEATURES["11.1.0"],
                client_evidence="API protocol test; not client rendering evidence",
            ),
        )
        self.received: list[dict[str, Any]] = []
        self.statuses: list[int] = []

    async def handle(self, payload: Any, **kwargs: Any) -> Any:
        self.received.append(dict(payload))
        response = await super().handle(payload, **kwargs)
        self.statuses.append(response.status)
        return response


class FormsStandScenario:
    def __init__(self, bot: Bot, dispatcher: Dispatcher) -> None:
        self.manager = RecordingDialogs(bot)
        self.calls: list[str] = []
        self.effects: list[str] = []
        self.cancelled = asyncio.Event()
        self.actor_opened: list[dict[str, Any]] = []

        @dispatcher.dialog_submit()
        async def submit(event: DialogSubmission) -> None:
            self.calls.append(event.application_state)
            if event.application_state == "validation":
                if event.values["title"] == "reject":
                    await event.field_errors({"title": "Choose another title"})
                    return
                assert event.values["flag"] is False
                assert event.values["choices"] == ["a", "b"]
                assert event.values["date"] == date(2026, 11, 12)
                assert event.values["time"].utcoffset() == timedelta(hours=3)
                self.effects.append("validated")
                await event.answer()
            elif event.application_state == "wizard":
                await event.next(
                    Form(
                        callback_id="wizard_second",
                        title="Step 2",
                        state="wizard_second",
                        elements=(TextInput(name="detail", label="Detail"),),
                    )
                )
            elif event.application_state == "wizard_second":
                assert event.values["title"] == "first"
                assert event.values["detail"] == "second"
                self.effects.append("wizard")
                await event.answer()
            elif event.application_state == "timeout":
                try:
                    await asyncio.Event().wait()
                finally:
                    self.cancelled.set()
            else:
                await event.answer()

        @dispatcher.dialog_refresh()
        async def refresh(event: DialogRefresh) -> None:
            self.calls.append("refresh")
            assert event.values["category"] == "a"
            assert event.values["comment"] is MISSING
            await event.replace_form(self.form("fields"))

        @dispatcher.dialog_lookup()
        async def lookup(event: DialogLookup) -> None:
            self.calls.append("lookup")
            assert event.query == "ab" and event.selected_field == "pick"
            assert event.values["comment"] is MISSING
            await event.answer((Option(text="Native result", value="native"),))

        @dispatcher.dialog_cancel()
        async def cancel(event: DialogCancel) -> None:
            self.calls.append("cancel")
            await event.answer()

    def form(self, mode: str) -> Form:
        if mode == "fields":
            return Form(
                callback_id="fields",
                title="Fields",
                state=mode,
                source_url=self.manager.callback_url,
                elements=(
                    Select(
                        name="category",
                        label="Category",
                        options=(Option(text="A", value="a"),),
                        refresh=True,
                    ),
                    Select(
                        name="pick",
                        label="Pick",
                        optional=True,
                        data_source="dynamic",
                        data_source_url=self.manager.callback_url,
                    ),
                    TextInput(name="comment", label="Comment"),
                ),
            )
        if mode == "validation":
            return Form(
                callback_id="validation",
                title="Native form",
                state=mode,
                elements=(
                    TextInput(name="title", label="Title", min_length=1),
                    Checkbox(name="flag", label="Flag"),
                    MultiSelect(
                        name="choices",
                        label="Choices",
                        options=(Option(text="A", value="a"), Option(text="B", value="b")),
                    ),
                    DatePicker(name="date", label="Date"),
                    DateTimePicker(name="time", label="Time", time_interval=30),
                ),
            )
        return Form(
            callback_id=mode,
            title="Native form",
            state=mode,
            notify_on_cancel=True,
            elements=(TextInput(name="title", label="Title"),),
        )

    async def open_from_command(self, event: SlashCommand) -> None:
        mode = event.text.removeprefix("form:")
        await event.open_dialog(self.form(mode))

    async def verify(
        self, *, request: Any, execute: Any, trigger: str, actor_socket: Any, state: dict[str, Any]
    ) -> dict[str, Any]:
        checks: list[str] = []

        async def open_form(mode: str) -> dict[str, Any]:
            await execute(trigger, "form:" + mode)
            async with asyncio.timeout(8):
                while True:
                    event = await actor_socket.receive_json(timeout=8)
                    if event.get("event") != "open_dialog":
                        continue
                    assert event["broadcast"]["user_id"] == state["admin_id"]
                    opened = json.loads(event["data"]["dialog"])
                    assert opened["dialog"]["callback_id"] == mode
                    assert opened["url"] == self.manager.callback_url
                    assert opened["dialog"]["state"].startswith("mgd1.")
                    self.actor_opened.append(opened)
                    return dict(opened["dialog"])

        def body(form: dict[str, Any], values: dict[str, Any], **extra: Any) -> dict[str, Any]:
            return {
                "url": self.manager.callback_url,
                "callback_id": form["callback_id"],
                "state": form["state"],
                "channel_id": state["channel_id"],
                "team_id": state["team_id"],
                "cancelled": False,
                "submission": values,
                **extra,
            }

        form = await open_form("validation")
        values = {
            "title": "",
            "flag": False,
            "choices": ["a", "b"],
            "date": "2026-11-12",
            "time": "2026-11-12T10:30:00+03:00",
        }
        reply = await request("POST", "actions/dialogs/submit", body(form, values))
        assert reply == {"errors": {"title": "This field is required"}}
        assert self.calls == []
        values["title"] = "reject"
        rejected = body(form, values)
        reply = await request("POST", "actions/dialogs/submit", rejected)
        assert reply == {"errors": {"title": "Choose another title"}}
        await request("POST", "actions/dialogs/submit", rejected)
        assert self.calls == ["validation"]
        values["title"] = "accept"
        accepted = body(form, values, user_id="untrusted-client-user")
        assert await request("POST", "actions/dialogs/submit", accepted) == {}
        assert await request("POST", "actions/dialogs/submit", accepted) == {}
        assert self.manager.received[-1]["user_id"] == state["admin_id"]
        assert self.effects == ["validated"]
        checks.append(
            "real slash trigger -> native open_dialog -> actor-targeted WS form; parser errors and business field errors preserve correction; native bool/array/date/offset parsed; terminal replay ran business effect once"
        )
        checks.append(
            "real submit API overwrote forged client user_id with authenticated actor before SDK binding"
        )

        first = await open_form("wizard")
        first_request = body(first, {"title": "first"})
        reply = await request("POST", "actions/dialogs/submit", first_request)
        second = reply["form"]
        assert reply["type"] == "form" and second["state"] != first["state"]
        assert await request("POST", "actions/dialogs/submit", first_request) == reply
        assert await request("POST", "actions/dialogs/submit", body(second, {"detail": "second"})) == {}
        assert self.effects == ["validated", "wizard"]
        checks.append(
            "real multi-step form response rotates state, replays prior transition and preserves verified previous values without relying on client accumulation"
        )

        fields = await open_form("fields")
        lookup = body(fields, {"query": "ab", "selected_field": "pick"})
        reply = await request("POST", "actions/dialogs/lookup", lookup)
        assert reply == {"items": [{"text": "Native result", "value": "native"}]}
        refreshed = await request(
            "POST",
            "actions/dialogs/submit",
            body(fields, {"category": "a", "selected_field": "category"}, type="refresh"),
        )
        assert refreshed["type"] == "form" and refreshed["form"]["state"] != fields["state"]
        checks.append(
            "real lookup and refresh use immediate native items/form responses and tolerate incomplete required inputs"
        )

        cancelled = await open_form("cancel")
        cancel_request = body(cancelled, {}, cancelled=True)
        assert await request("POST", "actions/dialogs/submit", cancel_request) == {}
        assert await request("POST", "actions/dialogs/submit", cancel_request) == {}
        assert self.calls.count("cancel") == 1
        checks.append(
            "real cancellation closes the managed session and duplicate delivery does not repeat cancel handler"
        )

        timed = await open_form("timeout")
        timed_request = body(timed, {"title": "timeout"})
        await request("POST", "actions/dialogs/submit", timed_request, expected=(400, 500))
        await asyncio.wait_for(self.cancelled.wait(), 5)
        await request("POST", "actions/dialogs/submit", timed_request, expected=(400, 500))
        assert self.calls.count("timeout") == 1
        await self.manager.reconcile(SecretStr(timed["state"]), response={})
        assert await request("POST", "actions/dialogs/submit", timed_request) == {}
        assert self.calls.count("timeout") == 1
        checks.append(
            "real timed-out submit is cancelled, repeated delivery blocked as indeterminate, explicit application reconciliation returns stored result without re-execution"
        )

        tampered = body(form, values)
        tampered["state"] = tampered["state"][:-1] + ("a" if tampered["state"][-1] != "a" else "b")
        previous = len(self.calls)
        await request("POST", "actions/dialogs/submit", tampered, expected=(400, 500))
        assert len(self.calls) == previous and self.manager.statuses[-1] == 403
        checks.append("real Mattermost forwarded tampered form state; SDK rejected before business dispatch")
        write_json(
            ROOT / "build/forms-private-captures.json",
            {"open_dialog": self.actor_opened, "requests": self.manager.received},
        )
        report = {
            "result": "passed",
            "scope": "Own Mattermost 11.1 API and actor WS, not UI rendering; native payloads and responses through SDK-managed forms",
            "checks": checks,
            "http_statuses": self.manager.statuses,
            "handler_calls": self.calls,
            "business_effects": self.effects,
            "credentials": "omitted; full captures only in ignored build/",
        }
        write_json(ROOT / "build/reports/forms-stand.json", report)
        return report
