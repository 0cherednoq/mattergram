from __future__ import annotations

from typing import Any

from mattergram_dialog.api.entities import AccessSettings


class DefaultStackAccessValidator:
    def is_allowed(self, actor_id: str, settings: AccessSettings | None, event: Any = None) -> bool:
        del event
        if not actor_id:
            return False
        return settings is None or not settings.actor_ids or actor_id in settings.actor_ids


__all__ = ["DefaultStackAccessValidator"]
