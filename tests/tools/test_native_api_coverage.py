from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from tools._common import ROOT, read_json
from tools.generate_native_api_coverage import generate


class NativeAPICoverageTests(unittest.TestCase):
    def test_committed_coverage_classifies_every_pinned_operation_id_once(self) -> None:
        report = generate(ROOT)
        self.assertEqual(report, read_json(ROOT / "docs/reports/native-api-coverage.json"))
        self.assertEqual(report["operation_ids"], 491)
        self.assertEqual(report["endpoints"], 493)
        self.assertEqual(
            report["counts"],
            {
                "generated_low_level": 17,
                "high_level_only": 3,
                "unsupported_upstream_collision": 2,
                "unsupported_not_reviewed": 469,
            },
        )
        identifiers = [item["operation_id"] for item in report["coverage"]]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertEqual(identifiers, sorted(identifiers))

    def test_high_level_facts_are_verified_against_the_pinned_source(self) -> None:
        policy_path = ROOT / "manifests/native-api-coverage.json"
        tampered = copy.deepcopy(read_json(policy_path))
        tampered["high_level_only"][0]["path"] = "/api/v4/not-the-native-path"
        actual_read_json = read_json

        def changed(path):
            return tampered if path == policy_path else actual_read_json(path)

        with patch("tools.generate_native_api_coverage.read_json", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "differ from pinned"):
                generate(ROOT)

    def test_ambiguous_operation_ids_are_never_implicitly_selected(self) -> None:
        report = generate(ROOT)
        ambiguous = {
            item["operation_id"]: item["endpoint_count"]
            for item in report["coverage"]
            if item["classification"] == "unsupported_upstream_collision"
        }
        self.assertEqual(ambiguous, {"GetSharedChannelRemotes": 2, "SearchFiles": 2})


if __name__ == "__main__":
    unittest.main()
