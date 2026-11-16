"""Generate API, examples, capability, exception, and LLM reference artifacts."""

from __future__ import annotations

import argparse
import ast
import importlib
import inspect
import json
import re
import tomllib
import types
import typing
from collections import Counter
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any

from tools._common import ROOT, read_json, sha256

DEPENDENCIES = frozenset({"core", "provisioning", "redis", "sqlalchemy"})
EXAMPLE_KINDS = frozenset({"declaration", "runnable"})
EXAMPLE_STATUSES = frozenset(
    {"contract-verified", "declaration-only", "live-verified", "offline-verified", "real-redis-verified"}
)
DOC_TYPES = frozenset({"explanation", "how-to", "reference", "tutorial"})
DOC_AUDIENCES = frozenset({"implementer", "maintainer", "operator", "verifier"})
ENVIRONMENT_NAME = re.compile(r"[A-Z][A-Z0-9_]+")


def _text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonempty text")
    return value.strip()


def _strings(value: Any, *, field: str, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{field} must be a list of nonempty strings")
    if not allow_empty and not value:
        raise ValueError(f"{field} must not be empty")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} must not contain duplicates")
    return list(value)


def _load_manifest(root: Path) -> dict[str, Any]:
    path = root / "manifests/documentation.json"
    manifest = read_json(path)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("Unsupported documentation manifest schema")
    for name in ("public_modules", "guides", "examples"):
        if not isinstance(manifest.get(name), list) or not manifest[name]:
            raise ValueError(f"Documentation manifest requires {name}")
    return manifest


def _project_version(root: Path) -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return _text(project.get("version"), field="project.version")


def _summary(obj: Any) -> str | None:
    value = getattr(obj, "__doc__", None)
    if not isinstance(value, str):
        return None
    lines: list[str] = []
    for line in inspect.cleandoc(value).splitlines():
        if not line.strip():
            if lines:
                break
            continue
        lines.append(line.strip())
    return " ".join(lines) or None


def _kind(obj: Any) -> str:
    if inspect.isclass(obj):
        return "class"
    if inspect.iscoroutinefunction(obj):
        return "async-function"
    if inspect.isfunction(obj):
        return "function"
    if isinstance(obj, Mapping):
        return "mapping"
    return "value"


def _stable_repr(value: Any) -> str:
    if inspect.isfunction(value) or inspect.isbuiltin(value):
        return f"<function {value.__module__}.{value.__qualname__}>"
    if type(value) is object:
        return "<sentinel>"
    result = repr(value)
    result = re.sub(r"<object object at 0x[0-9A-Fa-f]+>", "<sentinel>", result)
    return re.sub(r"<function ([^ >]+) at 0x[0-9A-Fa-f]+>", r"<function \1>", result)


def _type_name(value: Any) -> str:
    module = getattr(value, "__module__", "")
    name = getattr(value, "__qualname__", getattr(value, "_name", None))
    if not isinstance(name, str):
        return _stable_repr(value).replace("typing.", "")
    if module in ("", "builtins", "typing"):
        return name
    return f"{module}.{name}"


def _annotation(value: Any) -> str:
    if value is inspect.Signature.empty:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "[" + ", ".join(_annotation(item) for item in value) + "]"
    if isinstance(value, typing.ForwardRef):
        return value.__forward_arg__
    if value is None or value is types.NoneType:
        return "None"
    if value is typing.Any:
        return "Any"
    origin = typing.get_origin(value)
    arguments = typing.get_args(value)
    if origin in (types.UnionType, typing.Union):
        ordered = [item for item in arguments if item is not types.NoneType]
        if len(ordered) != len(arguments):
            ordered.append(types.NoneType)
        return " | ".join(_annotation(item) for item in ordered)
    if origin is typing.Annotated:
        base, *metadata = arguments
        return "Annotated[" + ", ".join([_annotation(base), *map(_stable_repr, metadata)]) + "]"
    if origin is typing.Literal:
        return "Literal[" + ", ".join(_stable_repr(item) for item in arguments) + "]"
    if origin is not None:
        return _type_name(origin) + "[" + ", ".join(_annotation(item) for item in arguments) + "]"
    if inspect.isclass(value) or hasattr(value, "__qualname__"):
        return _type_name(value)
    return _stable_repr(value).replace("typing.", "")


def _parameter(parameter: inspect.Parameter) -> str:
    prefix = ""
    if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
        prefix = "*"
    elif parameter.kind is inspect.Parameter.VAR_KEYWORD:
        prefix = "**"
    result = prefix + parameter.name
    annotation = _annotation(parameter.annotation)
    if annotation:
        result += ": " + annotation
    if parameter.default is not inspect.Parameter.empty:
        result += " = " + _stable_repr(parameter.default)
    return result


