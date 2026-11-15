from __future__ import annotations

from collections.abc import Mapping


class AdminInputError(ValueError):
    def __init__(self, field_errors: Mapping[str, str]) -> None:
        super().__init__("Administrator input is invalid")
        self.field_errors = dict(field_errors)


__all__ = ["AdminInputError"]
