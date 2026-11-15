from __future__ import annotations

import unittest

from tools.verify_dialog_extraction import verify


class DialogExtractionTests(unittest.TestCase):
    def test_dialog_replay_is_reproducible_and_audit_ready(self) -> None:
        report = verify()
        self.assertEqual(report["selected_modules"], 27)
        self.assertEqual(report["pending_adaptations"], 0)
        self.assertEqual(report["explicit_decisions"], 40)
        self.assertTrue(report["audit_ready"])
        self.assertTrue(report["source_written"])


if __name__ == "__main__":
    unittest.main()
