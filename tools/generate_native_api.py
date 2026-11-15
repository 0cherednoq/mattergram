"""Generate the reviewed low-level operation registry from an authored overlay.

The pinned Mattermost OpenAPI documentation is used only to verify structural
facts in the overlay. Descriptions and schemas are never copied into runtime
files. Run from the repository root with ``python -m tools.generate_native_api``.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools._common import ROOT, read_json, safe_path, sha256

PATH_LINE = re.compile(r'^\s{1,3}(?P<key>"?/api/v4/[^":]+(?:\{[^}]+\}[^":]*)*"?):\s*$')
METHOD_LINE = re.compile(r"^(?P<indent>\s+)(?P<method>delete|get|head|options|patch|post|put):\s*$")
OPERATION_LINE = re.compile(r"^\s+operationId:\s*(?P<operation>[A-Za-z][A-Za-z0-9_]*)\s*$")
RESPONSE_LINE = re.compile(r"^\s+['\"]?(?P<status>[1-5][0-9]{2})['\"]?:\s*$")
OPERATION_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
PATH_TEMPLATE = re.compile(r"/api/v4(?:/[A-Za-z0-9._~{}-]+)*")


@dataclass(frozen=True, slots=True)
class SourceOperation:
    operation_id: str
    method: str
    path: str
    success_statuses: tuple[int, ...]
    has_body: bool
    source: str


@dataclass(slots=True)
class _OperationBuilder:
    method: str
    path: str
    source: str
    operation_id: str | None = None
    responses_indent: int | None = None
    success_statuses: set[int] | None = None
    has_body: bool = False

    def __post_init__(self) -> None:
        self.success_statuses = set()

    def finish(self) -> SourceOperation | None:
        if self.operation_id is None:
            return None
        return SourceOperation(
            self.operation_id,
            self.method.upper(),
            self.path,
            tuple(sorted(self.success_statuses or set())),
            self.has_body,
            self.source,
        )


def _unquote_path(value: str) -> str:
    return value[1:-1] if value.startswith('"') and value.endswith('"') else value


def scan_source(path: Path, source_name: str) -> list[SourceOperation]:
    """Extract only endpoint facts; tolerate the pinned source's inconsistent indentation."""
    operations: list[SourceOperation] = []
    current_path: str | None = None
    path_indent = 0
    builder: _OperationBuilder | None = None

    def finish() -> None:
        nonlocal builder
        if builder is not None:
            operation = builder.finish()
            if operation is not None:
                operations.append(operation)
        builder = None

    for line in path.read_text(encoding="utf-8").splitlines():
        path_match = PATH_LINE.fullmatch(line)
        if path_match is not None:
            finish()
            current_path = _unquote_path(path_match.group("key"))
            path_indent = len(line) - len(line.lstrip())
            continue
        method_match = METHOD_LINE.fullmatch(line)
        if method_match is not None and current_path is not None:
            indent = len(method_match.group("indent"))
            if path_indent < indent <= path_indent + 3:
                finish()
                builder = _OperationBuilder(
                    method=method_match.group("method"), path=current_path, source=source_name
                )
                continue
        if builder is None:
            continue
        indent = len(line) - len(line.lstrip())
        operation_match = OPERATION_LINE.fullmatch(line)
        if operation_match is not None:
            if builder.operation_id is not None:
                raise ValueError(f"Duplicate operationId in one method: {source_name}:{builder.path}")
            builder.operation_id = operation_match.group("operation")
            continue
        if line.strip() == "requestBody:":
            builder.has_body = True
        if line.strip() == "responses:":
            builder.responses_indent = indent
            continue
        if builder.responses_indent is not None:
            if line.strip() and indent <= builder.responses_indent:
                builder.responses_indent = None
            else:
                response_match = RESPONSE_LINE.fullmatch(line)
                if response_match is not None and indent == builder.responses_indent + 2:
                    status = int(response_match.group("status"))
                    if 200 <= status < 300:
                        assert builder.success_statuses is not None
                        builder.success_statuses.add(status)
    finish()
    return operations


