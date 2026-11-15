"""Audit a staged port. Any integrity/dependency/adaptation finding exits nonzero.

python -m tools.audit_port --stage build/port-staging/core
This is a static gate. A clean audit is not a claim that behavior or a server
integration has been verified, and it never promotes files into maintained src.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Any

from tools._common import ROOT, read_json, safe_path, write_json
from tools.extract_core import render_stage, stage_path, verify_stage
from tools.inventory_upstream import inspect_python, module_name, source_text


def module_bindings(tree: ast.Module) -> set[str]:
    """Collect module bindings under guards, without mistaking function locals for exports."""
    names: set[str] = set()

    def target_names(target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for child in target.elts:
                target_names(child)

    def visit(statements: list[ast.stmt]) -> None:
        for node in statements:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name != "*":
                        names.add(
                            alias.asname
                            or (alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name)
                        )
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    target_names(target)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                target_names(node.target)
            elif isinstance(node, ast.If):
                visit(node.body)
                visit(node.orelse)
            elif isinstance(node, ast.Try):
                visit(node.body + node.orelse + node.finalbody)
                for handler in node.handlers:
                    visit(handler.body)

    visit(tree.body)
    return names


def audit_sources(files: dict[str, bytes], namespace: str, policy: dict[str, Any]) -> list[dict[str, Any]]:
    """Audit actual Python bytes; report guarded imports too, not only runtime paths."""
    forbidden = set(policy["forbidden_import_roots"])
    prefixes = tuple(policy["forbidden_symbol_prefixes"])
    if not {"aiogram", "aiogram_dialog", "telegram"}.issubset(forbidden) or not {
        "Telegram",
        "Aiogram",
    }.issubset(prefixes):
        raise ValueError("Audit policy cannot weaken the forbidden platform boundary")
    external = set(policy["allowed_external_import_roots"])
    stdlib = set(sys.stdlib_module_names)
    modules: dict[str, dict[str, Any]] = {}
    findings: list[dict[str, Any]] = []

    def problem(kind: str, path: str, detail: str, line: int | None = None) -> None:
        item: dict[str, Any] = {"kind": kind, "path": path, "detail": detail}
        if line is not None:
            item["line"] = line
        findings.append(item)

    for path, data in sorted(files.items()):
        if not path.startswith(f"src/{namespace}/") or not path.endswith(".py"):
            continue
        module = module_name(path)
        try:
            tree = ast.parse(source_text(data), filename=path)
            info = inspect_python(data, module, path.endswith("/__init__.py"))
        except (SyntaxError, UnicodeError) as error:
            problem("invalid_python", path, str(error))
            continue
        if module in modules:
            problem("module_collision", path, module)
            continue
        modules[module] = {"path": path, "info": info, "bindings": module_bindings(tree)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and (node.id in forbidden or node.id.startswith(prefixes)):
                problem("forbidden_symbol", path, node.id, node.lineno)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"eval", "exec", "compile"}
            ):
                problem("unresolved_link", path, "dynamic code execution", node.lineno)
        for name in set(info["exports"]["names"]) | modules[module]["bindings"]:
            if name.startswith(prefixes) or name in forbidden:
                problem("forbidden_export", path, name)
        for case in info["special_cases"]:
            if case["kind"] != "runtime_registration_candidate":
                problem("unresolved_link", path, case["kind"], case.get("line"))
        if any(symbol["name"] == "__getattr__" for symbol in info["symbols"]):
            problem("unresolved_link", path, "dynamic module __getattr__ exports")
    if not modules:
        problem("missing_code", "src/" + namespace, "No Python modules to audit")
    packages = {
        ".".join(module.split(".")[:index])
        for module in modules
        for index in range(1, len(module.split(".")))
    }
    known = set(modules) | packages
    for module, entry in modules.items():
        info = entry["info"]
        path = entry["path"]
        for item in info["imports"]:
            base = item["module"]
            name = item["name"]
            if base is None:
                problem("missing_dependency", path, "Relative import escapes package", item["line"])
                continue
            root = base.split(".")[0]
            if root in forbidden:
                problem("forbidden_import", path, base, item["line"])
                continue
            if name and name.startswith(prefixes):
                problem("forbidden_import", path, f"{base}.{name}", item["line"])
            if root == namespace:
                child = f"{base}.{name}" if name and name != "*" else base
                if base not in known:
                    problem("missing_dependency", path, base, item["line"])
                elif (
                    name
                    and name != "*"
                    and child not in known
                    and name not in modules.get(base, {}).get("bindings", set())
                ):
                    problem("missing_export", path, child, item["line"])
            elif root not in external and root not in stdlib and root != "__future__":
                # annotationlib is stdlib only in >=3.14; its import must be guarded.
                if root == "annotationlib" and any(
                    "sys.version_info" in guard and "3, 14" in guard for guard in item["guards"]
                ):
                    continue
                problem("undeclared_external_dependency", path, base, item["line"])
    return sorted(
        findings, key=lambda item: (item["path"], item.get("line", 0), item["kind"], item["detail"])
    )


def audit(root: Path, stage: Path, manifest: dict[str, Any], lock: dict[str, Any]) -> dict[str, Any]:
    destination = stage_path(root, stage)
    report: dict[str, Any] = {
        "schema_version": 1,
        "scope": "static staging integrity/import/export/dependency gate, not behavioral or server verification",
        "stage": destination.relative_to(root.resolve()).as_posix(),
        "ready": False,
        "findings": [],
        "analysis_limits": [
            "No donor code is imported or executed.",
            "Guarded declarations are conservatively visible; static bindings do not prove runtime initialization order.",
            "Runtime registrations, arbitrary alias mutation and reflection require behavioral review.",
            "Allowed external roots are declarations, not proof that dependencies or their attributes are installed.",
        ],
    }
    try:
        outputs, receipt = render_stage(root, manifest, lock)
        verify_stage(destination, outputs)
        findings = audit_sources(outputs, manifest["target_namespace"], manifest["audit_policy"])
    except (ValueError, OSError, KeyError, SyntaxError, UnicodeError) as error:
        report["findings"] = [{"kind": "integrity", "path": report["stage"], "detail": str(error)}]
        return report
    findings.extend(
        {"kind": "pending_adaptation", "path": item["target"], "detail": item["reason"]}
        for item in receipt["pending_adaptations"]
    )
    for entry in manifest["entries"]:
        if entry["action"] == "exclude":
            continue
        for dependency in entry["dependencies"]:
            if dependency["decision"] == "unresolved":
                findings.append(
                    {
                        "kind": "unresolved_dependency_decision",
                        "path": entry["target"],
                        "detail": dependency["module"],
                    }
                )
    report["findings"] = sorted(
        findings, key=lambda item: (item["path"], item.get("line", 0), item["kind"], item["detail"])
    )
    report["ready"] = not findings
    report["modules"] = len(receipt["files"])
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, default=Path("build/port-staging/core"))
    parser.add_argument("--manifest", type=Path, default=Path("manifests/core.json"))
    parser.add_argument("--report", type=Path, default=Path("build/reports/port-audit.json"))
    args = parser.parse_args()
    report = audit(
        ROOT,
        args.stage,
        read_json(safe_path(ROOT, args.manifest.as_posix())),
        read_json(ROOT / "upstream.lock.json"),
    )
    path = safe_path(ROOT, args.report.as_posix())
    write_json(path, report)
    print(
        f"audit: {'ready' if report['ready'] else 'BLOCKED'}, {len(report['findings'])} findings; report: {path}"
    )
    if not report["ready"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
