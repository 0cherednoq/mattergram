from __future__ import annotations

from collections.abc import Sequence

from .imports import EmitPush
from .models import (
    AccountCredentials,
    AccountStatus,
    CheckRun,
    RunStatus,
    _id,
    _now,
)
from .ports import AccountVerifier, OperationProgress
from .repository import MemoryAdminRepository


def redact_summary(summary: str, credentials: AccountCredentials) -> str:
    redacted = summary
    secrets = (credentials.password.get_secret_value(),) + tuple(
        cookie.value.get_secret_value() for cookie in credentials.cookies
    )
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        redacted = redacted.replace(secret, "[secret]")
    return redacted


async def check_ids(
    repository: MemoryAdminRepository,
    *,
    verifier: AccountVerifier,
    emit: EmitPush,
    actor_id: str,
    account_ids: Sequence[str],
    scope: str,
    progress: OperationProgress | None = None,
) -> CheckRun:
    """Verify a stable credential snapshot and commit only if it is still current."""

    async with repository.lock:
        accounts = [
            (
                repository.accounts[account_id].id,
                repository.accounts[account_id].game,
                repository.accounts[account_id].credentials,
                repository.accounts[account_id].credentials.login,
            )
            for account_id in account_ids
            if account_id in repository.accounts
        ]
    valid = invalid = errors = 0
    if progress is not None:
        await progress(0, len(accounts), "verify")
    for index, (account_id, game, credentials, login) in enumerate(accounts, start=1):
        last_error: str | None
        try:
            result = await verifier.verify(game, credentials)
        except Exception as error:  # verifier boundary; secret values never enter the message
            account_status: AccountStatus = "error"
            last_error = type(error).__name__
            outcome = "error"
            await emit(
                "account.check_failed",
                f"Ошибка проверки {game}/{login}",
            )
        else:
            account_status = "valid" if result.valid else "invalid"
            last_error = None if result.valid else redact_summary(result.summary, credentials)[:150]
            outcome = "valid" if result.valid else "invalid"
            if not result.valid:
                await emit(
                    "account.invalid",
                    f"Невалидный аккаунт {game}/{login}",
                )
        async with repository.lock:
            account = repository.accounts.get(account_id)
            if account is None or account.credentials != credentials:
                errors += 1
                continue
            account.status = account_status
            account.last_error = last_error
            account.last_checked_at = _now()
        if outcome == "valid":
            valid += 1
        elif outcome == "invalid":
            invalid += 1
        else:
            errors += 1
        if progress is not None:
            await progress(index, len(accounts), "verify")
    run_status: RunStatus = (
        "failed" if accounts and errors == len(accounts) else "partial" if errors else "completed"
    )
    run = CheckRun(
        _id("chk"),
        _now(),
        actor_id,
        scope,
        len(accounts),
        valid,
        invalid,
        errors,
        run_status,
    )
    async with repository.lock:
        repository.checks.insert(0, run)
    return run


__all__ = ["check_ids", "redact_summary"]