def _signature(obj: Any) -> str | None:
    if not (inspect.isclass(obj) or inspect.isroutine(obj)):
        return None
    if inspect.isclass(obj) and issubclass(obj, Enum):
        return "(value: object) -> Self"
    try:
        signature = inspect.signature(obj, eval_str=False)
    except (TypeError, ValueError):
        return None
    positional_only: list[str] = []
    positional: list[str] = []
    keyword_only: list[str] = []
    var_positional: str | None = None
    var_keyword: str | None = None
    for parameter in signature.parameters.values():
        rendered = _parameter(parameter)
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            positional_only.append(rendered)
        elif parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD:
            positional.append(rendered)
        elif parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            var_positional = rendered
        elif parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            keyword_only.append(rendered)
        else:
            var_keyword = rendered
    parts = positional_only.copy()
    if positional_only:
        parts.append("/")
    parts.extend(positional)
    if var_positional is not None:
        parts.append(var_positional)
    elif keyword_only:
        parts.append("*")
    parts.extend(keyword_only)
    if var_keyword is not None:
        parts.append(var_keyword)
    result = "(" + ", ".join(parts) + ")"
    return_annotation = _annotation(signature.return_annotation)
    if return_annotation:
        result += " -> " + return_annotation
    return result


def _relative_source(obj: Any, root: Path, module: ModuleType, export_name: str) -> dict[str, Any]:
    source_path: Path | None = None
    line: int | None = None
    try:
        candidate = inspect.getsourcefile(obj)
        if candidate is not None:
            source_path = Path(candidate).resolve()
        _, line = inspect.getsourcelines(obj)
    except (OSError, TypeError):
        candidate = getattr(module, "__file__", None)
        if isinstance(candidate, str):
            source_path = Path(candidate).resolve()
    if source_path is None or not source_path.is_relative_to(root.resolve()):
        raise ValueError(f"Public export has no repository source: {module.__name__}.{export_name}")
    if line is None:
        source = source_path.read_text(encoding="utf-8")
        match = re.search(rf"(?m)^(?:class|def|async def)\s+{re.escape(export_name)}\b", source)
        if match is None:
            match = re.search(rf"(?m)^\s*{re.escape(export_name)}\s*=", source)
        line = source.count("\n", 0, match.start()) + 1 if match is not None else 1
    return {"path": source_path.relative_to(root).as_posix(), "line": line}


def _public_members(cls: type[Any], root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for name, raw in cls.__dict__.items():
        if name.startswith("_"):
            continue
        kind = "member"
        value = raw
        if isinstance(raw, staticmethod):
            value = raw.__func__
            kind = "static-method"
        elif isinstance(raw, classmethod):
            value = raw.__func__
            kind = "class-method"
        elif isinstance(raw, property):
            value = raw.fget
            kind = "property"
        elif inspect.iscoroutinefunction(raw):
            kind = "async-method"
        elif inspect.isfunction(raw):
            kind = "method"
        else:
            continue
        if value is None:
            continue
        source_value = inspect.unwrap(value)
        source_module = importlib.import_module(source_value.__module__)
        result.append(
            {
                "name": name,
                "kind": kind,
                "signature": _signature(value),
                "summary": _summary(value),
                "source": _relative_source(source_value, root, source_module, name),
            }
        )
    return sorted(result, key=lambda item: item["name"])


def _module_exports(entry: Any, root: Path) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError("public_modules entries must be objects")
    name = _text(entry.get("name"), field="public module name")
    dependency = _text(entry.get("dependency"), field=f"{name}.dependency")
    if dependency not in DEPENDENCIES:
        raise ValueError(f"Unknown dependency group for {name}")
    module = importlib.import_module(name)
    configured = entry.get("exports")
    if configured == "__all__":
        raw = getattr(module, "__all__", None)
        if not isinstance(raw, list):
            raise ValueError(f"{name} has no static list __all__")
        exports = _strings(raw, field=f"{name}.__all__")
    else:
        exports = _strings(configured, field=f"{name}.exports")
    records: list[dict[str, Any]] = []
    for export_name in exports:
        if export_name.startswith("_") and export_name != "__version__":
            raise ValueError(f"Private export configured for {name}: {export_name}")
        try:
            obj = getattr(module, export_name)
        except AttributeError:
            raise ValueError(f"Missing public export: {name}.{export_name}") from None
        kind = _kind(obj)
        record: dict[str, Any] = {
            "name": export_name,
            "qualified_name": f"{name}.{export_name}",
            "kind": kind,
            "signature": _signature(obj),
            "summary": _summary(obj) if kind not in {"mapping", "value"} else None,
            "source": _relative_source(obj, root, module, export_name),
        }
        if inspect.isclass(obj):
            record["bases"] = [
                f"{base.__module__}.{base.__qualname__}" for base in obj.__bases__ if base is not object
            ]
            members = _public_members(obj, root)
            if members:
                record["members"] = members
        records.append(record)
    return {"name": name, "dependency": dependency, "exports": records}


class _EnvironmentVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if (
            isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "os"
            and node.value.attr == "environ"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
            and ENVIRONMENT_NAME.fullmatch(node.slice.value)
        ):
            self.names.add(node.slice.value)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Attribute)
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "os"
            and node.func.value.attr == "environ"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and ENVIRONMENT_NAME.fullmatch(node.args[0].value)
        ):
            self.names.add(node.args[0].value)
        self.generic_visit(node)


