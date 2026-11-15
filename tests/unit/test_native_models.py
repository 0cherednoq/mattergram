from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path

from mattergram import Bot
from mattergram.exceptions import ProtocolError
from mattergram.normalizers import normalize_posted
from mattergram.types import Channel, Message, User


def post(**changes):
    return {
        "id": "post-real",
        "channel_id": "channel-real",
        "user_id": "author-real",
        "message": "ping",
        "create_at": 1700000000123,
        "root_id": "",
        **changes,
    }


class ModelTests(unittest.TestCase):
    def test_captured_mattermost_posted_fixture_replays_without_io(self) -> None:
        fixture = Path(__file__).parent.parent / "fixtures/mattermost-11.1-posted.json"
        wire = json.loads(fixture.read_text(encoding="utf-8"))
        bot = Bot(token="fixture-token", base_url="http://127.0.0.1:1")
        message = normalize_posted(wire, bot=bot, self_user_id="different-user")
        post_wire = json.loads(wire["data"]["post"])
        self.assertEqual(message.raw, post_wire)
        self.assertEqual(message.chat.type, "O")
        self.assertEqual(message.event_raw, wire)
        self.assertIsNone(bot._session)

    def test_native_ids_aliases_and_millisecond_datetime(self) -> None:
        message = Message.from_wire(post())
        self.assertEqual(message.post_id, "post-real")
        self.assertEqual(message.message_id, message.post_id)
        self.assertEqual(message.chat.id, message.channel_id)
        self.assertEqual(message.from_user.id, "author-real")
        self.assertIsNone(message.chat.type)
        self.assertIsNone(message.from_user.username)
        self.assertEqual(message.text, "ping")
        self.assertIsNone(message.root_id)
        self.assertEqual(message.create_at, 1700000000123)
        self.assertEqual(message.date, datetime(2023, 11, 14, 22, 13, 20, 123000, tzinfo=UTC))

    def test_wire_snapshots_preserve_unknown_and_omitted_values(self) -> None:
        wire = post(props={"plugin": {"enabled": True}}, future_field=[1, 2])
        message = Message.from_wire(wire)
        self.assertEqual(message.raw, wire)
        wire["props"]["plugin"]["enabled"] = False
        exposed = message.raw
        exposed["props"]["plugin"]["enabled"] = False
        self.assertTrue(message.raw["props"]["plugin"]["enabled"])
        self.assertEqual(message.raw["root_id"], "")
        user = User.from_wire({"id": "u", "unknown": 7})
        self.assertEqual(user.raw, {"id": "u", "unknown": 7})
        self.assertEqual(Channel.from_wire({"id": "c", "team_id": ""}).team_id, "")

    def test_no_coercion_fabricated_ids_or_invalid_timestamp(self) -> None:
        for field, value in (
            ("id", 12),
            ("id", ""),
            ("channel_id", None),
            ("user_id", 9),
            ("create_at", "1700000000123"),
            ("create_at", True),
            ("create_at", -1),
            ("create_at", 10**30),
            ("message", False),
            ("root_id", 123),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(ProtocolError):
                Message.from_wire(post(**{field: value}))
        invalid = post()
        del invalid["id"]
        invalid["post_id"] = "invented-domain-alias"
        with self.assertRaises(ProtocolError):
            Message.from_wire(invalid)
        with self.assertRaises(ProtocolError):
            User.from_wire({"id": 123})

    def test_normalizer_ignores_self_and_other_event_types_without_io(self) -> None:
        bot = Bot(token="fixture-token", base_url="http://127.0.0.1:1/mattermost")
        wire = {"event": "posted", "data": {"post": json.dumps(post()), "channel_type": "D"}}
        message = normalize_posted(wire, bot=bot, self_user_id="bot-real")
        self.assertEqual(message.post_id, "post-real")
        self.assertIs(message._bot, bot)
        self.assertEqual(message.chat.type, "D")
        self.assertEqual(message.event_raw, wire)
        self.assertIsNone(bot._session)
        wire["data"]["post"] = json.dumps(post(user_id="bot-real"))
        self.assertIsNone(normalize_posted(wire, bot=bot, self_user_id="bot-real"))
        for event in ("post_edited", "post_deleted", "typing", "hello"):
            self.assertIsNone(normalize_posted({"event": event}, bot=bot, self_user_id="bot-real"))

    def test_normalizer_rejects_malformed_posted_and_missing_identity(self) -> None:
        bot = Bot(token="fixture-token", base_url="http://127.0.0.1:1")
        for data in ({}, {"post": {}}, {"post": "invalid-json"}, {"post": "[]"}, {"post": "{}"}):
            with self.subTest(data=data), self.assertRaises(ProtocolError):
                normalize_posted({"event": "posted", "data": data}, bot=bot, self_user_id="bot-real")
        with self.assertRaises(ValueError):
            normalize_posted({"event": "typing"}, bot=bot, self_user_id="")


if __name__ == "__main__":
    unittest.main()
