from __future__ import annotations

import unittest

from pydantic import ValidationError

from mattergram import ComposerButton, ComposerKeyboard


class ComposerKeyboardTests(unittest.TestCase):
    def test_declarative_lists_are_isolated_as_versioned_wire_tuples(self) -> None:
        rows = [[ComposerButton(id="accounts", text="Accounts", message="/accounts", style="primary")]]
        keyboard = ComposerKeyboard(title="Admin", rows=rows)
        rows[0].append(ComposerButton(id="imports", text="Imports", message="/imports"))
        self.assertEqual(len(keyboard.rows[0]), 1)
        self.assertEqual(
            keyboard.to_wire(),
            {
                "schema_version": 1,
                "title": "Admin",
                "rows": [
                    [
                        {
                            "id": "accounts",
                            "text": "Accounts",
                            "message": "/accounts",
                            "style": "primary",
                            "disabled": False,
                        }
                    ]
                ],
            },
        )

    def test_layout_ids_text_messages_and_styles_are_bounded(self) -> None:
        valid = ComposerButton(id="check_all", text="Check all", message="check all")
        invalid_buttons = (
            {"id": "bad id", "text": "Run", "message": "/run"},
            {"id": "run", "text": " Run", "message": "/run"},
            {"id": "run", "text": "Run\nnow", "message": "/run"},
            {"id": "run", "text": "Run", "message": ""},
            {"id": "run", "text": "Run", "message": "/run", "style": "success"},
        )
        for payload in invalid_buttons:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                ComposerButton(**payload)
        with self.assertRaises(ValidationError):
            ComposerKeyboard(rows=[])
        with self.assertRaises(ValidationError):
            ComposerKeyboard(rows=[[valid] * 5])
        with self.assertRaises(ValidationError):
            ComposerKeyboard(rows=[[valid], [valid]])


if __name__ == "__main__":
    unittest.main()
