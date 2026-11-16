from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol, TypeAlias

from mattergram_dialog.api.exceptions import OutdatedIntent
from mattergram_dialog.navigation import DialogManager
from mattergram_dialog.rendering import MattermostRenderer
from mattergram_dialog.runtime import DialogRef
from mattergram_dialog.updates import CoalescingDialogUpdater

from .models import CheckRun, ImportRun, _id, _now
from .service import GameAccountsAdminService

GameAdminJobState = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
]
TERMINAL_JOB_STATES: frozenset[GameAdminJobState] = frozenset({"completed", "failed", "cancelled"})


@dataclass(frozen=True, slots=True)
class ImportAccountsJob:
    game: str
    file_id: str


@dataclass(frozen=True, slots=True)
class CheckAccountJob:
    account_id: str


@dataclass(frozen=True, slots=True)
class CheckAllAccountsJob:
    pass


@dataclass(frozen=True, slots=True)
class CheckImportedAccountsJob:
    import_id: str


GameAdminJobPayload: TypeAlias = (
    ImportAccountsJob | CheckAccountJob | CheckAllAccountsJob | CheckImportedAccountsJob
)
GameAdminJobResult: TypeAlias = ImportRun | CheckRun | None


@dataclass(frozen=True, slots=True)
class GameAdminJob:
    """Durable, secret-free snapshot of one background operation."""

    id: str
    actor_id: str
    dialog_ref: DialogRef
    payload: GameAdminJobPayload
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    single_flight_key: str | None = None
    revision: int = 0
    state: GameAdminJobState = "queued"
    current: int = 0
    total: int | None = None
    stage: str = "queued"
    cancel_requested: bool = False
    dialog_outdated: bool = False
    result: GameAdminJobResult = None
    error_type: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.actor_id:
            raise ValueError("Game admin jobs require nonempty job and actor IDs")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("Game admin job revision must be a nonnegative integer")
        if type(self.current) is not int or self.current < 0:
            raise ValueError("Game admin job current progress must be a nonnegative integer")
        if self.total is not None and (
            type(self.total) is not int or self.total < 0 or self.current > self.total
        ):
            raise ValueError("Game admin job total must be nonnegative and no smaller than current")
        if not self.stage:
            raise ValueError("Game admin job stage must be nonempty")
        if self.state in TERMINAL_JOB_STATES and self.state == "completed" and self.result is None:
            raise ValueError("Completed game admin jobs require a result")

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_JOB_STATES


class JobRevisionConflict(RuntimeError):
    """The persisted job changed after the caller read it."""


class ActiveJobConflict(RuntimeError):
    """A job with the same atomic single-flight key is already active."""

    def __init__(self, job: GameAdminJob) -> None:
        super().__init__(f"Job {job.id!r} is already active")
        self.job = job


class GameAdminJobRepository(Protocol):
    """Durable port with CAS revisions and atomic active single-flight keys."""

    async def create(self, job: GameAdminJob) -> None: ...

    async def get(self, job_id: str) -> GameAdminJob | None: ...

    async def replace(self, job: GameAdminJob, *, expected_revision: int) -> None: ...

    async def request_cancel(self, job_id: str) -> GameAdminJob: ...

    async def terminal_history(self, actor_id: str) -> tuple[GameAdminJob, ...]: ...


class GameAdminJobQueue(Protocol):
    """Production queue port. Delivery may be at-least-once."""

    async def enqueue(self, job_id: str) -> None: ...


class DialogJobUpdater(Protocol):
    async def update(
        self,
        ref: DialogRef,
        data: dict[str, object] | None = None,
        *,
        revision: int | None = None,
    ) -> bool: ...


class CoalescingDialogJobUpdater:
    """Adapt the shared dialog coalescer to background-job progress snapshots."""

    def __init__(self, updater: CoalescingDialogUpdater) -> None:
        self.updater = updater

    async def update(
        self,
        ref: DialogRef,
        data: dict[str, object] | None = None,
        *,
        revision: int | None = None,
    ) -> bool:
        if data is None or revision is None:
            raise ValueError("Job progress requires data and a monotonic revision")
        state = data.get("job_state")
        terminal = state in TERMINAL_JOB_STATES
        flush = terminal or data.get("job_stage") == "starting"
        accepted = self.updater.submit(
            ref,
            revision,
            data,
            immediate=flush,
        )
        if flush:
            await self.updater.drain()
        return accepted


