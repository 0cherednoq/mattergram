from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from pydantic import SecretStr

from .errors import AdminInputError
from .models import (
    AccountCredentials,
    GameAccount,
    ImportRun,
    PushEvent,
    RunStatus,
    StoredCookie,
    _id,
    _now,
)
from .ports import ImportFileReader, OperationProgress
from .repository import MemoryAdminRepository

EmitPush = Callable[[PushEvent, str], Awaitable[None]]


def parse_cookies(value: Any, line: int) -> tuple[StoredCookie, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 500:
        raise ValueError(f"строка {line}: cookies должны быть массивом до 500 элементов")
    result: list[StoredCookie] = []
    for cookie in value:
        if not isinstance(cookie, dict):
            raise ValueError(f"строка {line}: cookie должен быть объектом")
        domain = cookie.get("domain")
        name = cookie.get("name")
        secret = cookie.get("value")
        if (
            not isinstance(domain, str)
            or not domain
            or not isinstance(name, str)
            or not name
            or not isinstance(secret, str)
            or not secret
        ):
            raise ValueError(f"строка {line}: cookie требует domain, name и value")
        result.append(StoredCookie(domain, name, SecretStr(secret)))
    return tuple(result)


async def import_accounts(
    repository: MemoryAdminRepository,
    *,
    games: Sequence[str],
    reader: ImportFileReader,
    emit: EmitPush,
    actor_id: str,
    game: str,
    file_id: str,
    progress: OperationProgress | None = None,
) -> ImportRun:
    """Parse and persist one bounded JSONL import after caller authorization."""

    if game not in games:
        raise AdminInputError({"game": "Неизвестная игра"})
    payload = await reader.read(file_id)
    if len(payload) > 1_000_000:
        raise AdminInputError({"import_file": "Файл превышает 1 МБ"})
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise AdminInputError({"import_file": "Файл должен быть UTF-8 JSONL"}) from None
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines or len(lines) > 500:
        raise AdminInputError({"import_file": "Файл должен содержать от 1 до 500 строк"})
    imported: list[str] = []
    issues: list[str] = []
    if progress is not None:
        await progress(0, len(lines), "parse")
    async with repository.lock:
        existing = {
            (account.game, account.credentials.login.casefold()) for account in repository.accounts.values()
        }
        for number, line in enumerate(lines, start=1):
            try:
                raw = json.loads(line)
                if not isinstance(raw, dict):
                    raise ValueError(f"строка {number}: ожидается JSON object")
                login = raw.get("login")
                password = raw.get("password")
                if not isinstance(login, str) or not login.strip():
                    raise ValueError(f"строка {number}: login обязателен")
                if not isinstance(password, str) or not password:
                    raise ValueError(f"строка {number}: password обязателен")
                key = (game, login.strip().casefold())
                if key in existing:
                    raise ValueError(f"строка {number}: такой login уже существует")
                cookies = parse_cookies(raw.get("cookies"), number)
                account = GameAccount(
                    _id("acc"),
                    game,
                    AccountCredentials(login.strip(), SecretStr(password), cookies),
                )
                repository.accounts[account.id] = account
                imported.append(account.id)
                existing.add(key)
            except (json.JSONDecodeError, ValueError) as error:
                issues.append(str(error).splitlines()[0])
            if progress is not None:
                await progress(number, len(lines), "parse")
        status: RunStatus = "completed" if not issues else "partial" if imported else "failed"
        run = ImportRun(
            _id("imp"),
            _now(),
            actor_id,
            game,
            len(lines),
            len(imported),
            len(issues),
            status,
            tuple(imported),
            tuple(issues[:20]),
        )
        repository.imports.insert(0, run)
    await emit(
        "import.completed" if imported else "import.failed",
        f"Импорт {run.id}: добавлено {run.imported}, отклонено {run.rejected}",
    )
    return run


__all__ = ["import_accounts", "parse_cookies"]
