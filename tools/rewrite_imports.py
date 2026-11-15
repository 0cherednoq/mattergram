"""Rewrite only reviewed import namespaces, preserving comments and source bytes elsewhere.

The namespace_imports_v1 codemod supports absolute from-imports and explicitly
aliased imports. An unaliased `import aiogram...` needs a reviewed binding rename
and is rejected rather than silently breaking references. Relative imports stay
relative. This module never executes donor code.
"""

from __future__ import annotations

import argparse
import ast
import io
import tokenize
from pathlib import Path

from tools._common import ROOT, safe_path, sha256

CODEMOD = "namespace_imports_v1"


def rewrite_namespace(data: bytes, old: str = "aiogram", new: str = "mattergram") -> bytes:
    if not old.isidentifier() or not new.isidentifier() or old == new:
        raise ValueError("Expected two distinct single-component Python namespaces")
    encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    source = data.decode(encoding)
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    def position(line: int, column: int, *, ast_column: bool = False) -> int:
        if ast_column:
            column = len(lines[line - 1].encode("utf-8")[:column].decode("utf-8"))
        return offsets[line - 1] + column

    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    edits: dict[int, tuple[int, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".")[0] != old:
                continue
            start = position(node.lineno, node.col_offset, ast_column=True)
            # Find the namespace token in this from-import's header, not a comment/string.
            header = [token for token in tokens if position(*token.start) >= start]
            found_from = False
            for token in header:
                if token.type == tokenize.NAME:
                    if not found_from:
                        if token.string != "from":
                            continue
                        found_from = True
                        continue
                    if token.string != old:
                        raise ValueError(f"Unexpected import header at line {node.lineno}")
                    edits[position(*token.start)] = (position(*token.end), new)
                    break
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] != old:
                    continue
                if alias.asname is None:
                    raise ValueError(
                        f"Unaliased import needs a binding-aware adaptation at line {node.lineno}"
                    )
                start = position(alias.lineno, alias.col_offset, ast_column=True)
                edits[start] = (start + len(old), new)
    for start, (end, replacement) in sorted(edits.items(), reverse=True):
        source = source[:start] + replacement + source[end:]
    result = source.encode(encoding)
    # Syntax is a required output check, not proof of runtime compatibility.
    ast.parse(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    allowed = safe_path(ROOT, "build/port-staging")
    destination = args.output if args.output.is_absolute() else ROOT / args.output
    if not destination.resolve().is_relative_to(allowed):
        raise ValueError("Codemod output must be inside build/port-staging")
    # This diagnostic CLI writes only a new file. Pipeline writes go through extract_core.
    data = args.source.read_bytes()
    result = rewrite_namespace(data)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as stream:
        stream.write(result)
    print(f"{CODEMOD}: {sha256(data)} -> {sha256(result)}")


if __name__ == "__main__":
    main()
