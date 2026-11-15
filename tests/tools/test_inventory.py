from __future__ import annotations

import unittest

from tools.inventory_upstream import (
    dependency_paths,
    inspect_python,
    module_name,
    resolve_import,
    resolve_relative,
)


class InventoryTests(unittest.TestCase):
    def test_src_and_flat_module_layouts(self) -> None:
        self.assertEqual(module_name("src/pkg/sub/__init__.py"), "pkg.sub")
        self.assertEqual(module_name("pkg/sub.py"), "pkg.sub")

    def test_relative_import_levels_and_reexports(self) -> None:
        self.assertEqual(resolve_relative("pkg.sub.module", False, 1, "base"), "pkg.sub.base")
        self.assertEqual(resolve_relative("pkg.sub", True, 2, "base"), "pkg.base")
        self.assertIsNone(resolve_relative("pkg", True, 2, "base"))
        entry = inspect_python(
            b"from .base import Handler as Public\n__all__ = ('Public',)\n", "pkg.sub", True
        )
        self.assertEqual(entry["exports"], {"mode": "explicit", "names": ["Public"]})
        targets, error = resolve_import(entry["imports"][0], {"pkg.sub.base"}, "pkg")
        self.assertEqual(targets, ["pkg.sub.base"])
        self.assertIsNone(error)

    def test_guards_and_dynamic_aliases_are_visible(self) -> None:
        entry = inspect_python(
            b"""from typing import TYPE_CHECKING
from importlib import import_module as load
import importlib as loader
if TYPE_CHECKING:
    from pkg.model import Event
else:
    from .base import Handler
try:
    import optional_client
except ImportError:
    pass
load('pkg.plugins')
loader.import_module(variable)
from pkg.base import *
""",
            "pkg.sub.module",
            False,
        )
        guarded = next(item for item in entry["imports"] if item["name"] == "Event")
        self.assertEqual(guarded["guards"], ["TYPE_CHECKING"])
        fallback = next(item for item in entry["imports"] if item["name"] == "Handler")
        self.assertEqual(fallback["guards"], ["not (TYPE_CHECKING)"])
        optional = next(item for item in entry["imports"] if item["module"] == "optional_client")
        self.assertEqual(optional["guards"], ["try (may be optional)"])
        kinds = [case["kind"] for case in entry["special_cases"]]
        self.assertEqual(kinds.count("dynamic_import"), 2)
        self.assertIn("star_import", kinds)

    def test_donor_code_is_not_executed(self) -> None:
        entry = inspect_python(b"raise RuntimeError('must never execute')\nclass Item: pass\n", "pkg", True)
        self.assertEqual(entry["symbols"][0]["name"], "Item")

    def test_unresolved_exports_never_look_complete(self) -> None:
        for content in (
            b"__all__ = choose_exports()",
            b"__all__ = ['A']\n__all__.append('B')",
            b"if flag:\n    __all__ = ['A']",
        ):
            with self.subTest(content=content):
                entry = inspect_python(content, "pkg", True)
                self.assertEqual(entry["exports"]["mode"], "unresolved")
                self.assertIn("unresolved_exports", [case["kind"] for case in entry["special_cases"]])

    def test_submodule_and_package_edges_both_recorded(self) -> None:
        item = {"module": "pkg", "name": "child"}
        targets, _ = resolve_import(item, {"pkg", "pkg.child"}, "pkg")
        self.assertEqual(targets, ["pkg", "pkg.child"])
        _, error = resolve_import({"module": "pkg.missing", "name": "X"}, {"pkg"}, "pkg")
        self.assertIn("missing local module", error)

    def test_dependency_paths_handle_cycles(self) -> None:
        report = {
            "modules": {
                "a": {"local_dependencies": ["b"]},
                "b": {"local_dependencies": ["a", "c"]},
                "c": {"local_dependencies": []},
            }
        }
        self.assertEqual(dependency_paths(report, "a")["c"], ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