def dialog_ref_from_manager(manager: DialogManager) -> DialogRef:
    """Capture durable coordinates for the active Mattermost dialog post."""

    renderer = manager.renderer
    if not isinstance(renderer, MattermostRenderer):
        raise TypeError("Background jobs require a MattermostRenderer")
    post_id = manager.stack.last_post_id
    if post_id is None:
        raise RuntimeError("The active dialog has no rendered post")
    event: Any = manager.event
    team_id = getattr(event, "team_id", None)
    return DialogRef(
        channel_id=renderer.channel_id,
        actor_id=manager.actor_id,
        post_id=post_id,
        intent_id=manager.current_context().intent_id,
        thread_id=renderer.root_id,
        team_id=team_id if isinstance(team_id, str) and team_id else None,
    )


class EphemeralMemoryGameAdminJobRepository:
    """Process-local demo/test repository. Never use it as production persistence."""

    def __init__(self) -> None:
        self._jobs: dict[str, GameAdminJob] = {}
        self._terminal_ids: list[str] = []
        self._revisions: dict[str, list[GameAdminJob]] = {}
        self._lock = asyncio.Lock()

    async def create(self, job: GameAdminJob) -> None:
        if job.revision != 0 or job.state != "queued":
            raise ValueError("New game admin jobs must start queued at revision zero")
        async with self._lock:
            if job.id in self._jobs:
                raise ValueError(f"Game admin job {job.id!r} already exists")
            if job.single_flight_key is not None:
                existing = next(
                    (
                        current
                        for current in self._jobs.values()
                        if not current.terminal and current.single_flight_key == job.single_flight_key
                    ),
                    None,
                )
                if existing is not None:
                    raise ActiveJobConflict(existing)
            self._jobs[job.id] = job
            self._revisions[job.id] = [job]

    async def get(self, job_id: str) -> GameAdminJob | None:
        async with self._lock:
            return self._jobs.get(job_id)

    async def replace(self, job: GameAdminJob, *, expected_revision: int) -> None:
        async with self._lock:
            current = self._jobs.get(job.id)
            if current is None:
                raise KeyError(job.id)
            if current.revision != expected_revision or job.revision <= current.revision:
                raise JobRevisionConflict(f"Job {job.id!r} changed from revision {expected_revision}")
            if current.terminal:
                raise JobRevisionConflict(f"Terminal job {job.id!r} is immutable")
            self._jobs[job.id] = job
            self._revisions[job.id].append(job)
            if job.terminal:
                self._terminal_ids.insert(0, job.id)

    async def request_cancel(self, job_id: str) -> GameAdminJob:
        async with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                raise KeyError(job_id)
            if current.terminal or current.cancel_requested:
                return current
            state: GameAdminJobState = "cancelled" if current.state == "queued" else current.state
            timestamp = _now()
            updated = replace(
                current,
                revision=current.revision + 1,
                state=state,
                stage="cancelled" if state == "cancelled" else current.stage,
                cancel_requested=True,
                updated_at=timestamp,
                finished_at=timestamp if state == "cancelled" else current.finished_at,
            )
            self._jobs[job_id] = updated
            self._revisions[job_id].append(updated)
            if updated.terminal:
                self._terminal_ids.insert(0, updated.id)
            return updated

    async def terminal_history(self, actor_id: str) -> tuple[GameAdminJob, ...]:
        async with self._lock:
            return tuple(
                self._jobs[job_id] for job_id in self._terminal_ids if self._jobs[job_id].actor_id == actor_id
            )

    async def revisions(self, job_id: str) -> tuple[GameAdminJob, ...]:
        """Expose immutable snapshots for demo diagnostics and contract tests."""

        async with self._lock:
            return tuple(self._revisions.get(job_id, ()))


