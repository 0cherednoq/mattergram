"""Enrich the reviewed port plan with pinned source hashes and dependency evidence."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from tools._common import ROOT, read_json, safe_path, sha256, verify_tree, write_json
from tools.inventory_upstream import dependency_paths


def build_manifest(
    root: Path, report: dict[str, Any], plan: dict[str, Any], lock: dict[str, Any]
) -> dict[str, Any]:
    source = next(source for source in lock["sources"] if source["id"] == plan["source"])
    if (
        report["source"]["commit"] != source["commit"]
        or report["source"]["tree_sha256"] != source["tree_sha256"]
    ):
        raise ValueError("Inventory provenance differs from lock")
    snapshot = safe_path(root, source["snapshot"])
    verify_tree(snapshot, source["files"])
    entries = []
    decisions = {entry["module"]: entry for entry in plan["entries"]}
    if len(decisions) != len(plan["entries"]):
        raise ValueError("Duplicate module decision")
    targets: set[str] = set()
    selected_source_paths = {
        report["modules"][entry["module"]]["path"]
        for entry in plan["entries"]
        if entry["action"] != "exclude"
    }
    for scope in plan["excluded_scopes"]:
        prefix = scope.get("prefix")
        exceptions = scope.get("exceptions", [])
        if not isinstance(prefix, str) or not prefix or not isinstance(exceptions, list):
            raise ValueError("Excluded scopes need a prefix and optional exception list")
        if any(
            not isinstance(path, str) or not path.startswith(prefix) or path not in selected_source_paths
            for path in exceptions
        ):
            raise ValueError("Excluded-scope exceptions must be selected pinned source paths")
    for decision in plan["entries"]:
        if decision["action"] not in {"copy", "adapt", "generate", "implement", "exclude", "unresolved"}:
            raise ValueError(f"Unknown manifest action: {decision['action']}")
        if not decision.get("reason"):
            raise ValueError("Each decision needs a reason")
        module = decision["module"]
        info = report["modules"][module]
        if info["sha256"] != source["files"][info["path"]]["sha256"]:
            raise ValueError(f"Inventory hash differs from lock: {module}")
        source_prefixes = (source["namespace"] + "/", "src/" + source["namespace"] + "/")
        source_path = info["path"]
        relative = next(
            (
                source_path.removeprefix(prefix)
                for prefix in source_prefixes
                if source_path.startswith(prefix)
            ),
            None,
        )
        if relative is None:
            raise ValueError(f"Selected module is outside the source namespace: {module}")
        target = decision.get("target", f"src/{plan['target_namespace']}/{relative}")
        safe_path(root, target)
        if target.casefold() in targets:
            raise ValueError(f"Duplicate target: {target}")
        targets.add(target.casefold())
        dependencies = []
        for dependency in info["local_dependencies"]:
            dependency_decision = decisions.get(dependency)
            dependencies.append(
                {
                    "module": dependency,
                    "decision": dependency_decision["action"] if dependency_decision else "unresolved",
                    "requires_native_boundary": not dependency_decision
                    or dependency_decision["action"] == "exclude",
                }
            )
        entry = {
            **decision,
            "source_id": source["id"],
            "source_commit": source["commit"],
            "source_path": info["path"],
            "source_sha256": info["sha256"],
            "target": target,
            "dependencies": dependencies,
            "related_tests": [
                {"path": name, "sha256": source["files"][name]["sha256"], "status": "requires-review"}
                for name in info["direct_tests"]
            ],
            "special_cases": info["special_cases"],
            "codemods": plan["codemods"] if decision["action"] != "exclude" else [],
            "attribution": [
                {"path": name, "sha256": source["files"][name]["sha256"]} for name in source["licenses"]
            ],
        }
        entries.append(entry)
    for name in plan["test_policy"]["selected_behavioral_suites"]:
        if name not in source["files"]:
            raise ValueError(f"Selected test does not exist: {name}")
    return {
        "schema_version": 1,
        "status": (
            "reviewed-scope-pending-semantic-adaptations"
            if any(
                entry["action"] == "adapt" and entry.get("adaptation", {}).get("status") != "applied"
                for entry in entries
            )
            else "reviewed-scope-semantic-adaptations-recorded"
        ),
        "source": report["source"],
        "inventory_summary": report["summary"],
        "inventory_sha256": sha256((root / f"build/inventory/{plan['source']}.json").read_bytes()),
        "analysis_limits": report["analysis_limits"],
        "target_namespace": plan["target_namespace"],
        "audit_policy": plan["audit_policy"],
        "entries": entries,
        "native": plan["native"],
        "excluded_scopes": plan["excluded_scopes"],
        "test_policy": plan["test_policy"],
        "dependency_evidence": {
            start: {
                "reachable_modules": len(paths),
                "example_telegram_path": next(
                    (path for name, path in paths.items() if name.startswith("aiogram.types.")), None
                ),
            }
            for start in plan.get(
                "dependency_evidence_roots",
                (
                    "aiogram.dispatcher.event.handler",
                    "aiogram.fsm.state",
                    "aiogram.dispatcher.router",
                ),
            )
            for paths in [dependency_paths(report, start)]
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Fail if committed manifest differs from regenerated content"
    )
    parser.add_argument("--plan", type=Path, default=Path("manifests/core-plan.json"))
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--output", type=Path, default=Path("manifests/core.json"))
    args = parser.parse_args()
    plan_path = safe_path(ROOT, args.plan.as_posix())
    plan = read_json(plan_path)
    inventory_path = (
        safe_path(ROOT, args.inventory.as_posix())
        if args.inventory is not None
        else ROOT / f"build/inventory/{plan['source']}.json"
    )
    manifest = build_manifest(
        ROOT,
        read_json(inventory_path),
        plan,
        read_json(ROOT / "upstream.lock.json"),
    )
    path = safe_path(ROOT, args.output.as_posix())
    if args.check:
        if read_json(path) != manifest:
            raise ValueError("Manifest drift: run python -m tools.build_manifest and review the diff")
    else:
        write_json(path, manifest)
    print(f"manifest verified: {len(manifest['entries'])} explicit decisions")


if __name__ == "__main__":
    main()
