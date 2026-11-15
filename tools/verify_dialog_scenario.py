"""Verify the pinned E8-03 behavioral/live scenario evidence."""

from __future__ import annotations

import argparse
from typing import Any

from tools._common import ROOT, read_json, sha256


def verify(*, require_live: bool = False) -> dict[str, Any]:
    receipt = read_json(ROOT / "manifests/dialog-scenario-tests.json")
    for section, path_key, hash_key in (
        ("local_integration", "path", "sha256"),
        ("live_harness", "path", "sha256"),
        ("runtime", "path", "sha256"),
        ("runtime", "binding_path", "binding_sha256"),
    ):
        item = receipt[section]
        path = ROOT / item[path_key]
        if sha256(path.read_bytes()) != item[hash_key]:
            raise RuntimeError(f"E8-03 receipt hash differs: {item[path_key]}")
    report_path = ROOT / receipt["live_harness"]["private_report"]
    live_verified = report_path.exists()
    if live_verified and sha256(report_path.read_bytes()) != receipt["live_harness"]["private_report_sha256"]:
        raise RuntimeError("E8-03 private live report hash differs")
    if require_live and not live_verified:
        raise RuntimeError("E8-03 live stand report is required but absent")
    public = read_json(ROOT / "docs/reports/dialog-scenario.json")
    if public["result"] != "passed" or public["receipt"] != "manifests/dialog-scenario-tests.json":
        raise RuntimeError("E8-03 public evidence differs from the receipt")
    return {
        "schema_version": 1,
        "result": "passed",
        "local_cases": len(receipt["local_integration"]["cases"]),
        "live_report_verified": live_verified,
        "live_server": receipt["live_profile"]["server"],
        "real_redis": receipt["live_profile"]["storage"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-live", action="store_true")
    args = parser.parse_args()
    result = verify(require_live=args.require_live)
    print(
        f"E8-03 evidence verified: {result['local_cases']} local cases; live={result['live_report_verified']}"
    )


if __name__ == "__main__":
    main()
