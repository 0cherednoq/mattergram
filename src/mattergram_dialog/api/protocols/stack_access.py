from __future__ import annotations

from typing import Any, Protocol

from mattergram_dialog.api.entities import AccessSettings


class StackAccessValidator(Protocol):
    def is_allowed(self, actor_id: str, settings: AccessSettings | None, event: Any = None) -> bool: ...


__all__ = ["StackAccessValidator"]
