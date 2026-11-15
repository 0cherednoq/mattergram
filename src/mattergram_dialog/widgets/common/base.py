from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from mattergram_dialog.api.exceptions import InvalidWidgetIdError

from .when import WhenCondition, is_visible

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,99}$")


class Widget:
    def __init__(self, *, id: str | None = None, when: WhenCondition = None) -> None:
        if id is not None and not _ID.fullmatch(id):
            raise InvalidWidgetIdError(f"Invalid widget id: {id!r}")
        self._widget_id = id
        self.when = when

    @property
    def widget_id(self) -> str | None:
        return self._widget_id

    async def is_visible(self, data: Mapping[str, Any], manager: Any) -> bool:
        return await is_visible(self.when, data, manager)


__all__ = ["Widget"]
