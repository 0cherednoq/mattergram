from __future__ import annotations

import json
import unittest
from pathlib import Path

from pydantic import Field, SecretStr

from mattergram import Bot, Dispatcher, F
from mattergram.exceptions import (
    IngressAuthenticationError,
    ProtocolError,
    ResponseDeadlineError,
    ResponseStateError,
    UnsupportedCapabilityError,
)
from mattergram.filters import CallbackData, Command, CommandObject
from mattergram.http import CallbackSigner, ResponseController, SlashBinding, TrustedPeers
from mattergram.http.auth import decode_form, decode_json
from mattergram.http.normalizers import normalize_callback, normalize_slash
from mattergram.types import Message, User


class HTTPEventTests(unittest.IsolatedAsyncioTestCase):
    def test_nullable_codec_default_factory_runs_per_unpacked_value(self):
        calls = []

        def default():
            calls.append(True)
            return "fresh" + str(len(calls))

        class Payload(CallbackData, prefix="factory"):
            value: str | None = Field(default_factory=default)

        self.assertEqual(Payload.unpack("factory:").value, "fresh1")
        self.assertEqual(Payload.unpack("factory:").value, "fresh2")
        self.assertEqual(Payload.unpack("factory:explicit").value, "explicit")
        self.assertEqual(len(calls), 2)

    def test_sanitized_real_http_fixtures_require_fresh_test_authentication(self):
        fixtures = Path(__file__).parent.parent / "fixtures"
        slash = json.loads((fixtures / "mattermost-11.1-slash.json").read_text(encoding="utf-8"))
        binding = SlashBinding(slash["team_id"], "fixture", SecretStr(slash["token"]))
        event = normalize_slash(slash, binding=binding, peers=self.peers, peer="127.0.0.1")
        self.assertEqual(event.name, "fixture")
        self.assertFalse(hasattr(event, "post_id"))
        callback = json.loads((fixtures / "mattermost-11.1-callback-select.json").read_text(encoding="utf-8"))
        with self.assertRaises(IngressAuthenticationError):
            normalize_callback(callback, signer=self.signer, peers=self.peers, peer="127.0.0.1")
        original = callback["context"]["mattergram"]["payload"]
        selected = callback["context"]["selected_option"]
        callback["context"] = self.signer.seal(
            original["data"],
            channel_id=callback["channel_id"],
            post_id=callback["post_id"],
            user_id=callback["user_id"],
            allowed_options=original["allowed_options"],
        )
        callback["context"]["selected_option"] = selected
        query = normalize_callback(callback, signer=self.signer, peers=self.peers, peer="127.0.0.1")
        self.assertEqual(query.data, "http:select")
        self.assertEqual(query.selected_option, "yes")
        self.assertEqual(query.message.post_id, callback["post_id"])

    async def asyncSetUp(self):
        self.bot = Bot(token="bot-fixture", base_url="https://mattermost.test/sub")
        self.bot._me = User(id="bot-native", username="native")
        self.dp = Dispatcher()
        self.now = 1000.0
        self.signer = CallbackSigner(
            instance_id=self.bot.instance_id,
            bot_id="bot-native",
            keys={"key-one": b"k" * 32},
            active_key_id="key-one",
            clock=lambda: self.now,
        )
        self.peers = TrustedPeers(["127.0.0.1/32"])
        self.binding = SlashBinding("team-native", "request", SecretStr("slash-fixture-secret"))
        self.slash = {
            "token": "slash-fixture-secret",
            "command": "/request",
            "text": "private argument",
            "user_id": "human-native",
            "channel_id": "channel-native",
            "team_id": "team-native",
            "root_id": "",
            "response_url": "https://mattermost.test/sub/hooks/commands/secret-url",
            "trigger_id": "secret-trigger",
            "future_field": "kept only in raw",
        }

    async def asyncTearDown(self):
        await self.dp.close()
        await self.bot.close()

    def slash_event(self, **changes):
        return normalize_slash(
            {**self.slash, **changes},
            binding=self.binding,
            peers=self.peers,
            peer="127.0.0.1",
            response=ResponseController("slash"),
        )

    def callback_wire(self):
        context = self.signer.seal(
            "request:approve:42",
            channel_id="channel-native",
            post_id="post-native",
            user_id="human-native",
            team_id="team-native",
            allowed_options=["yes", "no"],
        )
        context["selected_option"] = "yes"
        return {
            "user_id": "human-native",
            "post_id": "post-native",
            "channel_id": "channel-native",
            "team_id": "team-native",
            "context": context,
            "trigger_id": "secret-trigger",
        }

    def callback_event(self, wire=None):
        return normalize_callback(
            wire or self.callback_wire(),
            signer=self.signer,
            peers=self.peers,
            peer="127.0.0.1",
            response=ResponseController("callback"),
        )

    async def test_slash_native_identity_secret_redaction_and_fsm_di(self):
        event = self.slash_event()
        self.assertFalse(hasattr(event, "post_id"))
        self.assertIsNone(event.root_id)
        self.assertEqual(event.name, "request")
        self.assertEqual(event.unsafe_raw()["future_field"], "kept only in raw")
        for secret in ("slash-fixture-secret", "secret-url", "secret-trigger", "private argument"):
            self.assertNotIn(secret, repr(event))
            self.assertNotIn(secret, json.dumps(event.model_dump()))
            self.assertNotIn(secret, json.dumps(dict(event.raw)))

        @self.dp.slash_command(Command("request"))
        async def request(incoming, command, state, bot):
            self.assertIs(incoming, event)
            self.assertIs(bot, self.bot)
            self.assertIsInstance(command, CommandObject)
            self.assertEqual(command.args, "private argument")
            self.assertEqual(incoming.context.source, "http-slash")
            await state.set_state("waiting")
            await incoming.answer("accepted")

        await self.dp.feed_event(self.bot, event)
        self.assertEqual(event._response.payload, {"response_type": "ephemeral", "text": "accepted"})

    async def test_empty_slash_arguments_and_literal_commands(self):
        self.assertTrue(await Command("request")(self.slash_event(text=""), self.bot))
        msg = Message.from_wire(
            {
                "id": "post",
                "channel_id": "channel",
                "user_id": "human",
                "message": "!help@native argument",
                "create_at": 0,
            }
        )
        parsed = await Command("help", prefix="!")(msg, self.bot)
        self.assertEqual(parsed["command"].args, "argument")
        self.assertFalse(await Command("help")(msg, self.bot))
        self.assertFalse(
            await Command("help", prefix="!")(msg.model_copy(update={"message": "!help@someone"}), self.bot)
        )

    async def test_slash_token_scope_method_header_and_peer_checks(self):
        for change in ({"token": "wrong"}, {"team_id": "other"}, {"command": "/other"}):
            with self.subTest(change=change), self.assertRaises(IngressAuthenticationError):
                self.slash_event(**change)
        for kwargs in ({"method": "GET"}, {"authorization": "Token wrong"}, {"peer": "192.0.2.4"}):
            with self.assertRaises(IngressAuthenticationError):
                normalize_slash(
                    self.slash, binding=self.binding, peers=self.peers, **{"peer": "127.0.0.1", **kwargs}
                )
        with self.assertRaises(ProtocolError):
            self.slash_event(user_id=123)

    async def test_callback_data_selection_reference_and_typed_filter(self):
        wire = self.callback_wire()
        wire["data"] = "forged unsigned data"
        wire["selected_option"] = "forged unsigned choice"
        event = self.callback_event(wire)
        self.assertEqual(event.data, "request:approve:42")
        self.assertEqual(event.selected_option, "yes")
        self.assertEqual(event.message.post_id, "post-native")
        self.assertFalse(hasattr(event.message, "text"))
        self.assertEqual(event.unsafe_raw(), wire)
        self.assertNotIn("secret-trigger", repr(event))
        self.assertNotIn("request:approve", json.dumps(dict(event.raw)))

        class Action(CallbackData, prefix="request"):
            action: str
            request_id: int

        @self.dp.callback_query(Action.filter(F.action == "approve"), F.selected_option == "yes")
        async def approve(query, callback_data):
            self.assertEqual(callback_data.request_id, 42)
            await query.update_message("approved")

        await self.dp.feed_event(self.bot, event)
        self.assertEqual(event._response.payload, {"update": {"message": "approved"}})

    async def test_callback_signature_scope_expiry_and_selection_tamper(self):
        for field in ("data", "post_id", "bot_id", "instance_id", "exp"):
            wire = self.callback_wire()
            wire["context"]["mattergram"]["payload"][field] = "tampered"
            with self.subTest(field=field), self.assertRaises(IngressAuthenticationError):
                self.callback_event(wire)
        for field in ("user_id", "post_id", "team_id", "channel_id"):
            wire = self.callback_wire()
            wire[field] = "other-native-id"
            with self.assertRaises(IngressAuthenticationError):
                self.callback_event(wire)
        wire = self.callback_wire()
        wire["context"]["selected_option"] = "unlisted"
        with self.assertRaises(IngressAuthenticationError):
            self.callback_event(wire)
        wire = self.callback_wire()
        self.now += 900
        with self.assertRaises(IngressAuthenticationError):
            self.callback_event(wire)

    async def test_key_rotation_repeated_presses_and_actor_trust_boundary(self):
        wire = self.callback_wire()
        rotated = CallbackSigner(
            instance_id=self.bot.instance_id,
            bot_id="bot-native",
            keys={"key-one": b"k" * 32, "key-two": b"n" * 32},
            active_key_id="key-two",
            clock=lambda: self.now,
        )
        self.assertEqual(rotated.verify(wire)["data"], self.signer.verify(wire)["data"])
        self.assertEqual(rotated.verify(wire), rotated.verify(wire))
        with self.assertRaises(IngressAuthenticationError):
            normalize_callback(wire, signer=rotated, peers=self.peers, peer="192.0.2.4")
        context = self.signer.seal("shared", channel_id="channel-native")
        shared = {
            "user_id": "another-real-user",
            "channel_id": "channel-native",
            "post_id": "another-post",
            "context": context,
        }
        self.assertEqual(self.signer.verify(shared)["data"], "shared")

    async def test_response_once_deadline_unbound_and_baseline_capabilities(self):
        controller = ResponseController("callback")
        controller.answer("only requester")
        self.assertEqual(controller.payload, {"ephemeral_text": "only requester"})
        with self.assertRaises(ResponseStateError):
            controller.ack()
        current = ResponseController("callback")
        for action in (
            lambda: current.navigate("https://example.test"),
            lambda: current.error("not supported"),
            lambda: current.complete({"goto_location": "https://example.test"}),
        ):
            with self.assertRaises(UnsupportedCapabilityError):
                action()
        self.assertFalse(current.completed)
        current.expire()
        with self.assertRaises(ResponseDeadlineError):
            current.ack()
        event = self.slash_event()
        event._response = None
        with self.assertRaises(ResponseStateError):
            await event.answer("unbound")

    async def test_explicit_deferred_ack_and_followup(self):
        reserved, sent = [], []

        async def followup(payload):
            sent.append(payload)

        controller = ResponseController(
            "slash", reserve_deferred=lambda: reserved.append(True), followup=followup
        )
        controller.defer("working")
        self.assertTrue(controller.deferred)
        self.assertEqual(reserved, [True])
        await controller.followup("finished", ephemeral=False)
        self.assertEqual(sent, [{"response_type": "in_channel", "text": "finished"}])
        with self.assertRaises(ResponseStateError):
            controller.defer()
        with self.assertRaises(UnsupportedCapabilityError):
            ResponseController("slash").defer()

    async def test_ambiguous_json_form_and_non_telegram_callback_length(self):
        for body in (
            b'{"token":"one","token":"two"}',
            b'{"context":{"data":1,"data":2}}',
            b"[]",
            b'{"value":NaN}',
            b"\xff",
        ):
            with self.subTest(body=body), self.assertRaises(ProtocolError):
                decode_json(body)
        for body in (b"token=one&tok%65n=two", b"token=%FF", b"token=%ZZ", b"broken"):
            with self.assertRaises(ProtocolError):
                decode_form(body)
        self.assertEqual(decode_form(b"text=two+words&root_id="), {"text": "two words", "root_id": ""})

        class Large(CallbackData, prefix="large"):
            text: str

        self.assertGreater(len(Large(text="x" * 100).pack()), 64)
        with self.assertRaises(ValueError):
            Large(text="x" * 4096).pack()
