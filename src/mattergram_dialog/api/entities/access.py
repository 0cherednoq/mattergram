from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AccessSettings:
    actor_ids: tuple[str, ...] = ()
    custom: Any = None

    def __post_init__(self) -> None:
        if len(set(self.actor_ids)) != len(self.actor_ids) or any(
            not isinstance(actor_id, str) or not actor_id for actor_id in self.actor_ids
        ):
            raise ValueError("actor_ids must contain unique nonempty Mattermost user IDs")
