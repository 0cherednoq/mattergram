"""Validated structural metadata for generated low-level Mattermost operations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote

HTTPMethod = Literal["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
BodyPolicy = Literal["forbidden", "optional", "required"]

_OPERATION_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_PATH = re.compile(
    r"(?:[A-Za-z0-9._~-]+|\{[A-Za-z][A-Za-z0-9_]*\})(?:/(?:[A-Za-z0-9._~-]+|\{[A-Za-z][A-Za-z0-9_]*\}))*"
)
_PARAMETER = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class NativeOperation:
    operation_id: str
    method: HTTPMethod
    path_template: str
    success_statuses: tuple[int, ...]
    body: BodyPolicy
    source: str
    source_sha256: str

    def __post_init__(self) -> None:
        if _OPERATION_ID.fullmatch(self.operation_id) is None:
            raise ValueError("Invalid native operation ID")
        if _PATH.fullmatch(self.path_template) is None or any(
            segment in {".", ".."} for segment in self.path_template.split("/")
        ):
            raise ValueError("Invalid native operation path template")
        if (
            not self.success_statuses
            or tuple(sorted(set(self.success_statuses))) != self.success_statuses
            or any(isinstance(status, bool) or not 200 <= status < 300 for status in self.success_statuses)
        ):
            raise ValueError("Native operation success statuses must be unique sorted 2xx integers")
        if self.method in {"GET", "HEAD", "OPTIONS"} and self.body != "forbidden":
            raise ValueError("Safe native operations cannot declare a request body")
        if not self.source.startswith("api/v4/source/") or not self.source.endswith(".yaml"):
            raise ValueError("Invalid native operation source")
        if _SHA256.fullmatch(self.source_sha256) is None:
            raise ValueError("Invalid native operation source hash")

    @property
    def path_parameters(self) -> tuple[str, ...]:
        return tuple(_PARAMETER.findall(self.path_template))

    def render_path(self, parameters: dict[str, str]) -> str:
        expected = set(self.path_parameters)
        if set(parameters) != expected:
            missing = sorted(expected - parameters.keys())
            extra = sorted(parameters.keys() - expected)
            raise ValueError(f"Native path parameters differ: missing={missing}, extra={extra}")
        path = self.path_template
        for name in self.path_parameters:
            value = parameters[name]
            if not isinstance(value, str) or not value or value in {".", ".."}:
                raise ValueError(f"Native path parameter {name!r} must be a nonempty string")
            path = path.replace("{" + name + "}", quote(value, safe=""))
        return path