def inventory(root: Path, lock: dict[str, Any]) -> tuple[dict[str, list[SourceOperation]], dict[str, Any]]:
    source = next((item for item in lock["sources"] if item["id"] == "mattermost"), None)
    if not isinstance(source, dict):
        raise ValueError("Pinned Mattermost source is absent")
    snapshot = safe_path(root, source["snapshot"])
    operations: dict[str, list[SourceOperation]] = {}
    source_files = {
        name: info
        for name, info in source["files"].items()
        if name.startswith("api/v4/source/")
        and name.endswith(".yaml")
        and name != "api/v4/source/definitions.yaml"
    }
    for name in sorted(source_files):
        file = safe_path(snapshot, name)
        info = source_files[name]
        data = file.read_bytes()
        if len(data) != info["size"] or sha256(data) != info["sha256"]:
            raise ValueError(f"Pinned OpenAPI source mismatch: {name}")
        for operation in scan_source(file, name):
            operations.setdefault(operation.operation_id, []).append(operation)
    return operations, source


def _validated_overlay(
    overlay: dict[str, Any], operations: dict[str, list[SourceOperation]], source: dict[str, Any]
) -> list[dict[str, Any]]:
    if overlay.get("schema_version") != 1:
        raise ValueError("Unsupported native API overlay schema")
    if overlay.get("source_id") != source["id"] or overlay.get("source_commit") != source["commit"]:
        raise ValueError("Native API overlay provenance differs from the lock")
    if not isinstance(overlay.get("license_policy"), str) or not overlay["license_policy"]:
        raise ValueError("Native API overlay requires an explicit license policy")
    entries = overlay.get("operations")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Native API overlay must declare operations")
    reviewed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in entries:
        if not isinstance(raw, dict):
            raise ValueError("Native API overlay entries must be objects")
        operation_id = raw.get("operation_id")
        if not isinstance(operation_id, str) or OPERATION_ID.fullmatch(operation_id) is None:
            raise ValueError("Invalid operation_id in native API overlay")
        if operation_id in seen:
            raise ValueError(f"Duplicate native API overlay operation: {operation_id}")
        seen.add(operation_id)
        candidates = operations.get(operation_id)
        if candidates is None:
            raise ValueError(f"Unknown pinned operationId: {operation_id}")
        method, path, status, body, declared_source = (
            raw.get("method"),
            raw.get("path"),
            raw.get("success_status"),
            raw.get("body"),
            raw.get("source"),
        )
        matching = [
            operation
            for operation in candidates
            if method == operation.method
            and path == operation.path
            and status in operation.success_statuses
            and declared_source == operation.source
        ]
        if len(matching) != 1:
            raise ValueError(f"Overlay facts differ from pinned OpenAPI for {operation_id}")
        operation = matching[0]
        overrides = raw.get("success_status_overrides", [])
        if not isinstance(overrides, list):
            raise ValueError(f"Status overrides must be a list for {operation_id}")
        success_statuses = {status}
        for override in overrides:
            if not isinstance(override, dict):
                raise ValueError(f"Status overrides must be objects for {operation_id}")
            override_status = override.get("status")
            evidence = override.get("source")
            reason = override.get("reason")
            if (
                not isinstance(override_status, int)
                or isinstance(override_status, bool)
                or not 200 <= override_status < 300
                or override_status in operation.success_statuses
                or override_status in success_statuses
                or not isinstance(evidence, str)
                or not evidence.startswith(("build/reports/", "docs/reports/"))
                or not isinstance(reason, str)
                or not reason
            ):
                raise ValueError(f"Invalid explicit status override for {operation_id}")
            success_statuses.add(override_status)
        if not isinstance(path, str) or PATH_TEMPLATE.fullmatch(path) is None:
            raise ValueError(f"Unsafe native path template: {path!r}")
        if body not in {"forbidden", "optional", "required"}:
            raise ValueError(f"Invalid body policy for {operation_id}")
        if (body != "forbidden") != operation.has_body:
            raise ValueError(f"Body policy differs from pinned OpenAPI for {operation_id}")
        reviewed.append(
            {
                "operation_id": operation_id,
                "method": method,
                "path": path.removeprefix("/api/v4/"),
                "success_statuses": tuple(sorted(success_statuses)),
                "body": body,
                "source": declared_source,
                "source_sha256": source["files"][declared_source]["sha256"],
                "status_overrides": overrides,
            }
        )
    return sorted(reviewed, key=lambda item: item["operation_id"])