def _example_record(entry: Any, root: Path) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError("examples entries must be objects")
    path = _text(entry.get("path"), field="example path")
    target = root / path
    if not target.is_file() or target.parent != root / "examples" or target.suffix != ".py":
        raise ValueError(f"Invalid example path: {path}")
    source = target.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=path)
    docstring = ast.get_docstring(tree, clean=True)
    if not docstring:
        raise ValueError(f"Example needs a module docstring: {path}")
    visitor = _EnvironmentVisitor()
    visitor.visit(tree)
    kind = _text(entry.get("kind"), field=f"{path}.kind")
    status = _text(entry.get("status"), field=f"{path}.status")
    if kind not in EXAMPLE_KINDS or status not in EXAMPLE_STATUSES:
        raise ValueError(f"Invalid kind/status for {path}")
    dependencies = _strings(entry.get("dependencies"), field=f"{path}.dependencies")
    if set(dependencies) - DEPENDENCIES:
        raise ValueError(f"Unknown example dependency for {path}")
    guide = _text(entry.get("guide"), field=f"{path}.guide")
    if not (root / guide).is_file():
        raise ValueError(f"Missing example guide: {guide}")
    evidence = _strings(entry.get("evidence"), field=f"{path}.evidence")
    if any(not (root / item).is_file() for item in evidence):
        raise ValueError(f"Missing example evidence for {path}")
    return {
        "path": path,
        "scenario": _text(entry.get("scenario"), field=f"{path}.scenario"),
        "summary": docstring.split("\n\n", 1)[0].replace("\n", " "),
        "kind": kind,
        "minimum_profile": _text(entry.get("minimum_profile"), field=f"{path}.minimum_profile"),
        "permissions": _strings(entry.get("permissions"), field=f"{path}.permissions", allow_empty=True),
        "dependencies": dependencies,
        "environment": sorted(visitor.names),
        "assertions": _strings(entry.get("assertions"), field=f"{path}.assertions"),
        "status": status,
        "guide": guide,
        "evidence": evidence,
        "sha256": sha256(source.encode()),
    }


def _guide_record(entry: Any, root: Path) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError("guides entries must be objects")
    path = _text(entry.get("path"), field="guide path")
    target = root / path
    if not target.is_file() or target.parent != root / "docs" or target.suffix != ".md":
        raise ValueError(f"Invalid guide path: {path}")
    content = target.read_text(encoding="utf-8")
    if not content.startswith("# "):
        raise ValueError(f"Guide needs one leading H1: {path}")
    audience = _text(entry.get("audience"), field=f"{path}.audience")
    doc_type = _text(entry.get("type"), field=f"{path}.type")
    if audience not in DOC_AUDIENCES or doc_type not in DOC_TYPES:
        raise ValueError(f"Invalid audience/type for {path}")
    return {
        "path": path,
        "title": _text(entry.get("title"), field=f"{path}.title"),
        "audience": audience,
        "type": doc_type,
        "summary": _text(entry.get("summary"), field=f"{path}.summary"),
        "topics": _strings(entry.get("topics"), field=f"{path}.topics"),
        "sha256": sha256(content.encode()),
    }


def _api(root: Path, manifest: dict[str, Any], version: str) -> dict[str, Any]:
    modules = [_module_exports(item, root) for item in manifest["public_modules"]]
    names = [module["name"] for module in modules]
    if len(names) != len(set(names)):
        raise ValueError("Public module names must be unique")
    return {
        "schema_version": 1,
        "project": "mattergram",
        "version": version,
        "status": "implemented-public-api",
        "modules": modules,
    }


