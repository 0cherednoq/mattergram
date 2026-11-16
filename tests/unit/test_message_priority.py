from __future__ import annotations

import inspect
import unittest

from mattergram import Bot
from mattergram.types import MessagePriority


class MessagePriorityTests(unittest.TestCase):
    def test_e7_04_public_signatures_match_the_contract(self) -> None:
        send = inspect.signature(Bot.send_message)
        for name, default in (("priority", None), ("silent", False), ("file_ids", None)):
            self.assertIn(name, send.parameters)
            self.assertEqual(send.parameters[name].default, default)
            self.assertEqual(send.parameters[name].kind, inspect.Parameter.KEYWORD_ONLY)

        batch = inspect.signature(Bot.send_documents)
        self.assertEqual(
            tuple(batch.parameters),
            ("self", "chat_id", "documents", "text", "root_id", "priority", "silent"),
        )

    def test_serializes_exact_native_priority_object(self) -> None:
        self.assertEqual(
            MessagePriority(
                level="urgent",
                requested_ack=True,
                persistent_notifications=True,
            ).to_wire(),
            {
                "priority": "urgent",
                "requested_ack": True,
                "persistent_notifications": True,
            },
        )

    def test_rejects_invalid_levels_flags_and_persistent_important(self) -> None:
        with self.assertRaises(ValueError):
            MessagePriority(level="standard")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            MessagePriority(level="important", requested_ack=1)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            MessagePriority(level="urgent", persistent_notifications=1)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            MessagePriority(level="important", persistent_notifications=True)


if __name__ == "__main__":
    unittest.main()
