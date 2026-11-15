from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools._common import read_json, sha256, tree_digest, write_json
from tools.audit_port import audit, audit_sources
from tools.extract_core import extract, json_bytes
from tools.fetch_upstream import describe_files
from tools.rewrite_imports import rewrite_namespace

POLICY = {
    "forbidden_import_roots": ["aiogram", "aiogram_dialog", "telegram"],
    "forbidden_symbol_prefixes": ["Telegram", "Aiogram"],
    "allowed_external_import_roots": ["magic_filter"],
}


class CodemodTests(unittest.TestCase):
    def test_rewrites_only_import_tokens_and_preserves_layout(self) -> None:
        source = b'''"""aiogram is the donor; do not rewrite documentation."""
# from aiogram import TelegramObject -- a comment
from aiogram.helper import (
    VALUE as constant,  # preserve this comment
)
import aiogram.helper as helper
from .helper import VALUE
value = "aiogram.helper"
'''
        result = rewrite_namespace(source)
        self.assertIn(b"from mattergram.helper import (", result)
        self.assertIn(b"import mattergram.helper as helper", result)
        self.assertIn(b"from .helper import VALUE", result)
        self.assertIn(b'"aiogram.helper"', result)
        self.assertIn(b"# from aiogram import TelegramObject", result)
        self.assertEqual(rewrite_namespace(result), result)

    def test_unicode_columns_encodings_and_line_endings(self) -> None:
        for encoding, prefix in (("utf-8", ""), ("utf-8-sig", ""), ("latin-1", "# coding: latin-1\r\n")):
            source = (prefix + 'name = "caf\u00e9"; from aiogram.helper import VALUE\r\n').encode(encoding)
            expected = source.replace(b"from aiogram.helper", b"from mattergram.helper")
            with self.subTest(encoding=encoding):
                self.assertEqual(rewrite_namespace(source), expected)

    def test_unaliased_import_never_silently_breaks_bindings(self) -> None:
        with self.assertRaisesRegex(ValueError, "binding-aware"):
            rewrite_namespace(b"import aiogram.helper\nresult = aiogram.helper.VALUE\n")

    def test_absolute_import_after_guard_dedent_is_rewritten(self) -> None:
        source = b"try:\n    import optional\nexcept ImportError:\n    optional = None\nfrom aiogram.helper import VALUE\n"
        self.assertIn(b"from mattergram.helper import VALUE", rewrite_namespace(source))


class PortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.snapshot = self.root / "upstream/snapshots/fixture"
        paths = {
            "aiogram/__init__.py": b"from .core import transform\n",
            "aiogram/core.py": b"from aiogram.helper import VALUE\n\ndef transform(value):\n    return value + VALUE\n",
            "aiogram/helper.py": b"VALUE = 3\n",
            "LICENSE": b"Fixture copyright and license notice\n",
        }
        for name, data in paths.items():
            path = self.snapshot / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        files = describe_files(self.snapshot)
        self.source = {
            "id": "aiogram",
            "namespace": "aiogram",
            "commit": "a" * 40,
            "ref": "fixture",
            "repository": "https://github.com/example/fixture",
            "snapshot": "upstream/snapshots/fixture",
            "tree_sha256": tree_digest(files),
            "files": files,
            "licenses": ["LICENSE"],
        }
        self.lock = {"schema_version": 1, "sources": [self.source]}
        entries = []
        for name in paths:
            if not name.endswith(".py"):
                continue
            module = name.removesuffix(".py").replace("/", ".").removesuffix(".__init__")
            entries.append(
                {
                    "module": module,
                    "source_id": "aiogram",
                    "source_commit": self.source["commit"],
                    "source_path": name,
                    "source_sha256": files[name]["sha256"],
                    "target": "src/" + name.replace("aiogram/", "mattergram/"),
                    "action": "copy",
                    "reason": "Behavioral fixture, not donor production code",
                    "codemods": ["namespace_imports_v1"],
                    "attribution": [{"path": "LICENSE", "sha256": files["LICENSE"]["sha256"]}],
                    "related_tests": [],
                    "dependencies": [],
                }
            )
        self.manifest = {
            "schema_version": 1,
            "source": {key: self.source[key] for key in ("id", "commit", "ref", "repository", "tree_sha256")},
            "target_namespace": "mattergram",
            "entries": entries,
            "native": [],
            "excluded_scopes": [{"prefix": "aiogram/types/", "reason": "Platform-specific graph excluded"}],
            "audit_policy": POLICY,
        }
        self.output = self.root / "build/port-staging/fixture"
        self.maintained = self.root / "src/mattergram/native.py"
        self.maintained.parent.mkdir(parents=True)
        self.maintained.write_bytes(b"# maintained, never overwritten\n")

    def execute_fixture(self, stage: Path, expected: int) -> None:
        script = "import sys; sys.path.insert(0, sys.argv[1]); from mattergram import transform; assert transform(3) == int(sys.argv[2])"
        subprocess.run(
            [sys.executable, "-I", "-B", "-c", script, str(stage / "src"), str(expected)],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=True,
        )

    def core_entry(self):
        return next(entry for entry in self.manifest["entries"] if entry["module"] == "aiogram.core")

    def add_patch(self, *, status: str = "applied") -> Path:
        entry = self.core_entry()
        entry["action"] = "adapt"
        before = rewrite_namespace((self.snapshot / entry["source_path"]).read_bytes())
        after = before.replace(b"return value + VALUE", b"return value * VALUE")
        patch = {
            "schema_version": 1,
            "reason": "Change observed fixture arithmetic to test a reviewed semantic adaptation",
            "input_sha256": sha256(before),
            "output_sha256": sha256(after),
            "replacements": [
                {"before": "return value + VALUE", "after": "return value * VALUE", "occurrences": 1}
            ],
        }
        path = self.root / "manifests/patches/arithmetic.json"
        write_json(path, patch)
        entry["adaptation"] = {
            "status": status,
            "patches": [{"path": "manifests/patches/arithmetic.json", "sha256": sha256(path.read_bytes())}],
        }
        return path

    def test_repeatable_stage_preserves_maintained_code_and_mtimes(self) -> None:
        first = extract(self.root, self.output, self.manifest, self.lock)
        times = {str(path): path.stat().st_mtime_ns for path in self.output.rglob("*") if path.is_file()}
        second = extract(self.root, self.output, self.manifest, self.lock)
        other = self.root / "build/port-staging/independent"
        self.assertEqual(extract(self.root, other, self.manifest, self.lock), first)
        self.assertEqual(first, second)
        self.assertEqual(describe_files(self.output), describe_files(other))
        self.assertEqual(
            times, {str(path): path.stat().st_mtime_ns for path in self.output.rglob("*") if path.is_file()}
        )
        self.assertEqual(self.maintained.read_bytes(), b"# maintained, never overwritten\n")
        self.assertTrue(audit(self.root, self.output, self.manifest, self.lock)["ready"])
        self.execute_fixture(self.output, 6)
        self.assertTrue(audit(self.root, self.output, self.manifest, self.lock)["ready"])

    def test_native_composition_is_pinned_and_never_overwrites_maintained_source(self) -> None:
        original = self.maintained.read_bytes()
        self.manifest["native"] = [
            {
                "target": "src/mattergram/native.py",
                "action": "implement",
                "reason": "Compose a maintained native boundary for static dependency auditing",
                "source_sha256": sha256(original),
            }
        ]
        receipt = extract(self.root, self.output, self.manifest, self.lock)
        self.assertEqual(len(receipt["native_files"]), 1)
        self.assertEqual((self.output / "src/mattergram/native.py").read_bytes(), original)
        self.assertEqual(self.maintained.read_bytes(), original)
        self.assertTrue(audit(self.root, self.output, self.manifest, self.lock)["ready"])
        self.maintained.write_bytes(b"# unreviewed native edit\n")
        with self.assertRaisesRegex(ValueError, "Native boundary hash differs"):
            extract(self.root, self.output, self.manifest, self.lock)
        self.assertEqual(self.maintained.read_bytes(), b"# unreviewed native edit\n")

    def test_edited_staging_is_reported_and_never_overwritten(self) -> None:
        extract(self.root, self.output, self.manifest, self.lock)
        path = self.output / "src/mattergram/core.py"
        path.write_bytes(b"# a manual adaptation not in the manifest\n")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            extract(self.root, self.output, self.manifest, self.lock)
        self.assertEqual(path.read_bytes(), b"# a manual adaptation not in the manifest\n")
        self.assertEqual(
            audit(self.root, self.output, self.manifest, self.lock)["findings"][0]["kind"], "integrity"
        )

    def test_output_must_stay_in_staging_and_native_boundaries_are_protected(self) -> None:
        for output in (
            self.root / "src",
            self.root / "build/port-staging/../../../src",
            self.root.parent / "outside",
        ):
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, "staging"):
                extract(self.root, output, self.manifest, self.lock)
        self.manifest["native"] = [
            {"target": self.core_entry()["target"], "action": "implement", "reason": "native"}
        ]
        with self.assertRaisesRegex(ValueError, "native boundary"):
            extract(self.root, self.output, self.manifest, self.lock)
        self.assertFalse(self.output.exists())

    def test_reserved_native_directory_cannot_be_populated_by_extraction(self) -> None:
        self.manifest["native"] = [
            {"target": "src/mattergram/native/", "action": "implement", "reason": "native"}
        ]
        self.core_entry()["target"] = "src/mattergram/native/copied.py"
        with self.assertRaisesRegex(ValueError, "native boundary"):
            extract(self.root, self.output, self.manifest, self.lock)

    def test_source_and_manifest_hash_tampering_prevents_materialization(self) -> None:
        self.core_entry()["source_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "hash differs"):
            extract(self.root, self.output, self.manifest, self.lock)
        self.assertFalse(self.output.exists())
        self.core_entry()["source_sha256"] = self.source["files"]["aiogram/core.py"]["sha256"]
        (self.snapshot / "aiogram/core.py").write_bytes(b"# changed source")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            extract(self.root, self.output, self.manifest, self.lock)
        self.assertFalse(self.output.exists())

    def test_missing_attribution_and_excluded_scopes_are_errors(self) -> None:
        self.core_entry()["attribution"] = []
        with self.assertRaisesRegex(ValueError, "attribution"):
            extract(self.root, self.output, self.manifest, self.lock)
        self.core_entry()["source_path"] = "aiogram/types/forbidden.py"
        with self.assertRaisesRegex(ValueError, "excluded scope"):
            extract(self.root, self.output, self.manifest, self.lock)

    def test_excluded_scope_exception_is_exact(self) -> None:
        source_path = self.core_entry()["source_path"]
        self.manifest["excluded_scopes"] = [
            {
                "prefix": source_path,
                "exceptions": [source_path],
                "reason": "Only one reviewed generic module crosses the platform-specific scope",
            }
        ]
        extract(self.root, self.output, self.manifest, self.lock)
        self.manifest["excluded_scopes"][0]["exceptions"] = ["aiogram/helper.py"]
        with self.assertRaisesRegex(ValueError, "excluded scope"):
            extract(self.root, self.output, self.manifest, self.lock)

    def test_case_insensitive_target_collision_is_rejected(self) -> None:
        self.manifest["entries"][2]["target"] = (
            self.core_entry()["target"].upper().replace("SRC/MATTERGRAM/", "src/mattergram/")
        )
        self.manifest["entries"][2]["target"] = (
            self.manifest["entries"][2]["target"].removesuffix(".PY") + ".py"
        )
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            extract(self.root, self.output, self.manifest, self.lock)

    def test_missing_dependency_is_not_implicitly_added_to_allowlist(self) -> None:
        self.manifest["entries"] = [
            entry for entry in self.manifest["entries"] if entry["module"] != "aiogram.helper"
        ]
        extract(self.root, self.output, self.manifest, self.lock)
        report = audit(self.root, self.output, self.manifest, self.lock)
        self.assertFalse(report["ready"])
        self.assertTrue(
            any(
                item["kind"] == "missing_dependency" and item["detail"] == "mattergram.helper"
                for item in report["findings"]
            )
        )
        self.assertFalse((self.output / "src/mattergram/helper.py").exists())

    def test_pending_adaptations_block_even_when_imports_resolve(self) -> None:
        self.core_entry()["action"] = "adapt"
        extract(self.root, self.output, self.manifest, self.lock)
        self.assertIn(
            "pending_adaptation",
            {item["kind"] for item in audit(self.root, self.output, self.manifest, self.lock)["findings"]},
        )

    def test_pinned_semantic_adaptation_replays_and_changes_observed_behavior(self) -> None:
        self.add_patch()
        receipt = extract(self.root, self.output, self.manifest, self.lock)
        self.assertEqual(receipt["pending_adaptations"], [])
        self.assertTrue(audit(self.root, self.output, self.manifest, self.lock)["ready"])
        self.execute_fixture(self.output, 9)
        self.assertEqual(extract(self.root, self.output, self.manifest, self.lock), receipt)
        self.assertTrue((self.output / "LICENSES/aiogram/LICENSE").is_file())
        self.assertEqual(read_json(self.output / "receipt.json"), receipt)

    def test_patch_tampering_and_incorrect_pre_post_hashes_are_rejected(self) -> None:
        path = self.add_patch()
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        with self.assertRaisesRegex(ValueError, "file hash differs"):
            extract(self.root, self.output, self.manifest, self.lock)
        for key in ("input_sha256", "output_sha256"):
            patch = read_json(path)
            patch[key] = "0" * 64
            path.write_bytes(json_bytes(patch))
            self.core_entry()["adaptation"]["patches"][0]["sha256"] = sha256(path.read_bytes())
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "hash differs"):
                extract(self.root, self.output, self.manifest, self.lock)
            path.write_bytes(original)

    def test_applied_flag_without_patch_cannot_bypass_audit(self) -> None:
        entry = self.core_entry()
        entry["action"] = "adapt"
        entry["adaptation"] = {"status": "applied", "patches": []}
        with self.assertRaisesRegex(ValueError, "no pinned patch"):
            extract(self.root, self.output, self.manifest, self.lock)

    def test_incorrect_patch_occurrence_count_and_invalid_output_are_rejected(self) -> None:
        path = self.add_patch()
        original = read_json(path)
        for mode in ("count", "syntax"):
            patch = {**original, "replacements": [dict(original["replacements"][0])]}
            if mode == "count":
                patch["replacements"][0]["occurrences"] = 2
            else:
                patch["replacements"][0]["after"] = "return ("
                before = rewrite_namespace((self.snapshot / self.core_entry()["source_path"]).read_bytes())
                patch["output_sha256"] = sha256(before.replace(b"return value + VALUE", b"return ("))
            write_json(path, patch)
            self.core_entry()["adaptation"]["patches"][0]["sha256"] = sha256(path.read_bytes())
            with self.subTest(mode=mode), self.assertRaises((ValueError, SyntaxError)):
                extract(self.root, self.output, self.manifest, self.lock)
            self.assertFalse(self.output.exists())

    def test_partial_patch_still_requires_review(self) -> None:
        self.add_patch(status="pending")
        extract(self.root, self.output, self.manifest, self.lock)
        self.assertFalse(audit(self.root, self.output, self.manifest, self.lock)["ready"])

    def test_new_unreviewed_files_make_stage_invalid(self) -> None:
        extract(self.root, self.output, self.manifest, self.lock)
        (self.output / "src/mattergram/surprise.py").write_bytes(b"import aiogram\n")
        self.assertEqual(
            audit(self.root, self.output, self.manifest, self.lock)["findings"][0]["kind"], "integrity"
        )


