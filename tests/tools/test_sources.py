from __future__ import annotations

import io
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools._common import safe_path, sha256, tree_digest, verify_tree, write_json
from tools.fetch_upstream import describe_files, run, unpack_archive
from tools.inventory_upstream import inventory


class SnapshotTests(unittest.TestCase):
    def test_json_output_uses_platform_independent_lf_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.json"
            write_json(path, {"line": [1, 2]})
            self.assertNotIn(b"\r", path.read_bytes())
            self.assertTrue(path.read_bytes().endswith(b"\n"))

    def test_archive_rejects_traversal_and_symlinks(self) -> None:
        for filename in ("root/../outside.py", "root/C:/outside.py", "root/a\\outside.py"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temporary:
                data = io.BytesIO()
                with zipfile.ZipFile(data, "w") as archive:
                    info = zipfile.ZipInfo()
                    info.filename = filename  # Preserve the raw archive path on Windows.
                    archive.writestr(info, b"bad")
                with self.assertRaises(ValueError):
                    unpack_archive(data.getvalue(), Path(temporary))
        with tempfile.TemporaryDirectory() as temporary:
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w") as archive:
                info = zipfile.ZipInfo("root/link")
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, b"/outside")
            with self.assertRaisesRegex(ValueError, "Symlink"):
                unpack_archive(data.getvalue(), Path(temporary))

    def test_file_hash_and_file_set_tampering_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file = root / "module.py"
            file.write_bytes(b"original")
            files = describe_files(root)
            verify_tree(root, files)
            file.write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "mismatch"):
                verify_tree(root, files)
            file.write_bytes(b"original")
            (root / "surprise.py").write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "file set differs"):
                verify_tree(root, files)

    def test_lock_restore_and_read_only_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = {
                "id": "donor",
                "commit": "a" * 40,
                "kind": "files",
                "repository": "https://github.com/example/donor",
                "ref": "test",
                "licenses": ["LICENSE"],
                "paths": ["LICENSE", "pkg/__init__.py"],
            }
            write_json(root / "manifests/upstream-sources.json", {"sources": [source]})
            with patch("tools.fetch_upstream.download", return_value=b"# fixture\n") as fetch:
                lock = run(root, create_lock=True)
                self.assertEqual(fetch.call_count, 2)
            with patch(
                "tools.fetch_upstream.download", side_effect=AssertionError("verification accessed network")
            ):
                run(root, verify_only=True)
            with self.assertRaisesRegex(ValueError, "already exists"):
                run(root, create_lock=True)
            snapshot = root / lock["sources"][0]["snapshot"]
            self.assertEqual((snapshot / "LICENSE").read_bytes(), b"# fixture\n")
            import shutil

            shutil.rmtree(snapshot)
            with patch("tools.fetch_upstream.download", return_value=b"# fixture\n"):
                run(root)
            with patch("tools.fetch_upstream.download", return_value=b"altered"):
                shutil.rmtree(snapshot)
                with self.assertRaisesRegex(ValueError, "mismatch"):
                    run(root)
            self.assertFalse(snapshot.exists())

    def test_failed_lock_creation_leaves_no_partial_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = {
                "id": "donor",
                "commit": "a" * 40,
                "kind": "files",
                "repository": "https://github.com/example/donor",
                "licenses": ["LICENSE"],
                "paths": ["LICENSE"],
            }
            write_json(
                root / "manifests/upstream-sources.json", {"sources": [source, {**source, "id": "second"}]}
            )
            with patch("tools.fetch_upstream.download", side_effect=[b"license", OSError("network failed")]):
                with self.assertRaises(OSError):
                    run(root, create_lock=True)
            self.assertFalse((root / "upstream.lock.json").exists())
            self.assertFalse((root / "upstream/snapshots").exists())

    def test_inventory_is_deterministic_read_only_and_links_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = {
                "pkg/__init__.py": b"from .core import Item\n",
                "pkg/core.py": b"class Item: pass\n",
                "tests/test_core.py": b"from pkg.core import Item\n",
                "tests/conftest.py": b"",
                "LICENSE": b"fixture license",
            }
            for name, data in paths.items():
                file = safe_path(root, name)
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(data)
            files = describe_files(root)
            source = {
                "id": "donor",
                "namespace": "pkg",
                "repository": "fixture",
                "ref": "fixture",
                "commit": "b" * 40,
                "tree_sha256": tree_digest(files),
                "files": files,
                "licenses": ["LICENSE"],
            }
            first = inventory(root, source)
            second = inventory(root, source)
            self.assertEqual(first, second)
            self.assertEqual(first["modules"]["pkg.core"]["direct_tests"], ["tests/test_core.py"])
            self.assertEqual(describe_files(root), files)
            self.assertEqual(sha256(paths["pkg/core.py"]), first["modules"]["pkg.core"]["sha256"])


if __name__ == "__main__":
    unittest.main()
