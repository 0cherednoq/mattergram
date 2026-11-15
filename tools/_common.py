"""Small stdlib-only primitives shared by migration tools."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    """Reject absolute paths, traversal, Windows drives, and symlink escapes."""
    path = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or ":" in relative
        or path.is_absolute()
        or path.as_posix() != relative
        or any(part in {"..", "."} for part in relative.split("/"))
    ):
        raise ValueError(f"Unsafe relative path: {relative!r}")
    result = root.joinpath(*path.parts)
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes root: {relative!r}")
    return result


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def tree_digest(files: dict[str, dict[str, Any]]) -> str:
    """Hash the sorted path + content hash pairs, independently of host metadata."""
    return sha256("".join(f"{name}\0{info['sha256']}\n" for name, info in sorted(files.items())).encode())


def verify_tree(root: Path, files: dict[str, dict[str, Any]]) -> None:
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if actual != files.keys():
        raise ValueError(
            f"Snapshot file set differs: missing={sorted(files.keys() - actual)[:5]}, extra={sorted(actual - files.keys())[:5]}"
        )
    for name, info in files.items():
        data = safe_path(root, name).read_bytes()
        if len(data) != info["size"] or sha256(data) != info["sha256"]:
            raise ValueError(f"Snapshot content mismatch: {name}")
