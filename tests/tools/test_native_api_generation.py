from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools._common import ROOT, read_json
from tools.generate_native_api import _validated_overlay, generate, inventory, scan_source


class NativeAPIGenerationTests(unittest.TestCase):
    def test_generated_registry_and_report_are_deterministic(self) -> None:
        output, report = generate(ROOT)
        self.assertEqual(
            output,
            (ROOT / "src/mattergram/native/_operations.py").read_text(encoding="utf-8"),
        )
        report_path = ROOT / "build/reports/native-api-generation.json"
        if report_path.exists():
            self.assertEqual(report, read_json(report_path))
        self.assertEqual(report["discovered_operations"], 493)
        self.assertEqual(report["discovered_operation_ids"], 491)
        self.assertEqual(report["duplicate_operation_ids"], ["GetSharedChannelRemotes", "SearchFiles"])
        self.assertEqual(report["generated_operations"], 17)
        self.assertNotIn("description:", output)
        self.assertNotIn("summary:", output)

    def test_scanner_accepts_pinned_style_indentation_without_parsing_schema_text(self) -> None:
        source = """\
  \"/api/v4/items/{item_id}\":
   get:
    operationId: GetItem
    responses:
      '200':
        description: ok
      '404':
        description: missing
"""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "items.yaml"
            path.write_text(source, encoding="utf-8")
            operations = scan_source(path, "api/v4/source/items.yaml")
        self.assertEqual(len(operations), 1)
        operation = operations[0]
        self.assertEqual(operation.operation_id, "GetItem")
        self.assertEqual(operation.method, "GET")
        self.assertEqual(operation.path, "/api/v4/items/{item_id}")
        self.assertEqual(operation.success_statuses, (200,))

    def test_overlay_drift_from_pinned_source_is_rejected(self) -> None:
        lock = read_json(ROOT / "upstream.lock.json")
        operations, source = inventory(ROOT, lock)
        overlay = read_json(ROOT / "manifests/native-api-overlays.json")
        overlay["operations"][0]["method"] = "POST"
        with self.assertRaisesRegex(ValueError, "differ from pinned OpenAPI"):
            _validated_overlay(overlay, operations, source)


if __name__ == "__main__":
    unittest.main()
