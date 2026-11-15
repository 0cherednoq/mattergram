"""Verify/audit then copy explicitly ported files once; never overwrite hand edits.

Future port updates require reviewed changes to existing source, followed by an
updated pinned patch/manifest. --check is read-only and is used by CI.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tools._common import ROOT, read_json, safe_path, sha256, write_json
from tools.audit_port import audit
from tools.extract_core import TOOL_VERSION, extract, json_bytes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    manifest = read_json(ROOT / "manifests/core.json")
    lock = read_json(ROOT / "upstream.lock.json")
    digest = sha256(json_bytes(manifest))[:16]
    stage = Path(f"build/port-staging/promote-v{TOOL_VERSION}-{digest}")
    receipt = extract(ROOT, stage, manifest, lock)
    report = audit(ROOT, stage, manifest, lock)
    write_json(ROOT / "build/reports/port-audit.json", report)
    if not report["ready"]:
        raise ValueError(f"Promotion blocked by {len(report['findings'])} audit findings")
    pending = []
    for entry in receipt["files"]:
        target = safe_path(ROOT, entry["target"])
        data = safe_path(ROOT / stage, entry["target"]).read_bytes()
        if target.exists():
            if target.read_bytes() != data:
                raise ValueError(f"Maintained port differs from reviewed replay: {entry['target']}")
        elif args.check:
            raise ValueError(f"Missing maintained port: {entry['target']}")
        else:
            pending.append((target, data))
    for target, data in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
    write_json(
        ROOT / "build/reports/promoted-core.json",
        {
            "scope": "Explicit allowlisted source matches reviewed replay; static audit ready, behavior checked separately",
            "manifest_sha256": sha256(json_bytes(manifest)),
            "files": receipt["files"],
        },
    )
    print(f"maintained port verified: {len(receipt['files'])} modules, no overwrite; static audit ready")


if __name__ == "__main__":
    main()
