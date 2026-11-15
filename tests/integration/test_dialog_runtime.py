from __future__ import annotations

import unittest
from copy import deepcopy
from typing import Any

import aiohttp
from aiohttp import web
from pydantic import SecretStr

from mattergram import Bot, Dispatcher
from mattergram.fsm.state import State, StatesGroup
from mattergram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from mattergram.http import CallbackSigner, HttpAdapter, SlashBinding
from mattergram.types import Channel, DialogSubmission, Message, SlashCommand
from mattergram.ui import UIProfile
from mattergram.ui.capabilities import SERVER_FEATURES
from mattergram_dialog import (
    Back,
    Button,
    CoalescingDialogUpdater,
    Const,
    Dialog,
    DialogRef,
    DialogRegistry,
    DialogRuntime,
    DialogStorage,
    Format,
    FormWindow,
    Navigate,
    OpenForm,
    ShowMode,
    Window,
    setup_dialogs,
)
from mattergram_dialog.api.protocols.manager import DialogManager
from mattergram_dialog.rendering import PostControl
from mattergram_dialog.widgets.common import WidgetAction, WidgetActionResult
from mattergram_dialog.widgets.common.base import Widget
from mattergram_dialog.widgets.form import TextInput


class Scenario(StatesGroup):
    catalog = State()
    card = State()
    form = State()
    review = State()
    confirmation = State()


class ProtocolButton(Widget):
    def __init__(self) -> None:
        super().__init__(id="protocol")

    async def render_controls(self, data: Any, manager: DialogManager) -> tuple[PostControl, ...]:
        del data
        context = manager.current_context()
        return (
            PostControl(
                "protocol",
                "Protocol",
                intent_id=context.intent_id,
                source_state=context.state.state,
                action="increment",
                widget_id="protocol",
            ),
        )

    async def process_action(
        self,
        action: WidgetAction,
        event: Any,
        manager: DialogManager,
    ) -> WidgetActionResult:
        del event
        if action.type != "increment":
            raise ValueError("unexpected protocol action")
        manager.dialog_data["item"] = "Protocol book"
        return WidgetActionResult()


class DialogRuntimeHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.posts: list[dict[str, Any]] = []
        self.opened: list[dict[str, Any]] = []
        self.effects: list[str] = []
        self.refreshes = 0
        self.patch_count = 0
        self.dialog_ref: DialogRef | None = None
        provider = web.Application()

        async def identity(request: web.Request) -> web.Response:
            return web.json_response({"id": "bot-native", "username": "native"})

        async def create_post(request: web.Request) -> web.Response:
            payload = await request.json()
            post = {
                "id": f"post-{len(self.posts) + 1}",
                "channel_id": payload["channel_id"],
                "user_id": "bot-native",
                "message": payload["message"],
                "create_at": len(self.posts) + 1,
                "props": payload.get("props", {}),
            }
            self.posts.append(post)
            return web.json_response(post, status=201)

        async def get_post(request: web.Request) -> web.Response:
            post_id = request.match_info["post_id"]
            return web.json_response(next(post for post in self.posts if post["id"] == post_id))

        async def patch_post(request: web.Request) -> web.Response:
            self.patch_count += 1
            post_id = request.match_info["post_id"]
            post = next(post for post in self.posts if post["id"] == post_id)
            post.update(await request.json())
            return web.json_response(post)

        async def open_dialog(request: web.Request) -> web.Response:
            self.opened.append(await request.json())
            return web.json_response({"status": "OK"})

        provider.router.add_get("/api/v4/users/me", identity)
        provider.router.add_post("/api/v4/posts", create_post)
        provider.router.add_get("/api/v4/posts/{post_id}", get_post)
        provider.router.add_put("/api/v4/posts/{post_id}/patch", patch_post)
        provider.router.add_post("/api/v4/actions/dialogs/open", open_dialog)
        self.provider_runner = web.AppRunner(provider, access_log=None)
        await self.provider_runner.setup()
        await web.TCPSite(self.provider_runner, "127.0.0.1", 0).start()
        provider_port = self.provider_runner.addresses[0][1]
        self.bot = Bot(token="bot-secret", base_url=f"http://127.0.0.1:{provider_port}")
        self.dispatcher = Dispatcher()
        self.signer = CallbackSigner(
            instance_id=self.bot.instance_id,
            bot_id="bot-native",
            keys={"main": b"s" * 32},
            active_key_id="main",
        )
        from mattergram.http import DialogManager as NativeDialogManager

        self.native_dialogs = NativeDialogManager(
            self.bot,
            profile=UIProfile(
                "11.1.0",
                client="dialog-runtime-test",
                client_features=SERVER_FEATURES["11.1.0"],
                client_evidence="local integration test",
            ),
        )
        self.adapter = HttpAdapter(
            self.bot,
            self.dispatcher,
            trusted_peers=["127.0.0.1/32"],
            slash_bindings=[SlashBinding("team", "catalog", SecretStr("slash-secret"))],
            callback_signer=self.signer,
            dialogs=self.native_dialogs,
            own_dialogs=True,
            request_timeout=1,
        )
        self.runner = web.AppRunner(self.adapter.create_app(), access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", 0).start()
        self.base = f"http://127.0.0.1:{self.runner.addresses[0][1]}/mattergram"
        self.native_dialogs.set_callback_url(self.base + "/dialog")
        self.client = aiohttp.ClientSession()
        self.storage = MemoryStorage()
        self.isolation = SimpleEventIsolation()

        async def start_data(data: Any, manager: DialogManager) -> None:
            if isinstance(data, dict):
                manager.dialog_data.update(data)

        async def save(event: DialogSubmission, manager: DialogManager) -> None:
            title = event.values["title"]
            manager.dialog_data["title"] = title
            if title == "Needs review":
                await manager.switch_to(Scenario.review, show_mode=ShowMode.FORM)
                return
            self.effects.append(title)
            await event.answer()
            await manager.switch_to(Scenario.confirmation, show_mode=ShowMode.EDIT)

        async def review(event: DialogSubmission, manager: DialogManager) -> None:
            detail = event.values["detail"]
            manager.dialog_data["title"] += ": " + detail
            self.effects.append(manager.dialog_data["title"])
            await event.answer()
            await manager.switch_to(Scenario.confirmation, show_mode=ShowMode.EDIT)

        async def refresh(event: Any, button: Button, manager: DialogManager) -> None:
            del event, button
            self.refreshes += 1
            manager.dialog_data["item"] = "Refreshed book"

        scenario = Dialog(
            Window(
                Const("Catalog"),
                Navigate(Const("Open card"), id="card", state=Scenario.card),
                state=Scenario.catalog,
            ),
            Window(
                Format("Card: {item}"),
                OpenForm(Const("Configure"), id="configure", state=Scenario.form),
                Button(Const("Refresh"), id="refresh", on_click=refresh),
                ProtocolButton(),
                Back(Const("Back")),
                state=Scenario.card,
            ),
            FormWindow(
                TextInput(id="title", label="Title"),
                title=Const("Configure item"),
                submit_text=Const("Confirm"),
                on_submit=save,
                callback_id="scenario_form",
                state=Scenario.form,
            ),
            FormWindow(
                TextInput(id="detail", label="Detail"),
                title=Const("Review item"),
                submit_text=Const("Approve"),
                on_submit=review,
                callback_id="scenario_review",
                state=Scenario.review,
            ),
            Window(Format("Confirmed: {title}"), state=Scenario.confirmation),
            on_start=start_data,
        )
        registry = DialogRegistry(scenario)
        self.dialog_storage = DialogStorage(self.storage, self.isolation)
        self.runtime = DialogRuntime(
            self.bot,
            self.dialog_storage,
            registry,
            instance_id=self.bot.instance_id,
            bot_id="bot-native",
            callback_url=self.base + "/callback",
            callback_signer=self.signer,
            action_ttl=1800,
        )
        setup_dialogs(self.dispatcher, self.runtime)
        self.unrelated_callbacks: list[str | None] = []

        @self.dispatcher.callback_query()
        async def unrelated(event: Any) -> None:
            self.unrelated_callbacks.append(event.data)
            await event.answer("fallback")

        @self.dispatcher.slash_command()
        async def catalog(event: SlashCommand) -> None:
            self.dialog_ref = await self.runtime.start(
                event,
                Scenario.catalog,
                {"item": "Native book"},
            )
            await event.answer()

        self.registry = registry

    async def asyncTearDown(self) -> None:
        await self.runner.cleanup()
        await self.dispatcher.close()
        await self.bot.close()
        await self.client.close()
        await self.provider_runner.cleanup()
        await self.storage.close()
        await self.isolation.close()

    @staticmethod
    def action_context(post: dict[str, Any], index: int = 0) -> dict[str, Any]:
        block_actions = post["props"].get("mm_blocks_actions")
        if isinstance(block_actions, dict):
            values = tuple(block_actions.values())
            value = values[index]["context"]
            if not isinstance(value, dict):
                raise TypeError("Expected a native Blocks action context")
            return value
        actions = [
            action for attachment in post["props"]["attachments"] for action in attachment.get("actions", [])
        ]
        value = actions[index]["integration"]["context"]
        if not isinstance(value, dict):
            raise TypeError("Expected a native action context")
        return value

    async def slash(self) -> tuple[int, dict[str, Any]]:
        async with self.client.post(
            self.base + "/slash",
            data={
                "token": "slash-secret",
                "team_id": "team",
                "channel_id": "channel",
                "user_id": "human",
                "command": "/catalog",
                "trigger_id": "slash-trigger",
            },
        ) as response:
            return response.status, await response.json()

    async def callback(
        self,
        post: dict[str, Any],
        post_id: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        async with self.client.post(
            self.base + "/callback",
            json={
                "user_id": "human",
                "post_id": post_id,
                "channel_id": "channel",
                "team_id": "team",
                "trigger_id": "callback-trigger",
                "context": context or self.action_context(post),
            },
        ) as response:
            result = response.status, await response.json()
        update = result[1].get("update")
        if isinstance(update, dict):
            post.update(deepcopy(update))
        return result

    async def open_form(self) -> dict[str, Any]:
        await self.slash()
        await self.callback(self.posts[0], "post-1")
        await self.callback(self.posts[0], "post-1")
        value = self.opened[-1]["dialog"]
        if not isinstance(value, dict):
            raise TypeError("Expected a native form payload")
        return value

    async def test_catalog_card_form_confirmation_through_http_ingress(self) -> None:
        self.assertEqual(await self.slash(), (200, {"response_type": "ephemeral", "text": ""}))
        self.assertEqual([post["message"] for post in self.posts], ["Catalog"])
        old_context = deepcopy(self.action_context(self.posts[0]))
        status, replacement = await self.callback(self.posts[0], "post-1")
        self.assertEqual(status, 200)
        self.assertEqual(replacement["update"]["message"], "Card: Native book")
        self.assertEqual([post["message"] for post in self.posts], ["Card: Native book"])
        self.assertEqual(
            await self.callback(self.posts[0], "post-1", context=old_context),
            (200, {"ephemeral_text": "This dialog action is outdated"}),
        )
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(await self.callback(self.posts[0], "post-1"), (200, {}))
        self.assertEqual(len(self.opened), 1)
        wire_form = self.opened[0]["dialog"]
        self.assertEqual(wire_form["callback_id"], "scenario_form")
        self.assertNotEqual(wire_form["state"], "")

        submission = {
            "type": "dialog_submission",
            "callback_id": wire_form["callback_id"],
            "state": wire_form["state"],
            "user_id": "human",
            "channel_id": "channel",
            "team_id": "team",
            "cancelled": False,
            "submission": {"title": "Approved"},
        }
        async with self.client.post(self.base + "/dialog", json=submission) as response:
            self.assertEqual((response.status, await response.json()), (200, {}))
        self.assertEqual(
            [post["message"] for post in self.posts],
            ["Confirmed: Approved"],
        )
        self.assertEqual(self.effects, ["Approved"])

        async with self.client.post(self.base + "/dialog", json=submission) as response:
            self.assertEqual((response.status, await response.json()), (200, {}))
        self.assertEqual(self.effects, ["Approved"])
        self.assertEqual(len(self.posts), 1)

        key = self.dialog_storage.key(
            instance_id=self.bot.instance_id,
            bot_id="bot-native",
            channel_id="channel",
            actor_id="human",
        )
        session = await self.dialog_storage.load(key)
        current = session.current()
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.state, Scenario.confirmation)
        self.assertEqual(current.dialog_data["title"], "Approved")

    async def test_keyboard_message_navigation_edits_the_existing_dialog_post(self) -> None:
        await self.slash()
        keyboard_message = Message.from_wire(
            {
                "id": "keyboard-message",
                "channel_id": "channel",
                "user_id": "human",
                "message": "/catalog card",
                "create_at": 1,
            },
            bot=self.bot,
        )
        keyboard_message._channel = Channel.from_wire({"id": "channel", "team_id": ""})

        ref = await self.runtime.navigate(keyboard_message, Scenario.card)

        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.posts[0]["message"], "Card: Native book")
        self.assertEqual(ref.post_id, "post-1")
        self.assertEqual(ref.actor_id, "human")

    async def test_keyboard_message_navigation_recreates_a_deleted_dialog_post(self) -> None:
        await self.slash()
        self.posts[0]["delete_at"] = 1
        keyboard_message = Message.from_wire(
            {
                "id": "keyboard-message",
                "channel_id": "channel",
                "user_id": "human",
                "message": "/catalog card",
                "create_at": 1,
            },
            bot=self.bot,
        )
        keyboard_message._channel = Channel.from_wire({"id": "channel", "team_id": ""})

        ref = await self.runtime.navigate(keyboard_message, Scenario.card)

        self.assertEqual(len(self.posts), 2)
        self.assertEqual(self.posts[-1]["message"], "Card: Native book")
        self.assertEqual(ref.post_id, "post-2")
        self.assertEqual(ref.actor_id, "human")

    async def test_keyboard_message_response_always_creates_a_new_dialog_post(self) -> None:
        await self.slash()
        keyboard_message = Message.from_wire(
            {
                "id": "keyboard-message",
                "channel_id": "channel",
                "user_id": "human",
                "message": "Card",
                "create_at": 1,
            },
            bot=self.bot,
        )
        keyboard_message._channel = Channel.from_wire({"id": "channel", "team_id": "", "type": "D"})

        card_ref = await self.runtime.respond(
            keyboard_message,
            Scenario.card,
            {"item": "Keyboard book"},
        )
        catalog_ref = await self.runtime.respond(keyboard_message, Scenario.catalog)

        self.assertEqual(
            [post["message"] for post in self.posts], ["Catalog", "Card: Keyboard book", "Catalog"]
        )
        self.assertEqual(card_ref.post_id, "post-2")
        self.assertEqual(catalog_ref.post_id, "post-3")

    async def test_blocks_transition_patches_post_and_rotates_action_registry(self) -> None:
        self.runtime.ui_profile = UIProfile(
            "12.0.2",
            client="dialog-runtime-test",
            client_features=SERVER_FEATURES["12.0.2"],
            client_evidence="local integration test",
            mm_blocks_enabled=True,
        )
        self.assertEqual(await self.slash(), (200, {"response_type": "ephemeral", "text": ""}))
        post = self.posts[0]
        self.assertIn("mm_blocks", post["props"])
        self.assertIn("mm_blocks_actions", post["props"])
        old_context = deepcopy(self.action_context(post))
        payload = old_context["mattergram"]["payload"]
        self.assertEqual(payload["exp"] - payload["iat"], 1800)

        self.assertEqual(await self.callback(post, "post-1"), (200, {}))
        await self.runtime.drain()
        self.assertEqual(post["message"], "Card: Native book")
        self.assertIn("mm_blocks", post["props"])
        self.assertEqual(len(post["props"]["mm_blocks_actions"]), 4)
        self.assertNotEqual(self.action_context(post), old_context)
        self.assertEqual(
            await self.callback(post, "post-1", context=old_context),
            (200, {"ephemeral_text": "This dialog action is outdated"}),
        )

    async def test_rejects_action_ttl_above_signer_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "action_ttl"):
            DialogRuntime(
                self.bot,
                self.dialog_storage,
                self.registry,
                instance_id=self.bot.instance_id,
                bot_id="bot-native",
                callback_url=self.base + "/callback",
                callback_signer=self.signer,
                action_ttl=self.signer.max_ttl + 1,
            )

    async def test_form_to_form_transition_uses_native_next_response(self) -> None:
        first = await self.open_form()
        async with self.client.post(
            self.base + "/dialog",
            json={
                "type": "dialog_submission",
                "callback_id": first["callback_id"],
                "state": first["state"],
                "user_id": "human",
                "channel_id": "channel",
                "team_id": "team",
                "cancelled": False,
                "submission": {"title": "Needs review"},
            },
        ) as response:
            self.assertEqual(response.status, 200)
            replacement = await response.json()
        self.assertEqual(replacement["type"], "form")
        second = replacement["form"]
        self.assertEqual(second["callback_id"], "scenario_form")
        self.assertNotEqual(second["state"], first["state"])
        async with self.client.post(
            self.base + "/dialog",
            json={
                "type": "dialog_submission",
                "callback_id": second["callback_id"],
                "state": second["state"],
                "user_id": "human",
                "channel_id": "channel",
                "team_id": "team",
                "cancelled": False,
                "submission": {"detail": "Accepted"},
            },
        ) as response:
            self.assertEqual((response.status, await response.json()), (200, {}))
        self.assertEqual(self.effects, ["Needs review: Accepted"])
        self.assertEqual(self.posts[-1]["message"], "Confirmed: Needs review: Accepted")

    async def test_back_updates_the_same_post_and_rejects_repeated_action(self) -> None:
        await self.slash()
        self.assertEqual(len(self.posts), 1)
        await self.callback(self.posts[0], "post-1")
        self.assertEqual(self.posts[0]["message"], "Card: Native book")
        back_context = deepcopy(self.action_context(self.posts[0], 3))

        status, replacement = await self.callback(
            self.posts[0],
            "post-1",
            context=back_context,
        )
        self.assertEqual(status, 200)
        self.assertEqual(replacement["update"]["message"], "Catalog")
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(
            await self.callback(self.posts[0], "post-1", context=back_context),
            (200, {"ephemeral_text": "This dialog action is outdated"}),
        )

    async def test_declarative_button_invokes_callback_and_rebuilds_the_same_post(self) -> None:
        await self.slash()
        post = self.posts[0]
        await self.callback(post, "post-1")
        refresh_context = deepcopy(self.action_context(post, 1))

        status, replacement = await self.callback(post, "post-1", context=refresh_context)

        self.assertEqual(status, 200)
        self.assertEqual(replacement["update"]["message"], "Card: Refreshed book")
        self.assertEqual(post["message"], "Card: Refreshed book")
        self.assertEqual(self.refreshes, 1)
        self.assertEqual(
            await self.callback(post, "post-1", context=refresh_context),
            (200, {"ephemeral_text": "This dialog action is outdated"}),
        )
        self.assertEqual(self.refreshes, 1)

    async def test_custom_widget_action_protocol_has_no_runtime_type_branch(self) -> None:
        await self.slash()
        post = self.posts[0]
        await self.callback(post, "post-1")

        status, replacement = await self.callback(
            post,
            "post-1",
            context=deepcopy(self.action_context(post, 2)),
        )

        self.assertEqual(status, 200)
        self.assertEqual(replacement["update"]["message"], "Card: Protocol book")
        self.assertEqual(post["message"], "Card: Protocol book")

    async def test_background_update_rebuilds_signed_controls_in_the_same_post(self) -> None:
        await self.slash()
        post = self.posts[0]
        old_context = deepcopy(self.action_context(post))
        self.assertIsNotNone(self.dialog_ref)
        assert self.dialog_ref is not None

        self.assertTrue(
            await self.runtime.update(
                self.dialog_ref,
                {"item": "Background book"},
                revision=2,
            )
        )
        self.assertFalse(
            await self.runtime.update(
                self.dialog_ref,
                {"item": "Stale book"},
                revision=1,
            )
        )

        self.assertEqual(len(self.posts), 1)
        self.assertEqual(post["message"], "Catalog")
        self.assertNotEqual(self.action_context(post), old_context)
        await self.callback(post, "post-1")
        self.assertEqual(post["message"], "Card: Background book")

    async def test_coalescing_updater_keeps_only_the_latest_pending_revision(self) -> None:
        await self.slash()
        post = self.posts[0]
        assert self.dialog_ref is not None
        updater = CoalescingDialogUpdater(self.runtime, interval=0.05)

        self.assertTrue(updater.submit(self.dialog_ref, 1, {"item": "First snapshot"}))
        self.assertTrue(updater.submit(self.dialog_ref, 2, {"item": "Latest snapshot"}))
        self.assertFalse(updater.submit(self.dialog_ref, 1, {"item": "Stale snapshot"}))
        self.assertEqual(updater.pending_count, 1)
        await updater.close()

        self.assertEqual(self.patch_count, 1)
        await self.callback(post, "post-1")
        self.assertEqual(post["message"], "Card: Latest snapshot")
        with self.assertRaisesRegex(RuntimeError, "closed"):
            updater.submit(self.dialog_ref, 3, {"item": "Too late"})

    async def test_native_cancel_returns_to_the_post_that_opened_the_form(self) -> None:
        form = await self.open_form()
        self.assertIs(form["notify_on_cancel"], True)
        async with self.client.post(
            self.base + "/dialog",
            json={
                "type": "dialog_submission",
                "callback_id": form["callback_id"],
                "state": form["state"],
                "user_id": "human",
                "channel_id": "channel",
                "team_id": "team",
                "cancelled": True,
                "submission": {},
            },
        ) as response:
            self.assertEqual((response.status, await response.json()), (200, {}))
        key = self.dialog_storage.key(
            instance_id=self.bot.instance_id,
            bot_id="bot-native",
            channel_id="channel",
            actor_id="human",
        )
        session = await self.dialog_storage.load(key)
        current = session.current()
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.state, Scenario.card)
        self.assertEqual(len(self.posts), 1)

    async def test_unrelated_callback_reaches_the_next_router_handler(self) -> None:
        context = self.signer.seal(
            "another-feature",
            channel_id="channel",
            post_id="other-post",
            user_id="human",
        )
        async with self.client.post(
            self.base + "/callback",
            json={
                "user_id": "human",
                "post_id": "other-post",
                "channel_id": "channel",
                "team_id": "team",
                "context": context,
            },
        ) as response:
            self.assertEqual(
                (response.status, await response.json()),
                (200, {"ephemeral_text": "fallback"}),
            )
        self.assertEqual(self.unrelated_callbacks, ["another-feature"])
