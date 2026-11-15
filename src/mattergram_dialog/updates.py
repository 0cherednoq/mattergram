from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from mattergram_dialog.runtime import DialogRef, DialogRuntime


@dataclass(frozen=True, slots=True)
class DialogUpdate:
    """One immutable, monotonically ordered dialog data snapshot."""

    revision: int
    data: Mapping[str, Any]

    def __post_init__(self) -> None:
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("DialogUpdate revision must be a nonnegative integer")


class CoalescingDialogUpdater:
    """Rate-limit worker snapshots and keep only the newest pending revision.

    ``submit`` is intentionally non-blocking. Call ``drain`` before a worker
    exits so the trailing snapshot is delivered and transport errors surface.
    The helper is process-local; the persisted revision guard in
    ``DialogRuntime.update`` remains authoritative across workers.
    """

    def __init__(self, runtime: DialogRuntime, *, interval: float = 1.0) -> None:
        if (
            isinstance(interval, bool)
            or not isinstance(interval, (int, float))
            or not math.isfinite(interval)
            or interval < 0.05
            or interval > 60
        ):
            raise ValueError("Dialog update interval must be between 0.05 and 60 seconds")
        self.runtime = runtime
        self.interval = float(interval)
        self._latest: dict[DialogRef, int] = {}
        self._pending: dict[DialogRef, DialogUpdate] = {}
        self._tasks: dict[DialogRef, asyncio.Task[None]] = {}
        self._wake: dict[DialogRef, asyncio.Event] = {}
        self._errors: list[BaseException] = []
        self._closed = False

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def submit(
        self,
        ref: DialogRef,
        revision: int,
        data: Mapping[str, Any],
        *,
        immediate: bool = False,
    ) -> bool:
        """Queue a newer snapshot; return false for a local stale revision."""

        if self._closed:
            raise RuntimeError("CoalescingDialogUpdater is closed")
        update = DialogUpdate(revision, dict(data))
        current = self._latest.get(ref)
        if current is not None and revision <= current:
            return False
        self._latest[ref] = revision
        self._pending[ref] = update
        wake = self._wake.setdefault(ref, asyncio.Event())
        if immediate:
            wake.set()
        task = self._tasks.get(ref)
        if task is None or task.done():
            self._tasks[ref] = asyncio.create_task(self._run(ref))
        return True

    async def _run(self, ref: DialogRef) -> None:
        try:
            wake = self._wake[ref]
            while True:
                try:
                    await asyncio.wait_for(wake.wait(), timeout=self.interval)
                except TimeoutError:
                    pass
                wake.clear()
                update = self._pending.pop(ref, None)
                if update is None:
                    return
                await self.runtime.update(
                    ref,
                    update.data,
                    revision=update.revision,
                )
                if ref not in self._pending:
                    return
        except BaseException as error:
            if not isinstance(error, asyncio.CancelledError):
                self._errors.append(error)
            raise
        finally:
            self._tasks.pop(ref, None)
            self._wake.pop(ref, None)

    async def drain(self) -> None:
        """Wait for all accepted snapshots and raise the first delivery error."""

        while self._tasks:
            for wake in self._wake.values():
                wake.set()
            tasks = tuple(self._tasks.values())
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._errors:
            error = self._errors.pop(0)
            self._errors.clear()
            raise error

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.drain()

    async def __aenter__(self) -> CoalescingDialogUpdater:
        if self._closed:
            raise RuntimeError("CoalescingDialogUpdater is closed")
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.close()


__all__ = ["CoalescingDialogUpdater", "DialogUpdate"]
