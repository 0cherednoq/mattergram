"""Apply reviewed exact replacements with pinned pre/post hashes, not fuzzy patches."""

from __future__ import annotations

import ast
import io
import tokenize
from typing import Any

from tools._common import sha256


def apply_patch(data: bytes, patch: dict[str, Any]) -> bytes:
    if patch.get("schema_version") != 1 or not patch.get("reason"):
        raise ValueError("A semantic patch needs a schema and review reason")
    if sha256(data) != patch["input_sha256"]:
        raise ValueError("Semantic patch input hash differs")
    encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    source = data.decode(encoding)
    if not patch.get("replacements"):
        raise ValueError("An applied adaptation needs explicit replacements")
    for replacement in patch["replacements"]:
        before, after = replacement["before"], replacement["after"]
        count = replacement["occurrences"]
        if (
            not isinstance(before, str)
            or not before
            or not isinstance(after, str)
            or type(count) is not int
            or count < 1
        ):
            raise ValueError("Invalid semantic replacement")
        if source.count(before) != count:
            raise ValueError("Semantic replacement occurrence count differs")
        source = source.replace(before, after)
    result = source.encode(encoding)
    if sha256(result) != patch["output_sha256"]:
        raise ValueError("Semantic patch output hash differs")
    ast.parse(result)
    return result