class EphemeralMemoryGameAdminJobQueue:
    """Process-local, non-durable queue for the owned local demo only."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    async def enqueue(self, job_id: str) -> None:
        await self._queue.put(job_id)

    async def dequeue(self) -> str:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()


class GameAdminJobs:
    """Authorize and enqueue background work without choosing infrastructure adapters."""

    def __init__(
        self,
        service: GameAccountsAdminService,
        repository: GameAdminJobRepository,
        queue: GameAdminJobQueue,
    ) -> None:
        self.service = service
        self.repository = repository
        self.queue = queue

    async def _enqueue(
        self,
        actor_id: str,
        dialog_ref: DialogRef,
        payload: GameAdminJobPayload,
        *,
        single_flight_key: str | None = None,
    ) -> GameAdminJob:
        self.service.require_operator(actor_id)
        if dialog_ref.actor_id != actor_id:
            raise PermissionError("A background job DialogRef must belong to its actor")
        timestamp = _now()
        job = GameAdminJob(
            id=_id("job"),
            actor_id=actor_id,
            dialog_ref=dialog_ref,
            payload=payload,
            created_at=timestamp,
            updated_at=timestamp,
            single_flight_key=single_flight_key,
        )
        await self.repository.create(job)
        await self.queue.enqueue(job.id)
        return job

    async def import_accounts(
        self,
        actor_id: str,
        dialog_ref: DialogRef,
        *,
        game: str,
        file_id: str,
    ) -> GameAdminJob:
        return await self._enqueue(actor_id, dialog_ref, ImportAccountsJob(game, file_id))

    async def check_all(self, actor_id: str, dialog_ref: DialogRef) -> GameAdminJob:
        return await self._enqueue(actor_id, dialog_ref, CheckAllAccountsJob())

    async def check_account(
        self,
        actor_id: str,
        dialog_ref: DialogRef,
        account_id: str,
    ) -> GameAdminJob:
        await self.service.account_card(actor_id, account_id)
        return await self._enqueue(
            actor_id,
            dialog_ref,
            CheckAccountJob(account_id),
            single_flight_key=f"account-check:{actor_id}:{account_id}",
        )

    async def check_import(
        self,
        actor_id: str,
        dialog_ref: DialogRef,
        import_id: str,
    ) -> GameAdminJob:
        return await self._enqueue(
            actor_id,
            dialog_ref,
            CheckImportedAccountsJob(import_id),
        )

    async def cancel(self, actor_id: str, job_id: str) -> GameAdminJob:
        self.service.require_operator(actor_id)
        job = await self.repository.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.actor_id != actor_id:
            raise PermissionError("Administrators may cancel only their own dialog jobs")
        return await self.repository.request_cancel(job_id)


class _JobCancelled(Exception):
    pass


def account_check_dialog_data(job: GameAdminJob) -> dict[str, object]:
    """Return JSON-compatible dialog state for one selected-account check."""

    if not isinstance(job.payload, CheckAccountJob):
        return {}
    state = {
        "queued": "pending",
        "running": "running",
        "completed": "success",
        "failed": "failed",
        "cancelled": "cancelled",
    }[job.state]
    result_failed = isinstance(job.result, CheckRun) and job.result.status == "failed"
    if result_failed:
        state = "failed"
    data: dict[str, object] = {
        "account_check_account_id": job.payload.account_id,
        "account_check_job_id": job.id,
        "account_check_state": state,
        "account_check_stage": job.stage,
        "account_check_current": job.current,
        "account_check_total": job.total,
        "account_check_started_at": job.started_at,
        "account_check_finished_at": job.finished_at,
        "account_check_error": (
            "Не удалось получить результат проверки. Повторите попытку."
            if job.state == "failed" or result_failed
            else None
        ),
    }
    if isinstance(job.result, CheckRun):
        data.update(
            account_check_valid=job.result.valid,
            account_check_invalid=job.result.invalid,
            account_check_errors=job.result.errors,
        )
    return data


class GameAdminJobRunner:
    """Execute queued imports/checks and publish ordered, actor-scoped progress."""

    def __init__(
        self,
        service: GameAccountsAdminService,
        repository: GameAdminJobRepository,
        updater: DialogJobUpdater,
    ) -> None:
        self.service = service
        self.repository = repository
        self.updater = updater

    @staticmethod
    def _dialog_data(job: GameAdminJob) -> dict[str, object]:
        data: dict[str, object] = {
            "job_id": job.id,
            "job_state": job.state,
            "job_stage": job.stage,
            "job_current": job.current,
            "job_total": job.total if job.total else None,
            "job_cancel_requested": job.cancel_requested,
            "job_error_type": job.error_type,
        }
        data.update(account_check_dialog_data(job))
        if isinstance(job.result, ImportRun):
            data.update(
                last_import_id=job.result.id,
                import_total=job.result.total,
                import_imported=job.result.imported,
                import_rejected=job.result.rejected,
                import_status=job.result.status,
                import_issues="; ".join(job.result.issues) if job.result.issues else "нет",
            )
        elif isinstance(job.result, CheckRun):
            data["check_summary"] = (
                f"Проверено {job.result.total}: valid {job.result.valid}, "
                f"invalid {job.result.invalid}, errors {job.result.errors}"
            )
        return data

    async def _persist(self, current: GameAdminJob, updated: GameAdminJob) -> GameAdminJob:
        await self.repository.replace(updated, expected_revision=current.revision)
        return updated

    async def _publish(self, job: GameAdminJob) -> GameAdminJob:
        if job.dialog_outdated:
            return job
        try:
            await self.updater.update(
                job.dialog_ref,
                self._dialog_data(job),
                revision=job.revision,
            )
            return job
        except OutdatedIntent:
            current = await self.repository.get(job.id)
            if current is None or current.terminal or current.dialog_outdated:
                return job if current is None else current
            return await self._persist(
                current,
                replace(
                    current,
                    revision=current.revision + 1,
                    updated_at=_now(),
                    dialog_outdated=True,
                ),
            )

    async def _finish(
        self,
        job_id: str,
        state: GameAdminJobState,
        *,
        result: GameAdminJobResult = None,
        error_type: str | None = None,
    ) -> GameAdminJob:
        current = await self.repository.get(job_id)
        if current is None:
            raise KeyError(job_id)
        if current.terminal:
            return current
        timestamp = _now()
        finished = await self._persist(
            current,
            replace(
                current,
                revision=current.revision + 1,
                updated_at=timestamp,
                finished_at=timestamp,
                state=state,
                stage=state,
                result=result,
                error_type=error_type,
            ),
        )
        return await self._publish(finished)

    async def run(self, job_id: str) -> GameAdminJob:
        job = await self.repository.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.terminal:
            return job
        if job.cancel_requested:
            return await self._finish(job.id, "cancelled")
        timestamp = _now()
        job = await self._persist(
            job,
            replace(
                job,
                revision=job.revision + 1,
                updated_at=timestamp,
                started_at=timestamp,
                state="running",
                stage="starting",
            ),
        )
        job = await self._publish(job)

        async def report(current: int, total: int, stage: str) -> None:
            snapshot = await self.repository.get(job_id)
            if snapshot is None:
                raise KeyError(job_id)
            if snapshot.cancel_requested:
                raise _JobCancelled
            updated = await self._persist(
                snapshot,
                replace(
                    snapshot,
                    revision=snapshot.revision + 1,
                    updated_at=_now(),
                    state="running",
                    current=current,
                    total=total,
                    stage=stage,
                ),
            )
            await self._publish(updated)

        try:
            payload = job.payload
            if isinstance(payload, ImportAccountsJob):
                result: GameAdminJobResult = await self.service.import_accounts(
                    job.actor_id,
                    game=payload.game,
                    file_id=payload.file_id,
                    progress=report,
                )
            elif isinstance(payload, CheckAccountJob):
                result = await self.service.check_account(
                    job.actor_id,
                    payload.account_id,
                    progress=report,
                )
            elif isinstance(payload, CheckAllAccountsJob):
                result = await self.service.check_all(job.actor_id, progress=report)
            else:
                result = await self.service.check_import(
                    job.actor_id,
                    payload.import_id,
                    progress=report,
                )
            snapshot = await self.repository.get(job_id)
            if snapshot is not None and snapshot.cancel_requested:
                raise _JobCancelled
        except _JobCancelled:
            return await self._finish(job_id, "cancelled")
        except Exception as error:
            return await self._finish(job_id, "failed", error_type=type(error).__name__)
        return await self._finish(job_id, "completed", result=result)


__all__ = [
    "ActiveJobConflict",
    "CheckAccountJob",
    "CheckAllAccountsJob",
    "CheckImportedAccountsJob",
    "CoalescingDialogJobUpdater",
    "DialogJobUpdater",
    "EphemeralMemoryGameAdminJobQueue",
    "EphemeralMemoryGameAdminJobRepository",
    "GameAdminJob",
    "GameAdminJobPayload",
    "GameAdminJobQueue",
    "GameAdminJobRepository",
    "GameAdminJobResult",
    "GameAdminJobRunner",
    "GameAdminJobState",
    "GameAdminJobs",
    "ImportAccountsJob",
    "JobRevisionConflict",
    "TERMINAL_JOB_STATES",
    "account_check_dialog_data",
    "dialog_ref_from_manager",
]
