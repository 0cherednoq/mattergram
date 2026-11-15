from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class WidgetAction:
    """Verified widget-local action decoded from a signed post callback."""

    type: str
    value: str | None = None
    target_state: str | None = None


@dataclass(frozen=True, slots=True)
class WidgetActionResult:
    """Tell the runtime whether to render and optionally switch state."""

    accepted: bool = True
    target_state: str | None = None


class ActionWidget(Protocol):
    async def process_action(
        self,
        action: WidgetAction,
        event: Any,
        manager: Any,
    ) -> WidgetActionResult: ...


__all__ = ["ActionWidget", "WidgetAction", "WidgetActionResult"]
