"""Verify repeatability on the actual donor scope and unchanged maintained source.

This command proves extraction invariants only. It records a strict audit result
separately: a pending core stays BLOCKED and is never presented as a ready SDK.
"""

from __future__ import annotations

from pathlib import Path

from tools._common import ROOT, read_json, sha256, tree_digest, write_json
from tools.audit_port import audit
from tools.extract_core import TOOL_VERSION, extract, json_bytes
from tools.fetch_upstream import describe_files


def main() -> None:
    manifest = read_json(ROOT / "manifests/core.json")
    lock = read_json(ROOT / "upstream.lock.json")

    def maintained_files() -> dict[str, dict[str, object]]:
        return {
            name: info
            for name, info in describe_files(ROOT / "src").items()
            if "__pycache__" not in Path(name).parts and not name.endswith((".pyc", ".pyo"))
        }

    before = maintained_files()
    fingerprint = sha256(json_bytes({"manifest": manifest, "extraction_version": TOOL_VERSION}))[:16]
    first = Path(f"build/port-staging/repeat-{fingerprint}-a")
    second = Path(f"build/port-staging/repeat-{fingerprint}-b")
    receipt = extract(ROOT, first, manifest, lock)
    repeated = extract(ROOT, first, manifest, lock)
    independent = extract(ROOT, second, manifest, lock)
    if receipt != repeated or receipt != independent:
        raise ValueError("Extraction receipt is not repeatable")
    files = describe_files(ROOT / first)
    if files != describe_files(ROOT / second):
        raise ValueError("Independent staging bytes differ")
    after = maintained_files()
    if before != after:
        raise ValueError("Extraction changed maintained source")
    report = {
        "scope": "E1 extraction invariants; does not approve semantic adaptations or SDK integration",
        "result": "passed",
        "modules": len(receipt["files"]),
        "repeat_runs": 3,
        "independent_stages": [first.as_posix(), second.as_posix()],
        "staging_tree_sha256": tree_digest(files),
        "receipt_sha256": sha256((ROOT / first / "receipt.json").read_bytes()),
        "maintained_source_sha256": tree_digest(before),
        "maintained_source_unchanged": True,
    }
    gate = audit(ROOT, first, manifest, lock)
    write_json(ROOT / "build/reports/extraction.json", report)
    write_json(ROOT / "build/reports/port-audit.json", gate)
    print(
        f"repeatability verified: {report['modules']} modules, maintained source unchanged; SDK audit: {'ready' if gate['ready'] else 'BLOCKED'}"
    )


if __name__ == "__main__":
    main()
