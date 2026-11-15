"""Extract an explicit allowlist into immutable staging, never into maintained src.

python -m tools.extract_core
python -m tools.extract_core --output build/port-staging/review-1
The default stage contains all selected modules. Pending adaptations are retained
in the receipt; only audit_port can determine whether a stage is ready.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from tools._common import ROOT, read_json, safe_path, sha256, tree_digest, verify_tree
from tools._patches import apply_patch
from tools.rewrite_imports import CODEMOD, rewrite_namespace

TOOL_VERSION = 3


def json_bytes(data: Any) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def stage_path(root: Path, output: Path) -> Path:
    """Resolve both sides and reject symlinks that redirect staging outside build."""
    root = root.resolve()
    allowed = safe_path(root, "build/port-staging")
    if allowed.resolve() != root / "build/port-staging":
        raise ValueError("Staging parent must not be a symlink/junction")
    output = output if output.is_absolute() else root / output
    resolved = output.resolve()
    if resolved != allowed and not resolved.is_relative_to(allowed):
        raise ValueError("Extraction output must be inside build/port-staging")
    if output.absolute() != resolved:
        # A lexical '..' path is also rejected to make the write boundary reviewable.
        raise ValueError("Staging output must be a direct path without symlink aliases or traversal")
    return resolved


def render_stage(
    root: Path, manifest: dict[str, Any], lock: dict[str, Any]
) -> tuple[dict[str, bytes], dict[str, Any]]:
    if manifest.get("schema_version") != 1 or lock.get("schema_version") != 1:
        raise ValueError("Unsupported manifest/lock schema")
    namespace = manifest["target_namespace"]
    if not namespace.isidentifier():
        raise ValueError("Invalid target namespace")
    sources = {source["id"]: source for source in lock["sources"]}
    if len(sources) != len(lock["sources"]):
        raise ValueError("Duplicate source ID")
    origin = manifest["source"]
    source = sources[origin["id"]]
    for key in ("commit", "tree_sha256", "repository", "ref"):
        if origin[key] != source[key]:
            raise ValueError(f"Manifest source differs from lock: {key}")
    if tree_digest(source["files"]) != source["tree_sha256"]:
        raise ValueError("Lock source tree digest differs from its files")
    snapshot = safe_path(root, source["snapshot"])
    verify_tree(snapshot, source["files"])
    outputs: dict[str, bytes] = {}
    output_keys: set[str] = set()
    records = []
    adaptations = []
    seen: set[str] = set()
    native_targets = {entry["target"].casefold() for entry in manifest["native"]}

    def add_output(name: str, data: bytes) -> None:
        safe_path(root, name)
        key = name.casefold()
        if key in output_keys:
            raise ValueError(f"Conflicting staging target: {name}")
        # Windows cannot represent a file as the parent of another target.
        if any(key.startswith(existing + "/") or existing.startswith(key + "/") for existing in output_keys):
            raise ValueError(f"File/directory staging collision: {name}")
        output_keys.add(key)
        outputs[name] = data

    for entry in manifest["entries"]:
        module = entry["module"]
        if module in seen:
            raise ValueError(f"Duplicate manifest module: {module}")
        seen.add(module)
        action = entry["action"]
        if action not in {"copy", "adapt", "exclude", "unresolved", "implement", "generate"}:
            raise ValueError(f"Unknown manifest action: {action}")
        if action == "exclude":
            continue
        if action not in {"copy", "adapt"}:
            raise ValueError(f"Source decision is not extractable: {module}: {action}")
        if entry["source_id"] != source["id"] or entry["source_commit"] != source["commit"]:
            raise ValueError(f"Unpinned manifest entry: {module}")
        name = entry["source_path"]
        if any(
            name.startswith(item["prefix"]) and name not in item.get("exceptions", [])
            for item in manifest["excluded_scopes"]
        ):
            raise ValueError(f"Entry lies inside excluded scope: {name}")
        locked = source["files"].get(name)
        if locked is None or entry["source_sha256"] != locked["sha256"]:
            raise ValueError(f"Manifest source hash differs from lock: {name}")
        if not name.endswith(".py"):
            raise ValueError(f"Only explicit Python source entries can be extracted: {name}")
        target = entry["target"]
        if not target.startswith(f"src/{namespace}/") or not target.endswith(".py"):
            raise ValueError(f"Target is outside selected package: {target}")
        if any(
            target.casefold() == native or (native.endswith("/") and target.casefold().startswith(native))
            for native in native_targets
        ):
            raise ValueError(f"Entry would replace a native boundary: {target}")
        if entry["attribution"] != [
            {"path": name, "sha256": source["files"][name]["sha256"]} for name in source["licenses"]
        ]:
            raise ValueError(f"Incomplete source attribution: {module}")
        if entry["codemods"] != [CODEMOD]:
            raise ValueError(f"Unsupported codemod sequence: {module}")
        data = safe_path(snapshot, name).read_bytes()
        rewritten = rewrite_namespace(data, source["namespace"], namespace)
        adaptation = entry.get("adaptation", {"status": "pending", "patches": []})
        patches = adaptation["patches"]
        if action == "copy" and "adaptation" in entry:
            raise ValueError(f"Semantic patches require an adapt decision: {module}")
        if adaptation["status"] not in {"pending", "applied"}:
            raise ValueError(f"Unknown adaptation status: {module}")
        if action == "adapt" and adaptation["status"] == "applied" and not patches:
            raise ValueError(f"Applied adaptation has no pinned patch: {module}")
        applied = []
        for patch in patches:
            if not patch["path"].startswith("manifests/patches/") or not patch["path"].endswith(".json"):
                raise ValueError("Semantic patches must be reviewed JSON files in manifests/patches")
            patch_bytes = safe_path(root, patch["path"]).read_bytes()
            if sha256(patch_bytes) != patch["sha256"]:
                raise ValueError(f"Semantic patch file hash differs: {patch['path']}")
            rewritten = apply_patch(rewritten, json.loads(patch_bytes.decode("utf-8")))
            staged_patch = "PATCHES/" + patch["sha256"] + ".json"
            if staged_patch not in outputs:
                add_output(staged_patch, patch_bytes)
            applied.append(patch)
        add_output(target, rewritten)
        records.append(
            {
                "module": module,
                "target": target,
                "source_path": name,
                "source_sha256": sha256(data),
                "output_sha256": sha256(rewritten),
                "action": action,
                "codemods": entry["codemods"],
                "semantic_patches": applied,
                "related_tests": entry["related_tests"],
            }
        )
        if action == "adapt" and adaptation["status"] != "applied":
            adaptations.append({"module": module, "target": target, "reason": entry["reason"]})
    for name in source["licenses"]:
        add_output(f"LICENSES/{source['id']}/{name}", safe_path(snapshot, name).read_bytes())
    native_records = []
    for entry in manifest["native"]:
        if "source_sha256" not in entry:
            continue
        target = entry["target"]
        if (
            entry["action"] != "implement"
            or not target.startswith(f"src/{namespace}/")
            or not target.endswith((".py", ".typed"))
        ):
            raise ValueError(f"Invalid pinned native boundary: {target}")
        data = safe_path(root, target).read_bytes()
        if sha256(data) != entry["source_sha256"]:
            raise ValueError(f"Native boundary hash differs: {target}")
        add_output(target, data)
        native_records.append({"target": target, "sha256": sha256(data), "reason": entry["reason"]})
    add_output("manifest.json", json_bytes(manifest))
    attribution = {
        "source": origin,
        "licenses": source["licenses"],
        "files": records,
        "note": "Selected files only; namespace rewrite and pinned semantic patches are recorded per file. Behavioral verification is separate; extraction alone does not claim donor tests passed.",
    }
    add_output("attribution.json", json_bytes(attribution))
    receipt = {
        "schema_version": 1,
        "tool_version": TOOL_VERSION,
        "status": "pending-semantic-adaptations" if adaptations else "pending-audit",
        "manifest_sha256": sha256(json_bytes(manifest)),
        "source": origin,
        "codemods": [CODEMOD],
        "files": records,
        "pending_adaptations": adaptations,
        "native_boundaries": manifest["native"],
        "native_files": native_records,
        "outputs": {
            name: {"sha256": sha256(data), "size": len(data)} for name, data in sorted(outputs.items())
        },
    }
    add_output("receipt.json", json_bytes(receipt))
    return outputs, receipt


def verify_stage(output: Path, expected: dict[str, bytes]) -> None:
    files = {name: {"sha256": sha256(data), "size": len(data)} for name, data in expected.items()}
    verify_tree(output, files)


def extract(root: Path, output: Path, manifest: dict[str, Any], lock: dict[str, Any]) -> dict[str, Any]:
    destination = stage_path(root, output)
    outputs, receipt = render_stage(root, manifest, lock)
    if destination.exists():
        verify_stage(destination, outputs)
        return receipt
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".extract-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "stage"
        staged.mkdir()
        for name, data in outputs.items():
            target = safe_path(staged, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        verify_stage(staged, outputs)
        # The destination never exists at rename time; never replace an existing stage.
        staged.rename(destination)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("build/port-staging/core"))
    parser.add_argument("--manifest", type=Path, default=Path("manifests/core.json"))
    args = parser.parse_args()
    receipt = extract(
        ROOT,
        args.output,
        read_json(safe_path(ROOT, args.manifest.as_posix())),
        read_json(ROOT / "upstream.lock.json"),
    )
    print(
        f"staged {len(receipt['files'])} allowlisted modules; {len(receipt['pending_adaptations'])} pending adaptations; audit required"
    )


if __name__ == "__main__":
    main()