class AuditTests(unittest.TestCase):
    def test_policy_cannot_disable_forbidden_platform_boundary(self) -> None:
        policy = {**POLICY, "forbidden_import_roots": []}
        with self.assertRaisesRegex(ValueError, "cannot weaken"):
            audit_sources({"src/mattergram/__init__.py": b""}, "mattergram", policy)

    def test_dynamic_code_execution_is_an_unresolved_link(self) -> None:
        files = {"src/mattergram/__init__.py": b"exec(source)\n"}
        self.assertIn(
            "unresolved_link", {item["kind"] for item in audit_sources(files, "mattergram", POLICY)}
        )

    def test_forbidden_imports_exports_guarded_links_and_dynamic_imports(self) -> None:
        files = {
            "src/mattergram/__init__.py": b"""from typing import TYPE_CHECKING
import aiogram as donor
from importlib import import_module as load
if TYPE_CHECKING:
    from mattergram.missing import Item
class TelegramObject: pass
load('plugins.' + selected)
from mattergram.core import *
"""
        }
        kinds = {item["kind"] for item in audit_sources(files, "mattergram", POLICY)}
        self.assertTrue(
            {"forbidden_import", "forbidden_export", "missing_dependency", "unresolved_link"}.issubset(kinds)
        )

    def test_missing_attribute_undeclared_external_and_invalid_python(self) -> None:
        files = {
            "src/mattergram/__init__.py": b"from .helper import DoesNotExist\nimport unknown_client\n",
            "src/mattergram/helper.py": b"def actual(): pass\n",
            "src/mattergram/invalid.py": b"def broken(\n",
        }
        kinds = {item["kind"] for item in audit_sources(files, "mattergram", POLICY)}
        self.assertTrue(
            {"missing_export", "undeclared_external_dependency", "invalid_python"}.issubset(kinds)
        )

    def test_function_locals_do_not_satisfy_module_exports(self) -> None:
        files = {
            "src/mattergram/__init__.py": b"from .helper import hidden\n",
            "src/mattergram/helper.py": b"def function():\n    hidden = 1\n",
        }
        self.assertIn("missing_export", {item["kind"] for item in audit_sources(files, "mattergram", POLICY)})

    def test_dynamic_exports_and_unknown_inventory_links_are_blocked(self) -> None:
        files = {
            "src/mattergram/__init__.py": b"def __getattr__(name): return object()\n__all__ = discover()\n"
        }
        self.assertIn(
            "unresolved_link", {item["kind"] for item in audit_sources(files, "mattergram", POLICY)}
        )


if __name__ == "__main__":
    unittest.main()