def _markdown(value: str | None) -> str:
    return (value or "").replace("|", "\\|").replace("\n", " ")


def _source_link(source: dict[str, Any], *, prefix: str = "../..") -> str:
    path = source["path"]
    line = source["line"]
    return f"[{path}]({prefix}/{path}#L{line})"


def _render_api(api: dict[str, Any]) -> str:
    lines = [
        "# Public API reference",
        "",
        "Generated from the implemented Python exports and signatures. Do not edit this file manually.",
        "",
        f"SDK version: `{api['version']}`.",
        "",
    ]
    for module in api["modules"]:
        lines.extend(
            [
                f"## `{module['name']}`",
                "",
                f"Dependency group: `{module['dependency']}`.",
                "",
                "| Export | Kind | Signature | Source |",
                "| --- | --- | --- | --- |",
            ]
        )
        for export in module["exports"]:
            signature = f"`{_markdown(export['signature'])}`" if export["signature"] else "—"
            lines.append(
                f"| `{export['name']}` | {export['kind']} | {signature} | {_source_link(export['source'])} |"
            )
        lines.append("")
        for export in module["exports"]:
            if not export.get("summary") and not export.get("members"):
                continue
            lines.extend([f"### `{export['name']}`", ""])
            if export.get("summary"):
                lines.extend([export["summary"], ""])
            members = export.get("members", [])
            if members:
                lines.extend(
                    [
                        "| Public member | Kind | Signature | Source |",
                        "| --- | --- | --- | --- |",
                    ]
                )
                for member in members:
                    signature = f"`{_markdown(member['signature'])}`" if member["signature"] else "—"
                    lines.append(
                        f"| `{member['name']}` | {member['kind']} | {signature} | "
                        f"{_source_link(member['source'])} |"
                    )
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_exceptions(api: dict[str, Any]) -> str:
    module = next(item for item in api["modules"] if item["name"] == "mattergram.exceptions")
    lines = [
        "# Exceptions",
        "",
        "Reference for implemented SDK exceptions. Catch the narrowest class that matches the recovery action.",
        "",
        "| Exception | Base | Meaning | Source |",
        "| --- | --- | --- | --- |",
    ]
    for item in module["exports"]:
        bases = ", ".join(name.rsplit(".", 1)[-1] for name in item.get("bases", []))
        lines.append(
            f"| `{item['name']}` | `{bases}` | {_markdown(item['summary'])} | "
            f"{_source_link(item['source'])} |"
        )
    lines.extend(
        [
            "",
            "`RequestOutcomeUnknown` means a write may have reached Mattermost. Reconcile server state before retrying.",
            "",
            "`ResponseStateError` and `ResponseDeadlineError` indicate interaction lifecycle errors. They do not mean that a second response is safe.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _render_capabilities() -> str:
    from mattergram.ui.capabilities import SERVER_FEATURES

    features = sorted({feature for values in SERVER_FEATURES.values() for feature in values})
    versions = list(SERVER_FEATURES)
    lines = [
        "# UI capability reference",
        "",
        "Generated from `mattergram.ui.capabilities.SERVER_FEATURES`. Server support alone does not prove client support or feature-flag availability.",
        "",
        "| Feature | " + " | ".join(versions) + " |",
        "| --- | " + " | ".join("---" for _ in versions) + " |",
    ]
    for feature in features:
        lines.append(
            "| `"
            + feature
            + "` | "
            + " | ".join("yes" if feature in SERVER_FEATURES[version] else "no" for version in versions)
            + " |"
        )
    lines.extend(
        [
            "",
            "Use `UIProfile.require(feature)` before serialization. An unknown client declaration remains unknown unless the application supplies explicit evidence or an override for an unknown capability.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _render_examples(examples: list[dict[str, Any]]) -> str:
    lines = [
        "# Examples index",
        "",
        "This generated index distinguishes runnable, declaration-only, offline, contract, Redis, and live verification levels.",
        "",
        "| Example | Scenario | Minimum profile | Status | Guide |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in examples:
        example_link = "[" + item["path"] + "](../" + item["path"] + ")"
        guide_link = "[guide](" + item["guide"].removeprefix("docs/") + ")"
        lines.append(
            f"| {example_link} | {_markdown(item['scenario'])} | "
            f"{_markdown(item['minimum_profile'])} | `{item['status']}` | {guide_link} |"
        )
    for item in examples:
        lines.extend(
            [
                "",
                f"## `{item['path']}`",
                "",
                item["summary"],
                "",
                f"- Kind: `{item['kind']}`",
                f"- Dependencies: {', '.join(f'`{value}`' for value in item['dependencies'])}",
                "- Environment: " + (", ".join(f"`{value}`" for value in item["environment"]) or "none"),
                "- Permissions: " + ("; ".join(item["permissions"]) or "none"),
                "- Assertions: " + "; ".join(item["assertions"]),
                "- Evidence: "
                + ", ".join(
                    "[" + value + "](" + value.removeprefix("docs/") + ")" for value in item["evidence"]
                ),
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _render_llms(version: str, guides: list[dict[str, Any]]) -> str:
    lines = [
        f"# mattergram {version}",
        "",
        "> Native Mattermost bot framework with aiogram-style routing, explicit HTTP/WS lifecycles, typed UI/forms, provisioning, and mattergram_dialog.",
        "",
        "Use only imports present in docs/reference/public-api.json. Treat capability and verification status as versioned facts.",
        "",
        "## Start",
        "",
        "- [Project README](README.md)",
        "- [Examples index](docs/examples.md)",
        "- [Public API](docs/reference/api.md)",
        "- [Exceptions](docs/reference/exceptions.md)",
        "- [UI capabilities](docs/reference/capabilities.md)",
        "",
        "## Guides",
        "",
    ]
    for guide in guides:
        lines.append(f"- [{guide['title']}]({guide['path']}): {guide['summary']}")
    lines.extend(
        [
            "",
            "## Important boundaries",
            "",
            "- WebSocket is inbound event transport. Shared post writes use REST or one callback response.",
            "- Native forms and posts are different UI surfaces.",
            "- A capability needs server, client, flag, and permission evidence; unknown is not supported.",
            "- RequestOutcomeUnknown requires reconciliation before retrying a write.",
            "- Telegram-only APIs and widgets are not compatibility promises.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def generate(root: Path) -> tuple[dict[str, str], dict[str, Any]]:
    manifest = _load_manifest(root)
    version = _project_version(root)
    guides = [_guide_record(item, root) for item in manifest["guides"]]
    examples = [_example_record(item, root) for item in manifest["examples"]]
    guide_paths = [item["path"] for item in guides]
    example_paths = [item["path"] for item in examples]
    if len(guide_paths) != len(set(guide_paths)) or len(example_paths) != len(set(example_paths)):
        raise ValueError("Documentation manifest paths must be unique")
    discovered_examples = {path.relative_to(root).as_posix() for path in (root / "examples").glob("*.py")}
    if discovered_examples != set(example_paths):
        raise ValueError(
            "Example manifest drift: "
            f"missing={sorted(discovered_examples - set(example_paths))}, "
            f"stale={sorted(set(example_paths) - discovered_examples)}"
        )
    api = _api(root, manifest, version)
    api_json = json.dumps(api, indent=2, ensure_ascii=False) + "\n"
    outputs = {
        "docs/reference/public-api.json": api_json,
        "docs/reference/api.md": _render_api(api),
        "docs/reference/exceptions.md": _render_exceptions(api),
        "docs/reference/capabilities.md": _render_capabilities(),
        "docs/examples.md": _render_examples(examples),
        "llms.txt": _render_llms(version, guides),
    }
    status_counts = Counter(item["status"] for item in examples)
    report = {
        "schema_version": 1,
        "result": "generated-reference-current",
        "version": version,
        "manifest_sha256": sha256((root / "manifests/documentation.json").read_bytes()),
        "public_modules": len(api["modules"]),
        "public_exports": sum(len(module["exports"]) for module in api["modules"]),
        "public_members": sum(
            len(export.get("members", [])) for module in api["modules"] for export in module["exports"]
        ),
        "guides": len(guides),
        "examples": len(examples),
        "example_statuses": dict(sorted(status_counts.items())),
        "artifacts": {path: sha256(content.encode()) for path, content in sorted(outputs.items())},
        "network_access": "none",
        "mutating_operations": "none",
    }
    outputs["docs/reports/documentation.json"] = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    return outputs, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail when committed artifacts differ")
    args = parser.parse_args()
    outputs, report = generate(ROOT)
    changed: list[str] = []
    for relative, content in outputs.items():
        target = ROOT / relative
        if args.check:
            if not target.is_file() or target.read_text(encoding="utf-8") != content:
                changed.append(relative)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
    if changed:
        raise ValueError("Documentation drift; regenerate and review: " + ", ".join(changed))
    print(
        f"documentation verified: {report['public_exports']} exports, "
        f"{report['examples']} examples, {report['guides']} guides"
    )


if __name__ == "__main__":
    main()
