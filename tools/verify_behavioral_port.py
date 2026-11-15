"""Replay pinned test adaptations and check maintained suites without executing donors."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools._common import ROOT, read_json, safe_path, sha256
from tools._patches import apply_patch


def verify(root: Path = ROOT) -> dict[str, Any]:
    manifest = read_json(root / "manifests/ported-tests.json")
    lock = read_json(root / "upstream.lock.json")
    source = next(item for item in lock["sources"] if item["id"] == manifest["source_id"])
    if source["commit"] != manifest["source_commit"]:
        raise ValueError("Behavioral test source commit differs")
    seen: set[str] = set()
    for item in manifest["files"]:
        if item["target"] in seen:
            raise ValueError("Duplicate behavioral test target")
        seen.add(item["target"])
        expected = source["files"][item["source"]]["sha256"]
        data = safe_path(root / source["snapshot"], item["source"]).read_bytes()
        patch_path = safe_path(root, item["patch"])
        if sha256(data) != expected or expected != item["source_sha256"]:
            raise ValueError("Behavioral test donor hash differs")
        if sha256(patch_path.read_bytes()) != item["patch_sha256"]:
            raise ValueError("Behavioral test patch hash differs")
        rendered = apply_patch(data, read_json(patch_path))
        maintained = safe_path(root, item["target"]).read_bytes()
        if rendered != maintained or sha256(maintained) != item["target_sha256"]:
            raise ValueError(f"Behavioral test differs from reviewed replay: {item['target']}")
    return {"suites": len(seen), "commit": source["commit"], "scope": manifest["scope"]}


def main() -> None:
    report = verify()
    print(f"reviewed behavioral port verified: {report['suites']} suites; {report['commit']}")


if __name__ == "__main__":
    main()
