from __future__ import annotations

import unittest

from tools.verify_dialog_scenario import verify


class DialogScenarioEvidenceTests(unittest.TestCase):
    def test_e8_03_receipt_and_public_evidence_are_pinned(self) -> None:
        result = verify()
        self.assertEqual(result["local_cases"], 13)
        self.assertEqual(result["live_server"], "owned Mattermost 11.1.0")


if __name__ == "__main__":
    unittest.main()
