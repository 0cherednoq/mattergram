from __future__ import annotations

import json
import re
import unittest

from tools._common import ROOT
from tools.generate_documentation import generate

LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
ADDRESS = re.compile(r"0x[0-9A-Fa-f]{6,}")


class DocumentationGenerationTests(unittest.TestCase):
    def test_generated_reference_is_current_and_has_no_process_addresses(self) -> None:
        outputs, report = generate(ROOT)

        for relative, content in outputs.items():
            self.assertEqual(content, (ROOT / relative).read_text(encoding="utf-8"), relative)
            self.assertIsNone(ADDRESS.search(content), relative)
        self.assertEqual(report["public_modules"], 20)
        self.assertEqual(report["public_exports"], 255)
        self.assertEqual(report["public_members"], 429)
        self.assertEqual(report["guides"], 28)
        self.assertEqual(report["examples"], 19)
        self.assertEqual(report["network_access"], "none")
        self.assertEqual(report["mutating_operations"], "none")

    def test_public_api_contains_implemented_exports_and_sources(self) -> None:
        api = json.loads((ROOT / "docs/reference/public-api.json").read_text(encoding="utf-8"))
        modules = {module["name"]: module for module in api["modules"]}

        self.assertIn("mattergram", modules)
        self.assertIn("mattergram.ui.forms", modules)
        self.assertIn("mattergram_dialog", modules)
        root_exports = {item["name"] for item in modules["mattergram"]["exports"]}
        self.assertEqual(
            root_exports,
            {
                "Bot",
                "ComposerButton",
                "ComposerKeyboard",
                "ComposerKeyboardState",
                "Dispatcher",
                "F",
                "NativeAPI",
                "Router",
                "__version__",
            },
        )
        for module in modules.values():
            for export in module["exports"]:
                source = export["source"]
                self.assertTrue((ROOT / source["path"]).is_file())
                self.assertGreaterEqual(source["line"], 1)

    def test_registered_markdown_links_resolve_locally(self) -> None:
        manifest = json.loads((ROOT / "manifests/documentation.json").read_text(encoding="utf-8"))
        documents = [
            ROOT / "README.md",
            ROOT / "docs/reference/api.md",
            ROOT / "docs/reference/exceptions.md",
            ROOT / "docs/reference/capabilities.md",
            ROOT / "docs/examples.md",
            ROOT / "llms.txt",
        ]
        documents.extend(ROOT / guide["path"] for guide in manifest["guides"])
        for document in documents:
            for destination in LINK.findall(document.read_text(encoding="utf-8")):
                if "://" in destination:
                    continue
                relative = destination.split("#", 1)[0]
                if not relative:
                    continue
                target = (document.parent / relative).resolve()
                self.assertTrue(target.is_file(), f"{document}: {destination}")


if __name__ == "__main__":
    unittest.main()
