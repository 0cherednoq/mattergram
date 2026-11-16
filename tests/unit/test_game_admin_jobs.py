from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from mattergram.contrib.game_accounts_admin import (
    ActiveJobConflict,
    AdminPanel,
    AdminUser,
    EphemeralMemoryGameAdminJobQueue,
    EphemeralMemoryGameAdminJobRepository,
    GameAccountsAdminService,
    GameAdminActions,
    GameAdminJob,
    GameAdminJobRunner,
    GameAdminJobs,
    ImportAccountsJob,
    JobRevisionConflict,
    MemoryAdminRepository,
    TeamUser,
    VerificationResult,
)
from mattergram.contrib.game_accounts_admin import actions as game_admin_actions
from mattergram_dialog import DialogRef, ShowMode
from mattergram_dialog.api.exceptions import OutdatedIntent


class Reader:
    async def read(self, file_id: str) -> bytes:
        assert file_id == "file-1"
        return b'{"login":"alice","password":"secret"}\n'


class Verifier:
    async def verify(self, game: str, credentials: Any) -> VerificationResult:
        del game, credentials
        return VerificationResult(valid=True, summary="accepted")


class BlockingVerifier:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def verify(self, game: str, credentials: Any) -> VerificationResult:
        del game, credentials
        self.started.set()
        await self.release.wait()
        return VerificationResult(valid=True, summary="accepted")


class Pushes:
    async def send(self, rule: Any, event: Any, text: str) -> None:
        del rule, event, text


class Users:
    async def list_users(self) -> tuple[TeamUser, ...]:
        return (TeamUser("admin", "admin"),)


class RecordingUpdater:
    def __init__(self, *, outdated: bool = False) -> None:
        self.outdated = outdated
        self.updates: list[tuple[DialogRef, dict[str, object], int]] = []

    async def update(
        self,
        ref: DialogRef,
        data: dict[str, object] | None = None,
        *,
        revision: int | None = None,
    ) -> bool:
        assert data is not None
        assert revision is not None
        self.updates.append((ref, data, revision))
        if self.outdated:
            raise OutdatedIntent("deleted post")
        return True


def dialog_ref() -> DialogRef:
    return DialogRef("channel", "admin", "post", "intent")


def service(*, verifier: Any | None = None) -> GameAccountsAdminService:
    return GameAccountsAdminService(
        MemoryAdminRepository(users=(AdminUser("user", "admin", "admin", "admin"),)),
        games=("Game",),
        verifier=verifier or Verifier(),
        import_reader=Reader(),
        push_dispatcher=Pushes(),
        user_directory=Users(),
    )


class ActionManager:
    actor_id = "admin"

    def __init__(self) -> None:
        self.dialog_data: dict[str, object] = {}
        self.transition: tuple[Any, ShowMode] | None = None

    async def switch_to(self, state: Any, *, show_mode: ShowMode) -> None:
        self.transition = (state, show_mode)


@pytest.mark.asyncio
async def test_memory_job_repository_enforces_monotonic_revisions_and_terminal_history() -> None:
    repository = EphemeralMemoryGameAdminJobRepository()
    job = GameAdminJob(
        "job-1",
        "admin",
        dialog_ref(),
        ImportAccountsJob("Game", "file-1"),
        "created",
        "created",
    )
    await repository.create(job)

    with pytest.raises(JobRevisionConflict):
        await repository.replace(replace(job, stage="duplicate"), expected_revision=0)

    cancelled = await repository.request_cancel(job.id)
    assert cancelled.state == "cancelled"
    assert cancelled.revision == 1
    assert await repository.request_cancel(job.id) is cancelled
    assert await repository.terminal_history("admin") == (cancelled,)
    assert [snapshot.revision for snapshot in await repository.revisions(job.id)] == [0, 1]

    with pytest.raises(JobRevisionConflict, match="Terminal job"):
        await repository.replace(
            replace(cancelled, revision=2, stage="mutated"),
            expected_revision=1,
        )


@pytest.mark.asyncio
async def test_import_job_reports_ordered_progress_and_records_terminal_result() -> None:
    admin = service()
    repository = EphemeralMemoryGameAdminJobRepository()
    queue = EphemeralMemoryGameAdminJobQueue()
    jobs = GameAdminJobs(admin, repository, queue)
    updater = RecordingUpdater()
    runner = GameAdminJobRunner(admin, repository, updater)

    queued = await jobs.import_accounts(
        "admin",
        dialog_ref(),
        game="Game",
        file_id="file-1",
    )
    assert await queue.dequeue() == queued.id
    completed = await runner.run(queued.id)

    assert completed.state == "completed"
    assert completed.result is not None
    assert completed.result.total == 1
    assert completed.result.imported == 1
    revisions = [snapshot.revision for snapshot in await repository.revisions(queued.id)]
    assert revisions == sorted(set(revisions))
    assert [revision for _, _, revision in updater.updates] == sorted(
        revision for _, _, revision in updater.updates
    )
    assert await repository.terminal_history("admin") == (completed,)


