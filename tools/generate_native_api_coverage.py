"""Generate exhaustive operationId support coverage from pinned structural facts."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from tools._common import ROOT, read_json, sha256, write_json
from tools.generate_native_api import OPERATION_ID, _validated_overlay, inventory

FACADE = re.compile(r"[A-Za-z][A-Za-z0-9_]*\.[A-Za-z][A-Za-z0-9_]*")


def _facades(value: Any, *, context: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or FACADE.fullmatch(item) is None for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError(f"{context} requires unique Class.method facade names")
    return sorted(value)


def generate(root: Path) -> dict[str, Any]:
    lock = read_json(root / "upstream.lock.json")
    overlay = read_json(root / "manifests/native-api-overlays.json")
    policy_path = root / "manifests/native-api-coverage.json"
    policy = read_json(policy_path)
    operations, source = inventory(root, lock)
    reviewed = _validated_overlay(overlay, operations, source)
    if policy.get("schema_version") != 1:
        raise ValueError("Unsupported native API coverage schema")
    if policy.get("source_id") != source["id"] or policy.get("source_commit") != source["commit"]:
        raise ValueError("Native API coverage provenance differs from the lock")
    if not isinstance(policy.get("license_policy"), str) or not policy["license_policy"]:
        raise ValueError("Native API coverage requires an explicit license policy")
    generated_ids = {item["operation_id"] for item in reviewed}

    generated_facades_raw = policy.get("generated_facades")
    if not isinstance(generated_facades_raw, dict):
        raise ValueError("generated_facades must be an object")
    generated_facades: dict[str, list[str]] = {}
    for operation_id, names in generated_facades_raw.items():
        if operation_id not in generated_ids:
            raise ValueError(f"Facade mapping is not a generated operation: {operation_id}")
        generated_facades[operation_id] = _facades(names, context=operation_id)

    high_level_raw = policy.get("high_level_only")
    if not isinstance(high_level_raw, list) or not high_level_raw:
        raise ValueError("high_level_only must declare reviewed operations")
    high_level: dict[str, dict[str, Any]] = {}
    for item in high_level_raw:
        if not isinstance(item, dict):
            raise ValueError("high_level_only entries must be objects")
        operation_id = item.get("operation_id")
        if not isinstance(operation_id, str) or OPERATION_ID.fullmatch(operation_id) is None:
            raise ValueError("Invalid high-level operationId")
        if operation_id in generated_ids or operation_id in high_level:
            raise ValueError(f"Duplicate or generated high-level operation: {operation_id}")
        candidates = operations.get(operation_id)
        if candidates is None or len(candidates) != 1:
            raise ValueError(f"High-level operation must select one unique pinned endpoint: {operation_id}")
        candidate = candidates[0]
        if (
            item.get("method") != candidate.method
            or item.get("path") != candidate.path
            or item.get("success_status") not in candidate.success_statuses
            or (item.get("body") != "forbidden") != candidate.has_body
            or item.get("source") != candidate.source
        ):
            raise ValueError(f"High-level facts differ from pinned OpenAPI for {operation_id}")
        evidence = item.get("evidence")
        if not isinstance(evidence, str) or not evidence.startswith(("build/reports/", "docs/reports/")):
            raise ValueError(f"High-level operation needs an evidence path: {operation_id}")
        high_level[operation_id] = {
            "facades": _facades(item.get("facades"), context=operation_id),
            "evidence": evidence,
        }

    for name in ("unsupported_policy", "ambiguous_policy"):
        value = policy.get(name)
        if not isinstance(value, dict) or not all(
            isinstance(value.get(key), str) and value[key]
            for key in ("classification", "reason", "promotion")
        ):
            raise ValueError(f"{name} requires authored classification/reason/promotion")
    unsupported_policy: dict[str, str] = policy["unsupported_policy"]
    ambiguous_policy: dict[str, str] = policy["ambiguous_policy"]

    entries: list[dict[str, Any]] = []
    counts = {
        "generated_low_level": 0,
        "high_level_only": 0,
        ambiguous_policy["classification"]: 0,
        unsupported_policy["classification"]: 0,
    }
    endpoint_counts = {key: 0 for key in counts}
    for operation_id in sorted(operations):
        candidates = operations[operation_id]
        if operation_id in generated_ids:
            status = "generated_low_level"
            surfaces = ["NativeAPI.call"] + generated_facades.get(operation_id, [])
        elif operation_id in high_level:
            status = "high_level_only"
            surfaces = high_level[operation_id]["facades"]
        elif len(candidates) > 1:
            status = ambiguous_policy["classification"]
            surfaces = []
        else:
            status = unsupported_policy["classification"]
            surfaces = []
        counts[status] += 1
        endpoint_counts[status] += len(candidates)
        entry: dict[str, Any] = {
            "operation_id": operation_id,
            "classification": status,
            "endpoint_count": len(candidates),
        }
        if surfaces:
            entry["surfaces"] = surfaces
        if operation_id in high_level:
            entry["evidence"] = high_level[operation_id]["evidence"]
        entries.append(entry)

    if sum(counts.values()) != len(operations) or sum(endpoint_counts.values()) != sum(
        len(items) for items in operations.values()
    ):
        raise RuntimeError("Coverage classifications are not exhaustive")
    return {
        "schema_version": 1,
        "status": "exhaustive-operationId-classification",
        "source": {
            "id": source["id"],
            "repository": source["repository"],
            "commit": source["commit"],
            "tree_sha256": source["tree_sha256"],
            "license_review": source["license_review"],
        },
        "license_policy": policy["license_policy"],
        "coverage_manifest_sha256": sha256(policy_path.read_bytes()),
        "overlay_sha256": sha256((root / "manifests/native-api-overlays.json").read_bytes()),
        "operation_ids": len(operations),
        "endpoints": sum(len(items) for items in operations.values()),
        "counts": counts,
        "endpoint_counts": endpoint_counts,
        "supported_operation_ids": counts["generated_low_level"] + counts["high_level_only"],
        "unsupported_policy": unsupported_policy,
        "ambiguous_policy": ambiguous_policy,
        "coverage": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail when the committed report differs")
    args = parser.parse_args()
    report = generate(ROOT)
    target = ROOT / "docs/reports/native-api-coverage.json"
    if args.check:
        if not target.exists() or read_json(target) != report:
            raise ValueError("Native API coverage drift; regenerate and review")
    else:
        write_json(target, report)
    print(
        f"native API coverage verified: {report['operation_ids']} operationIds, "
        f"{report['supported_operation_ids']} supported"
    )


if __name__ == "__main__":
    main()
