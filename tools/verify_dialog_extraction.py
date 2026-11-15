"""Verify the E8-02 dialog replay and clean native-boundary audit."""

from __future__ import annotations

from collections import Counter
from typing import Any

from tools._common import ROOT, read_json, sha256, write_json
from tools.audit_port import audit_sources
from tools.extract_core import json_bytes, render_stage


def verify() -> dict[str, Any]:
    manifest = read_json(ROOT / "manifests/dialog.json")
    lock = read_json(ROOT / "upstream.lock.json")
    tests = read_json(ROOT / manifest["test_policy"]["receipt"])
    source = next(item for item in lock["sources"] if item["id"] == manifest["source"]["id"])
    selected = set(manifest["test_policy"]["selected_behavioral_suites"])
    if {item["path"] for item in tests["sources"]} != selected:
        raise RuntimeError("Dialog behavioral source receipt differs from the reviewed selection")
    for item in tests["sources"]:
        if item["sha256"] != source["files"][item["path"]]["sha256"]:
            raise RuntimeError(f"Dialog behavioral source hash differs: {item['path']}")
    target = tests["target"]
    if sha256((ROOT / target["path"]).read_bytes()) != target["sha256"]:
        raise RuntimeError("Dialog behavioral target hash differs")
    first_outputs, first = render_stage(ROOT, manifest, lock)
    second_outputs, second = render_stage(ROOT, manifest, lock)
    if first != second or first_outputs != second_outputs:
        raise RuntimeError("Dialog extraction is not deterministic")
    actions = Counter(entry["action"] for entry in manifest["entries"])
    if actions != {"adapt": 27, "exclude": 13}:
        raise RuntimeError(f"Dialog selection drift requires review: {dict(actions)}")
    if len(first["files"]) != 27 or first["pending_adaptations"]:
        raise RuntimeError("Dialog selected/applied module count differs")
    findings = audit_sources(first_outputs, manifest["target_namespace"], manifest["audit_policy"])
    kinds = Counter(item["kind"] for item in findings)
    if kinds:
        raise RuntimeError(f"Dialog audit is not ready: {dict(kinds)}")
    return {
        "schema_version": 1,
        "result": "passed-replay-and-audit",
        "source_commit": manifest["source"]["commit"],
        "selected_modules": len(first["files"]),
        "pending_adaptations": 0,
        "explicit_decisions": len(manifest["entries"]),
        "audit_ready": True,
        "expected_audit_findings": 0,
        "findings_by_kind": dict(sorted(kinds.items())),
        "receipt_sha256": sha256(json_bytes(first)),
        "outputs": len(first_outputs),
        "source_written": True,
        "manifest_sha256": sha256((ROOT / "manifests/dialog.json").read_bytes()),
        "inventory_sha256": sha256((ROOT / "build/inventory/aiogram_dialog.json").read_bytes()),
        "behavioral_receipt_sha256": sha256((ROOT / manifest["test_policy"]["receipt"]).read_bytes()),
    }


def main() -> None:
    report = verify()
    path = ROOT / "build/reports/dialog-extraction.json"
    write_json(path, report)
    print(
        f"dialog selection verified: {report['selected_modules']} modules, "
        "all adaptations applied; audit ready"
    )


if __name__ == "__main__":
    main()