@pytest.mark.asyncio
async def test_selected_account_check_is_single_flight_and_publishes_factual_lifecycle() -> None:
    admin = service()
    imported = await admin.import_accounts("admin", game="Game", file_id="file-1")
    account_id = imported.account_ids[0]
    repository = EphemeralMemoryGameAdminJobRepository()
    queue = EphemeralMemoryGameAdminJobQueue()
    jobs = GameAdminJobs(admin, repository, queue)
    updater = RecordingUpdater()
    runner = GameAdminJobRunner(admin, repository, updater)

    queued = await jobs.check_account("admin", dialog_ref(), account_id)
    with pytest.raises(ActiveJobConflict) as conflict:
        await jobs.check_account("admin", dialog_ref(), account_id)
    assert conflict.value.job.id == queued.id
    assert await queue.dequeue() == queued.id

    completed = await runner.run(queued.id)

    assert completed.state == "completed"
    assert completed.started_at is not None
    assert completed.finished_at is not None
    assert completed.result is not None
    assert (completed.result.valid, completed.result.invalid, completed.result.errors) == (1, 0, 0)
    snapshots = [data for _, data, _ in updater.updates]
    assert [snapshot["account_check_state"] for snapshot in snapshots] == [
        "running",
        "running",
        "running",
        "success",
    ]
    assert snapshots[1]["account_check_total"] == 1
    assert snapshots[-1]["account_check_valid"] == 1
    assert snapshots[-1]["account_check_error"] is None

    next_job = await jobs.check_account("admin", dialog_ref(), account_id)
    assert next_job.id != queued.id


@pytest.mark.asyncio
async def test_batch_action_enqueues_and_switches_to_declarative_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = service()
    repository = EphemeralMemoryGameAdminJobRepository()
    queue = EphemeralMemoryGameAdminJobQueue()
    jobs = GameAdminJobs(admin, repository, queue)
    manager = ActionManager()
    monkeypatch.setattr(game_admin_actions, "dialog_ref_from_manager", lambda manager: dialog_ref())

    await GameAdminActions(admin, jobs).check_all(manager)  # type: ignore[arg-type]

    job_id = manager.dialog_data["job_id"]
    assert await queue.dequeue() == job_id
    assert manager.dialog_data["job_state"] == "queued"
    assert manager.transition == (AdminPanel.job_progress, ShowMode.NO_UPDATE)


@pytest.mark.asyncio
async def test_running_batch_check_observes_cancellation_and_keeps_terminal_history() -> None:
    verifier = BlockingVerifier()
    admin = service(verifier=verifier)
    await admin.import_accounts("admin", game="Game", file_id="file-1")
    repository = EphemeralMemoryGameAdminJobRepository()
    queue = EphemeralMemoryGameAdminJobQueue()
    jobs = GameAdminJobs(admin, repository, queue)
    runner = GameAdminJobRunner(admin, repository, RecordingUpdater())
    queued = await jobs.check_all("admin", dialog_ref())

    run_task = asyncio.create_task(runner.run(queued.id))
    await verifier.started.wait()
    requested = await jobs.cancel("admin", queued.id)
    assert requested.cancel_requested is True
    verifier.release.set()
    cancelled = await run_task

    assert cancelled.state == "cancelled"
    assert cancelled.cancel_requested is True
    assert await repository.terminal_history("admin") == (cancelled,)
    revisions = [snapshot.revision for snapshot in await repository.revisions(queued.id)]
    assert revisions == sorted(set(revisions))


@pytest.mark.asyncio
async def test_outdated_dialog_ref_detaches_ui_without_losing_import_job() -> None:
    admin = service()
    repository = EphemeralMemoryGameAdminJobRepository()
    queue = EphemeralMemoryGameAdminJobQueue()
    jobs = GameAdminJobs(admin, repository, queue)
    updater = RecordingUpdater(outdated=True)
    runner = GameAdminJobRunner(admin, repository, updater)
    queued = await jobs.import_accounts(
        "admin",
        dialog_ref(),
        game="Game",
        file_id="file-1",
    )

    completed = await runner.run(queued.id)

    assert completed.state == "completed"
    assert completed.dialog_outdated is True
    assert len(updater.updates) == 1
    assert await repository.terminal_history("admin") == (completed,)
