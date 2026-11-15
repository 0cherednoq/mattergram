"""Build a deterministic AST inventory without executing donor code.

python -m tools.inventory_upstream --source aiogram
python -m tools.inventory_upstream --source aiogram --focus aiogram.dispatcher.event.handler
"""

from __future__ import annotations

import argparse
import ast
import io
import tokenize
import tomllib
from collections import Counter, deque
from pathlib import Path
from typing import Any

from tools._common import ROOT, read_json, safe_path, sha256, verify_tree, write_json


def module_name(path: str) -> str:
    parts = path.removeprefix("src/").removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def source_text(data: bytes) -> str:
    encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    return data.decode(encoding)


def resolve_relative(module: str, is_package: bool, level: int, name: str | None) -> str | None:
    if not level:
        return name or ""
    package = module.split(".") if is_package else module.split(".")[:-1]
    if level > len(package):
        return None
    return ".".join(package[: len(package) - level + 1] + ([name] if name else []))


class Imports(ast.NodeVisitor):
    """Record guarded and dynamic imports, including aliased import helpers."""

    def __init__(self, module: str, is_package: bool, tree: ast.Module):
        self.module = module
        self.is_package = is_package
        self.imports: list[dict[str, Any]] = []
        self.special: list[dict[str, Any]] = []
        self.guards: list[str] = []
        self.importlib_names = {"importlib"}
        self.import_helpers = {"__import__"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.importlib_names.update(
                    alias.asname or alias.name for alias in node.names if alias.name == "importlib"
                )
            elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
                self.import_helpers.update(
                    alias.asname or alias.name for alias in node.names if alias.name == "import_module"
                )

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        guard = ast.unparse(node.test)
        self.guards.append(guard)
        for statement in node.body:
            self.visit(statement)
        self.guards.pop()
        self.guards.append(f"not ({guard})")
        for statement in node.orelse:
            self.visit(statement)
        self.guards.pop()

    def visit_Try(self, node: ast.Try) -> None:
        self.guards.append("try (may be optional)")
        for statement in node.body:
            self.visit(statement)
        self.guards.pop()
        for handler in node.handlers:
            self.visit(handler)
        for statement in node.orelse + node.finalbody:
            self.visit(statement)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(
                {
                    "module": alias.name,
                    "name": None,
                    "alias": alias.asname,
                    "line": node.lineno,
                    "guards": list(self.guards),
                }
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = resolve_relative(self.module, self.is_package, node.level, node.module)
        for alias in node.names:
            self.imports.append(
                {
                    "module": base,
                    "name": alias.name,
                    "alias": alias.asname,
                    "line": node.lineno,
                    "guards": list(self.guards),
                }
            )
            if alias.name == "*":
                self.special.append(
                    {"kind": "star_import", "line": node.lineno, "expression": ast.unparse(node)}
                )
        if base is None:
            self.special.append(
                {"kind": "unresolved_relative_import", "line": node.lineno, "expression": ast.unparse(node)}
            )

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        dynamic = isinstance(function, ast.Name) and function.id in self.import_helpers
        dynamic |= (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id in self.importlib_names
            and function.attr == "import_module"
        )
        if dynamic:
            self.special.append(
                {"kind": "dynamic_import", "line": node.lineno, "expression": ast.unparse(node)}
            )
        if isinstance(function, ast.Attribute) and function.attr in {
            "register",
            "include_router",
            "include_routers",
        }:
            self.special.append(
                {
                    "kind": "runtime_registration_candidate",
                    "line": node.lineno,
                    "expression": ast.unparse(node),
                }
            )
        self.generic_visit(node)


def inspect_python(data: bytes, module: str, is_package: bool) -> dict[str, Any]:
    tree = ast.parse(source_text(data), filename=module)
    visitor = Imports(module, is_package, tree)
    visitor.visit(tree)
    symbols: list[dict[str, Any]] = []
    inferred: set[str] = set()
    explicit: list[str] | None = None
    export_expressions: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = (
                "class"
                if isinstance(node, ast.ClassDef)
                else "async_function"
                if isinstance(node, ast.AsyncFunctionDef)
                else "function"
            )
            symbol = {"name": node.name, "kind": kind, "line": node.lineno, "end_line": node.end_lineno}
            if isinstance(node, ast.ClassDef):
                symbol["bases"] = [ast.unparse(base) for base in node.bases]
            else:
                symbol["signature"] = f"({ast.unparse(node.args)})"
                symbol["returns"] = ast.unparse(node.returns) if node.returns else None
            symbols.append(symbol)
            inferred.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name != "*":
                    inferred.add(
                        alias.asname
                        or (alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name)
                    )
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [target.id for target in targets if isinstance(target, ast.Name)]
            inferred.update(names)
            if "__all__" in names:
                try:
                    value = ast.literal_eval(node.value) if node.value is not None else None
                    if not isinstance(value, (list, tuple)) or not all(
                        isinstance(item, str) for item in value
                    ):
                        raise ValueError("Non-literal exports")
                    if isinstance(node, ast.AugAssign):
                        if explicit is None or not isinstance(node.op, ast.Add):
                            raise ValueError("Unresolved exports mutation")
                        explicit += list(value)
                    else:
                        explicit = list(value)
                except (ValueError, TypeError, SyntaxError):
                    export_expressions.append(ast.unparse(node))
    # Other mutations/conditional __all__ declarations cannot be resolved statically.
    top_level = {id(node) for node in tree.body}
    for nested in ast.walk(tree):
        if isinstance(nested, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and id(nested) not in top_level:
            targets = nested.targets if isinstance(nested, ast.Assign) else [nested.target]
            if any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
                export_expressions.append(ast.unparse(nested))
        if (
            isinstance(nested, ast.Call)
            and isinstance(nested.func, ast.Attribute)
            and isinstance(nested.func.value, ast.Name)
            and nested.func.value.id == "__all__"
        ):
            export_expressions.append(ast.unparse(nested))
    if export_expressions:
        visitor.special.append({"kind": "unresolved_exports", "expressions": export_expressions})
    return {
        "symbols": symbols,
        "exports": {
            "mode": "unresolved"
            if export_expressions
            else "explicit"
            if explicit is not None
            else "inferred",
            "names": sorted(
                set(
                    explicit
                    if explicit is not None
                    else (name for name in inferred if not name.startswith("_"))
                )
            ),
        },
        "imports": visitor.imports,
        "special_cases": visitor.special,
    }


def resolve_import(item: dict[str, Any], known: set[str], namespace: str) -> tuple[list[str], str | None]:
    base = item["module"]
    if base is None:
        return [], "unresolved relative import"
    if base.split(".")[0] != namespace:
        return [], None
    targets = []
    if base in known:
        targets.append(base)
    name = item["name"]
    child = f"{base}.{name}" if name and name != "*" else base
    if child != base and child in known:
        targets.append(child)
    if not targets:
        return [], f"missing local module: {base}"
    return targets, None


def inventory(snapshot: Path, source: dict[str, Any]) -> dict[str, Any]:
    verify_tree(snapshot, source["files"])
    namespace = source["namespace"]
    modules: dict[str, Any] = {}
    for name, info in sorted(source["files"].items()):
        if not name.endswith(".py"):
            continue
        module = module_name(name)
        entry: dict[str, Any] = {
            "path": name,
            "sha256": info["sha256"],
            "is_package": name.endswith("/__init__.py"),
            "is_test": name.startswith("tests/") or "/test_" in name,
        }
        try:
            entry.update(inspect_python(safe_path(snapshot, name).read_bytes(), module, entry["is_package"]))
        except (SyntaxError, UnicodeError) as error:
            entry.update(
                {
                    "symbols": [],
                    "exports": {"mode": "unresolved", "names": []},
                    "imports": [],
                    "special_cases": [{"kind": "parse_error", "detail": str(error)}],
                }
            )
        if module in modules:
            raise ValueError(f"Module collision: {module}")
        modules[module] = entry
    known = set(modules)
    external: set[str] = set()
    for module, entry in modules.items():
        edges: set[str] = set()
        for item in entry["imports"]:
            targets, problem = resolve_import(item, known, namespace)
            item["local_targets"] = targets
            edges.update(targets)
            if problem:
                entry["special_cases"].append(
                    {"kind": "missing_local_import", "line": item["line"], "detail": problem}
                )
            if item["module"] and not targets:
                external.add(item["module"].split(".")[0])
        entry["local_dependencies"] = sorted(edges)
        entry["telegram_dependencies"] = sorted(
            edge
            for edge in edges
            if edge == f"{namespace}.types"
            or edge.startswith((f"{namespace}.types.", f"{namespace}.methods", f"{namespace}.client"))
        )
    tests_by_module: dict[str, set[str]] = {}
    for entry in modules.values():
        if entry["is_test"]:
            for target in entry["local_dependencies"]:
                tests_by_module.setdefault(target, set()).add(entry["path"])
    for module, entry in modules.items():
        entry["direct_tests"] = sorted(tests_by_module.get(module, set()))
    fixtures = sorted(
        name
        for name in source["files"]
        if name.startswith("tests/") and (name.endswith("conftest.py") or "/fixtures/" in name)
    )
    configs: dict[str, Any] = {}
    if "pyproject.toml" in source["files"]:
        config_data = safe_path(snapshot, "pyproject.toml").read_bytes()
        parsed = tomllib.loads(config_data.decode("utf-8-sig"))
        configs = {
            "path": "pyproject.toml",
            "sha256": sha256(config_data),
            "project": parsed.get("project", {}),
            "build_system": parsed.get("build-system", {}),
            "poetry": parsed.get("tool", {}).get("poetry", {}),
        }
    cases = Counter(case["kind"] for entry in modules.values() for case in entry["special_cases"])
    report = {
        "schema_version": 1,
        "source": {key: source[key] for key in ("id", "repository", "ref", "commit", "tree_sha256")},
        "analysis_limits": [
            "Static AST analysis only; donor code is never imported.",
            "Attributes imported from a local module are linked to that module, not proven to exist.",
            "direct_tests are import links, not a claim of behavioral coverage.",
            "Fixture lists include conftest/fixture directories; dynamic fixtures and runtime registration require review.",
            "Inferred exports and dynamic imports are not proof of a complete graph.",
            "Implicit parent package initialization, getattr/plugin discovery and runtime mutations are not modeled.",
        ],
        "summary": {
            "python_files": len(modules),
            "package_modules": sum(name == namespace or name.startswith(namespace + ".") for name in modules),
            "test_modules": sum(entry["is_test"] for entry in modules.values()),
            "special_cases": dict(sorted(cases.items())),
        },
        "external_import_roots": sorted(external),
        "config": configs,
        "licenses": {name: source["files"][name] for name in source["licenses"]},
        "test_fixtures": fixtures,
        "modules": modules,
    }
    verify_tree(snapshot, source["files"])
    return report


def dependency_paths(report: dict[str, Any], start: str) -> dict[str, list[str]]:
    modules = report["modules"]
    if start not in modules:
        raise ValueError(f"Unknown module: {start}")
    paths = {start: [start]}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for target in modules[current]["local_dependencies"]:
            if target not in paths:
                paths[target] = [*paths[current], target]
                queue.append(target)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="aiogram")
    parser.add_argument("--focus")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    lock = read_json(ROOT / "upstream.lock.json")
    source = next(source for source in lock["sources"] if source["id"] == args.source)
    report = inventory(safe_path(ROOT, source["snapshot"]), source)
    output = args.output or ROOT / "build/inventory" / f"{args.source}.json"
    write_json(output, report)
    if args.focus:
        focused = {
            "module": report["modules"][args.focus],
            "dependency_paths": dependency_paths(report, args.focus),
        }
        write_json(output.with_name(output.stem + "-focus.json"), focused)
        print(f"focus: {args.focus}, {len(focused['dependency_paths'])} transitive modules")
    print(f"{report['summary']}\nreport: {output}")


if __name__ == "__main__":
    main()
