from __future__ import annotations

import asyncio
import json
import unittest

import aiohttp
from aiohttp import web
from pydantic import SecretStr

from mattergram import Bot, Dispatcher
from mattergram.exceptions import PermissionDeniedError
from mattergram.http import ASGIAdapter, DialogManager, HttpAdapter, SlashBinding
from mattergram.ui import UIProfile
from mattergram.ui.capabilities import SERVER_FEATURES
from mattergram.ui.form_values import MISSING
from mattergram.ui.forms import (
    Checkbox,
    Collapsible,
    DialogAction,
    DialogButton,
    Form,
    MultiSelect,
    Option,
    Select,
    TextInput,
)


class DialogHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.opened, self.handles = [], []
        self.now = 1000.0
        self.provider_mode = "normal"
        provider = web.Application()

        async def identity(request):
            return web.json_response({"id": "bot", "username": "bot"})

        async def open_dialog(request):
            self.assertEqual(request.headers.get("Authorization"), "Bearer bot-secret")
            self.opened.append(await request.json())
            if self.provider_mode == "error":
                payload = self.opened[-1]
                return web.json_response(
                    {"id": payload["trigger_id"] + ":" + payload["dialog"]["state"]}, status=403
                )
            return web.json_response({"status": "OK"})

        provider.router.add_get("/api/v4/users/me", identity)
        provider.router.add_post("/api/v4/actions/dialogs/open", open_dialog)
        self.provider_runner = web.AppRunner(provider, access_log=None)
        await self.provider_runner.setup()
        await web.TCPSite(self.provider_runner, "127.0.0.1", 0).start()
        self.bot = Bot(
            token="bot-secret", base_url=f"http://127.0.0.1:{self.provider_runner.addresses[0][1]}"
        )
        self.dp = Dispatcher()
        self.profile = UIProfile(
            "12.0.2",
            client="declared-test",
            client_features=SERVER_FEATURES["12.0.2"],
            client_evidence="test fixture",
            mm_blocks_enabled=True,
        )
        self.manager = DialogManager(self.bot, profile=self.profile, clock=lambda: self.now)
        self.adapter = HttpAdapter(
            self.bot,
            self.dp,
            trusted_peers=["127.0.0.1/32"],
            slash_bindings=[SlashBinding("team", "form", SecretStr("slash-secret"))],
            dialogs=self.manager,
            own_dialogs=True,
            request_timeout=0.5,
            shutdown_timeout=0.3,
        )
        self.runner = web.AppRunner(self.adapter.create_app(), access_log=None, shutdown_timeout=0.3)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", 0).start()
        self.base = f"http://127.0.0.1:{self.runner.addresses[0][1]}/mattergram"
        self.manager.set_callback_url(self.base + "/dialog")
        self.form = Form(
            callback_id="request",
            title="Request",
            state="application state",
            elements=(
                TextInput(name="title", label="Title", min_length=1),
                Checkbox(name="flag", label="Flag"),
            ),
        )
        self.client = aiohttp.ClientSession()

        @self.dp.slash_command()
        async def command(event):
            self.handles.append(await event.open_dialog(self.form))

    async def asyncTearDown(self):
        await self.runner.cleanup()
        await self.dp.close()
        await self.bot.close()
        await self.client.close()
        await self.provider_runner.cleanup()

    async def open(self, form=None):
        if form is not None:
            self.form = form
        async with self.client.post(
            self.base + "/slash",
            data={
                "token": "slash-secret",
                "command": "/form",
                "user_id": "user",
                "channel_id": "channel",
                "team_id": "team",
                "root_id": "root",
                "trigger_id": "real-provider-trigger",
            },
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), {"response_type": "ephemeral", "text": ""})
        return self.opened[-1]["dialog"]

    def wire(self, dialog, *, values=None, **changes):
        return {
            "type": "dialog_submission",
            "callback_id": dialog["callback_id"],
            "state": dialog["state"],
            "user_id": "user",
            "channel_id": "channel",
            "team_id": "team",
            "cancelled": False,
            "submission": {"title": "ready", "flag": False} if values is None else values,
            **changes,
        }

    async def post(self, wire):
        async with self.client.post(self.base + "/dialog", json=wire) as response:
            return response.status, await response.json()

    async def test_open_uses_real_trigger_before_ack_and_duplicate_terminal_submission_runs_once(self):
        calls = []

        @self.dp.dialog_submit()
        async def submit(event, state):
            calls.append(event.session_id)
            self.assertEqual(event.application_state, "application state")
            self.assertIs(event.values["flag"], False)
            self.assertEqual(state.key.dialog_session_id, event.session_id)
            self.assertNotIn("ready", repr(event.values))
            self.assertNotIn("mgd1.", repr(event))
            await state.set_state("completed")
            await event.answer()

        dialog = await self.open()
        self.assertNotEqual(dialog["state"], "application state")
        self.assertEqual(self.opened[0]["trigger_id"], "real-provider-trigger")
        replies = await asyncio.gather(*(self.post(self.wire(dialog)) for _ in range(8)))
        self.assertTrue(all(reply == (200, {}) for reply in replies))
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            await self.post(self.wire(dialog, values={"title": "changed", "flag": False})), (200, {})
        )
        self.assertEqual((await self.post(self.wire(dialog, user_id="another-user")))[0], 403)

    async def test_parser_and_business_validation_keep_the_form_open_until_corrected(self):
        calls = []

        @self.dp.dialog_submit()
        async def submit(event):
            calls.append(event.values["title"])
            if event.values["title"] == "bad":
                await event.field_errors({"title": "Choose another title"})
            else:
                await event.answer()

        dialog = await self.open()
        invalid = await self.post(self.wire(dialog, values={"title": "", "flag": False}))
        self.assertEqual(invalid, (200, {"errors": {"title": "This field is required"}}))
        self.assertEqual(calls, [])
        bad = self.wire(dialog, values={"title": "bad", "flag": False})
        self.assertEqual((await self.post(bad))[1], {"errors": {"title": "Choose another title"}})
        self.assertEqual((await self.post(bad))[0], 200)
        self.assertEqual(calls, ["bad"])
        self.assertEqual(await self.post(self.wire(dialog)), (200, {}))
        self.assertEqual(calls, ["bad", "ready"])

    async def test_next_step_rotates_capability_replays_old_response_and_preserves_prior_values(self):
        calls = []

        @self.dp.dialog_submit()
        async def submit(event):
            calls.append(event.form.callback_id)
            self.assertEqual(event.callback_id, "request")
            if event.form.callback_id == "request":
                await event.next(
                    Form(
                        callback_id="second",
                        title="Second",
                        elements=(TextInput(name="detail", label="Detail"),),
                    )
                )
            else:
                self.assertEqual(event.values["title"], "ready")
                self.assertIs(event.values["flag"], False)
                await event.answer()

        first = await self.open()
        first_wire = self.wire(first)
        status, reply = await self.post(first_wire)
        self.assertEqual(status, 200)
        second = reply["form"]
        self.assertEqual(second["callback_id"], first["callback_id"])
        self.assertNotEqual(second["state"], first["state"])
        self.assertEqual(
            (await self.post(self.wire(second, values={"detail": "second"}, callback_id="second")))[0], 403
        )
        self.assertEqual(await self.post(first_wire), (200, reply))
        self.assertEqual(
            (await self.post(self.wire(first, values={"title": "changed", "flag": False})))[0], 409
        )
        self.assertEqual(
            (await self.post(self.wire(second, values={"title": "tamper", "detail": "second"})))[0], 403
        )
        self.assertEqual(
            await self.post(self.wire(second, values={"title": "ready", "flag": False, "detail": "second"})),
            (200, {}),
        )
        self.assertEqual(calls, ["request", "second"])
        self.assertEqual(await self.post(first_wire), (200, {}))

    async def test_refresh_and_lookup_are_immediate_partial_value_protocols(self):
        cancellations = []
        url = self.manager.callback_url
        dynamic = Select(
            name="lookup", label="Lookup", data_source="dynamic", data_source_url=url, optional=True
        )
        select = Select(
            name="category", label="Category", options=(Option(text="A", value="a"),), refresh=True
        )
        form = Form(
            callback_id="fields",
            title="Fields",
            source_url=url,
            elements=(select, dynamic, TextInput(name="title", label="Title")),
        )

        @self.dp.dialog_lookup()
        async def lookup(event):
            self.assertEqual(event.query, "q")
            self.assertEqual(event.selected_field, "lookup")
            self.assertIs(event.values["title"], MISSING)
            await event.answer([Option(text="Result", value="result")])

        @self.dp.dialog_refresh()
        async def refresh(event):
            self.assertEqual(event.values["category"], "a")
            await event.replace_form(form.model_copy(update={"callback_id": "updated-fields"}))

        @self.dp.dialog_cancel()
        async def cancel(event):
            cancellations.append(event.form.callback_id)
            self.assertEqual(event.callback_id, "fields")
            await event.answer()

        dialog = await self.open(form)
        lookup_wire = self.wire(
            dialog,
            values={"query": "q", "selected_field": "lookup", "category": {"label": "A", "value": "a"}},
            type="dialog_lookup",
        )
        self.assertEqual(
            await self.post(lookup_wire), (200, {"items": [{"text": "Result", "value": "result"}]})
        )
        status, reply = await self.post(
            self.wire(dialog, values={"category": "a", "selected_field": "category"}, type="refresh")
        )
        self.assertEqual((status, reply["type"]), (200, "form"))
        self.assertNotEqual(reply["form"]["state"], dialog["state"])
        self.assertEqual(reply["form"]["callback_id"], "fields")
        # Actual native lookup/cancel adapters retain the opening state after
        # replacing the form. Lookup still validates the current dynamic field.
        self.assertEqual((await self.post(lookup_wire))[0], 200)
        self.assertEqual((await self.post({**lookup_wire, "callback_id": "updated-fields"}))[0], 403)
        self.assertEqual(
            (await self.post(self.wire(dialog, values={"category": "a", "title": "new"})))[0], 409
        )
        self.assertEqual(
            (await self.post(self.wire(dialog, values={"selected_field": "title"}, type="dialog_lookup")))[0],
            400,
        )
        cancellation = self.wire(dialog, values={}, cancelled=True)
        self.assertEqual(await self.post(cancellation), (200, {}))
        self.assertEqual(await self.post(cancellation), (200, {}))
        self.assertEqual(cancellations, ["updated-fields"])
        self.assertEqual((await self.post(lookup_wire))[0], 409)

    async def test_refresh_preserves_entered_values_but_honors_explicit_resets_and_new_options(self):
        options = (Option(text="A", value="a"), Option(text="B", value="b"))
        url = self.manager.callback_url

        def form(*, replacement=False):
            return Form(
                callback_id="retain",
                title="Retain",
                source_url=url,
                elements=(
                    Select(name="category", label="Category", options=options, refresh=True),
                    TextInput(name="comment", label="Comment", optional=True),
                    Checkbox(name="flag", label="Flag", optional=True),
                    MultiSelect(name="choices", label="Choices", options=options),
                    TextInput(
                        name="clear", label="Clear", optional=True, **({"default": ""} if replacement else {})
                    ),
                    Select(
                        name="changed",
                        label="Changed options",
                        options=options[:1] if replacement else options,
                        optional=True,
                    ),
                    Collapsible(
                        name="section",
                        label="Section",
                        elements=(TextInput(name="nested", label="Nested", optional=True),),
                    ),
                ),
            )

        refreshed = []

        @self.dp.dialog_refresh()
        async def refresh(event):
            if refreshed:
                self.assertNotIn("default", event.form.elements[1].model_fields_set)
                replacement = event.form
            else:
                replacement = form(replacement=True)
            refreshed.append(True)
            await event.replace_form(replacement)

        opened = await self.open(form())
        values = {
            "category": "a",
            "selected_field": "category",
            "comment": "keep",
            "flag": False,
            "choices": ["a", "b"],
            "clear": "clear me",
            "changed": "b",
            "nested": "nested keep",
        }
        status, reply = await self.post(self.wire(opened, values=values, type="refresh"))
        self.assertEqual(status, 200)
        fields = {field["name"]: field for field in reply["form"]["elements"]}
        self.assertEqual(fields["comment"]["default"], "keep")
        self.assertEqual(fields["flag"]["default"], "false")
        self.assertEqual(fields["choices"]["default"], "a,b")
        self.assertEqual(fields["clear"]["default"], "")
        self.assertNotIn("default", fields["changed"])
        self.assertEqual(fields["section"]["collapsible_config"]["elements"][0]["default"], "nested keep")
        values.update(comment="edited after refresh", choices=["b"], clear="", changed="a")
        status, again = await self.post(self.wire(reply["form"], values=values, type="refresh"))
        self.assertEqual(status, 200)
        fields = {field["name"]: field for field in again["form"]["elements"]}
        self.assertEqual(fields["comment"]["default"], "edited after refresh")
        self.assertEqual(fields["choices"]["default"], "b")
        self.assertEqual(fields["clear"]["default"], "")

    async def test_timeout_after_claim_requires_explicit_reconciliation_before_retry(self):
        calls, cancelled = [], asyncio.Event()

        @self.dp.dialog_submit()
        async def submit(event):
            calls.append(event.session_id)
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        dialog = await self.open()
        self.assertEqual((await self.post(self.wire(dialog)))[0], 504)
        self.assertTrue(cancelled.is_set())
        self.assertEqual((await self.post(self.wire(dialog)))[0], 409)
        await self.manager.reconcile(SecretStr(dialog["state"]), response={})
        self.assertEqual(await self.post(self.wire(dialog)), (200, {}))
        self.assertEqual(len(calls), 1)

    async def test_missing_explicit_response_does_not_auto_ack_valid_form(self):
        @self.dp.dialog_submit()
        async def submit(event):
            return None

        dialog = await self.open()
        self.assertEqual((await self.post(self.wire(dialog)))[0], 500)
        self.assertEqual((await self.post(self.wire(dialog)))[0], 409)

    async def test_native_open_error_redacts_trigger_and_state_metadata(self):
        self.provider_mode = "error"
        with self.assertRaises(PermissionDeniedError) as caught:
            await self.bot.open_dialog(
                SecretStr("private-trigger"),
                Form(callback_id="f", title="F", state="private-state"),
                url=self.manager.callback_url,
                profile=self.profile,
            )
        self.assertNotIn("private-trigger", caught.exception.error_id)
        self.assertNotIn("private-state", caught.exception.error_id)

    async def test_asgi_disconnect_claim_is_indeterminate_and_never_sends_ack(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()

        @self.dp.dialog_submit()
        async def submit(event):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        dialog = await self.open()
        queue, sent = asyncio.Queue(), []
        queue.put_nowait(
            {"type": "http.request", "body": json.dumps(self.wire(dialog)).encode(), "more_body": False}
        )

        async def send(message):
            sent.append(message)

        task = asyncio.create_task(
            ASGIAdapter(self.adapter)(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/mattergram/dialog",
                    "headers": [(b"content-type", b"application/json")],
                    "client": ("127.0.0.1", 1234),
                },
                queue.get,
                send,
            )
        )
        await asyncio.wait_for(entered.wait(), 2)
        queue.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(task, 2)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(sent, [])
        self.assertEqual((await self.post(self.wire(dialog)))[0], 409)

    async def test_shutdown_tracks_pre_dispatch_session_wait_and_borrows_manager(self):
        dialog = await self.open()
        key = self.manager._key(self.handles[-1].session_id)
        self.adapter._own_dialogs = False
        try:
            async with self.manager.store.lock(key):
                task = asyncio.create_task(
                    self.adapter.handle(
                        method="POST",
                        path="/mattergram/dialog",
                        headers={"content-type": "application/json"},
                        body=json.dumps(self.wire(dialog)).encode(),
                        peer="127.0.0.1",
                    )
                )
                for _ in range(20):
                    if task in self.adapter._requests:
                        break
                    await asyncio.sleep(0)
                self.assertIn(task, self.adapter._requests)
                await self.adapter.shutdown()
                self.assertTrue(task.cancelled())
            self.assertFalse(self.manager._closed)
            self.assertFalse(self.bot.is_closed)
            self.assertEqual(self.adapter._requests, set())
        finally:
            self.adapter._own_dialogs = True

    async def test_cancel_closes_session_and_invalid_scope_or_expiry_never_dispatches(self):
        calls = []

        @self.dp.dialog_submit()
        async def submit(event):
            calls.append(event)
            await event.answer()

        dialog = await self.open()
        for changes in (
            {"user_id": "other"},
            {"channel_id": "other"},
            {"team_id": "other"},
            {"callback_id": "other"},
            {"state": dialog["state"][:-1] + ("a" if dialog["state"][-1] != "a" else "b")},
        ):
            with self.subTest(changes=changes):
                self.assertEqual((await self.post(self.wire(dialog, **changes)))[0], 403)
        self.assertEqual(calls, [])
        self.assertEqual(await self.post(self.wire(dialog, values={}, cancelled=True)), (200, {}))
        self.assertEqual(await self.post(self.wire(dialog)), (200, {}))
        self.assertEqual(calls, [])
        self.assertEqual((await self.post(self.wire(dialog, type="unknown")))[0], 400)
        self.assertEqual((await self.post(self.wire(dialog, cancelled="false")))[0], 400)
        self.now += 901
        self.assertEqual((await self.post(self.wire(dialog)))[0], 403)

    async def test_child_action_uses_declared_context_new_trigger_and_bounds_live_modal_stack(self):
        url = self.manager.callback_url
        form = Form(
            callback_id="parent",
            title="Parent",
            elements=(
                DialogButton(
                    name="child",
                    label="Child",
                    action_button=DialogAction(url=url, context={"app": "trusted declaration"}),
                ),
            ),
        )
        calls = []

        @self.dp.dialog_action()
        async def action(event):
            calls.append(event.field_name)
            self.assertEqual(event.application_context, {"app": "trusted declaration"})
            await event.open_dialog(form)

        @self.dp.dialog_submit()
        async def submit(event):
            await event.answer()

        root = await self.open(form)

        def action_wire(dialog, trigger):
            context = dict(dialog["elements"][0]["action_button"]["context"])
            context["app"] = "untrusted client change"
            return {
                "type": "dialog_action",
                "user_id": "user",
                "channel_id": "channel",
                "team_id": "team",
                "trigger_id": trigger,
                "context": context,
            }

        first = action_wire(root, "child-trigger")
        self.assertEqual(await self.post(first), (200, {}))
        child = self.opened[-1]["dialog"]
        self.assertEqual(await self.post(first), (200, {}))
        self.assertEqual((await self.post(action_wire(root, "new-intentional-trigger")))[0], 409)
        self.assertEqual(await self.post(action_wire(child, "grandchild-trigger")), (200, {}))
        grandchild = self.opened[-1]["dialog"]
        self.assertEqual((await self.post(action_wire(grandchild, "fourth-trigger")))[0], 409)
        self.assertEqual(calls, ["child", "child"])