def _python_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render(overlay: dict[str, Any], reviewed: list[dict[str, Any]], overlay_hash: str) -> str:
    lines = [
        '"""Generated structural operation registry; do not edit by hand.',
        "",
        "The authored overlay is the distributed source. The pinned CC BY-NC-SA",
        "OpenAPI documentation was used only to verify these endpoint facts.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from types import MappingProxyType",
        "",
        "from .operation import NativeOperation",
        "",
        f"SOURCE_COMMIT = {_python_string(overlay['source_commit'])}",
        f"OVERLAY_SHA256 = {_python_string(overlay_hash)}",
        "",
        "_OPERATIONS = {",
    ]
    for item in reviewed:
        lines.extend(
            [
                f"    {_python_string(item['operation_id'])}: NativeOperation(",
                f"        operation_id={_python_string(item['operation_id'])},",
                f"        method={_python_string(item['method'])},",
                f"        path_template={_python_string(item['path'])},",
                f"        success_statuses={item['success_statuses']!r},",
                f"        body={_python_string(item['body'])},",
                f"        source={_python_string(item['source'])},",
                f"        source_sha256={_python_string(item['source_sha256'])},",
                "    ),",
            ]
        )
    lines.extend(["}", "", "OPERATIONS = MappingProxyType(_OPERATIONS)", ""])
    return "\n".join(lines)


def generate(root: Path) -> tuple[str, dict[str, Any]]:
    lock = read_json(root / "upstream.lock.json")
    overlay = read_json(root / "manifests/native-api-overlays.json")
    operations, source = inventory(root, lock)
    reviewed = _validated_overlay(overlay, operations, source)
    report = {
        "schema_version": 1,
        "source": {
            "id": source["id"],
            "repository": source["repository"],
            "commit": source["commit"],
            "tree_sha256": source["tree_sha256"],
            "license_review": source["license_review"],
        },
        "discovered_operations": sum(len(items) for items in operations.values()),
        "discovered_operation_ids": len(operations),
        "duplicate_operation_ids": sorted(
            operation_id for operation_id, items in operations.items() if len(items) > 1
        ),
        "generated_operations": len(reviewed),
        "operation_ids": [item["operation_id"] for item in reviewed],
        "status_overrides": [
            {
                "operation_id": item["operation_id"],
                **override,
            }
            for item in reviewed
            for override in item["status_overrides"]
        ],
        "overlay_sha256": sha256((root / "manifests/native-api-overlays.json").read_bytes()),
        "policy": overlay["license_policy"],
    }
    return render(overlay, reviewed, report["overlay_sha256"]), report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail when generated output differs")
    args = parser.parse_args()
    output, report = generate(ROOT)
    target = ROOT / "src/mattergram/native/_operations.py"
    report_path = ROOT / "build/reports/native-api-generation.json"
    if args.check:
        if not target.exists() or target.read_text(encoding="utf-8") != output:
            raise ValueError("Generated native API drift; regenerate and review")
        if report_path.exists() and read_json(report_path) != report:
            raise ValueError("Native API generation report drift; regenerate and review")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(output, encoding="utf-8", newline="\n")
        from tools._common import write_json

        write_json(report_path, report)
    print(
        f"native API verified: {report['generated_operations']} generated of "
        f"{report['discovered_operation_ids']} pinned operationIds"
    )


if __name__ == "__main__":
    main()
