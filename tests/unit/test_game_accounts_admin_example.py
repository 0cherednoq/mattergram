from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from examples.game_accounts_admin import (
    GAME_ADMIN_KEYBOARD_ROUTES,
    AdminInputError,
    AdminPanel,
    AdminUser,
    GameAccountsAdminService,
    MemoryAdminRepository,
    PushRule,
    TeamUser,
    VerificationResult,
    build_admin_dialog,
    build_admin_keyboard,
    game_admin_router,
)
from mattergram.fsm.storage.base import StorageKey
from mattergram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from mattergram_dialog import DialogManager, DialogRegistry, DialogStorage, ShowMode
from mattergram_dialog.rendering import FormRender, PostRender, RenderPlan


class Reader:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    async def read(self, file_id: str) -> bytes:
        return self.files[file_id]


class Verifier:
    async def verify(self, game: str, credentials: Any) -> VerificationResult:
        del game
        return VerificationResult(
            valid=not credentials.login.startswith("bad"),
            summary=("accepted" if not credentials.login.startswith("bad") else "expired secret-B token-B"),
        )


class BlockingVerifier:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def verify(self, game: str, credentials: Any) -> VerificationResult:
        del game, credentials
        self.started.set()
        await self.release.wait()
        return VerificationResult(valid=False, summary="stale result")


class Pushes:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send(self, rule: PushRule, event: str, text: str) -> None:
        self.sent.append((rule.destination_id, event, text))


class Users:
    async def list_users(self) -> tuple[TeamUser, ...]:
        return (
            TeamUser("human", "root", "Root Admin"),
            TeamUser("alice-id", "alice", "Alice Example"),
            TeamUser("bob-id", "bob"),
            TeamUser("bot-id", "gamebot", is_bot=True),
        )


@dataclass
class SpyRenderer:
    calls: list[tuple[RenderPlan, ShowMode]]

    async def render(self, plan: RenderPlan, stack: Any, mode: ShowMode) -> None:
        self.calls.append((plan, mode))
        if isinstance(plan, PostRender):
            stack.last_post_id = stack.last_post_id or "post-admin"


def service() -> tuple[GameAccountsAdminService, Pushes]:
    repository = MemoryAdminRepository(users=(AdminUser("usr_admin", "human", "root", "admin"),))
    pushes = Pushes()
    payload = b"\n".join(
        (
            b'{"login":"alice","password":"secret-A","cookies":[{"domain":"game.example","name":"sid","value":"token-A"}]}',
            b'{"login":"bad-bob","password":"secret-B","cookies":[{"domain":"auth.example","name":"sid","value":"token-B"},{"domain":"game.example","name":"pref","value":"token-C"}]}',
            b'{"login":"missing-password"}',
        )
    )
    return (
        GameAccountsAdminService(
            repository,
            games=("ExampleGame", "SecondGame"),
            verifier=Verifier(),
            import_reader=Reader({"file-1": payload}),
            push_dispatcher=pushes,
            user_directory=Users(),
        ),
        pushes,
    )


@pytest.mark.asyncio
async def test_admin_service_import_check_push_and_secret_boundary() -> None:
    admin, pushes = service()
    await admin.save_push_rule(
        "human",
        rule_id=None,
        destination_kind="channel",
        destination_id="alerts-channel",
        events=("import.completed", "account.invalid"),
        enabled=True,
    )

    imported = await admin.import_accounts("human", game="ExampleGame", file_id="file-1")

    assert imported.total == 3
    assert imported.imported == 2
    assert imported.rejected == 1
    assert imported.status == "partial"
    accounts = await admin.list_accounts("human")
    assert accounts == [
        {
            "id": accounts[0]["id"],
            "game": "ExampleGame",
            "login": "alice",
            "status": "unchecked",
            "filter": "unchecked",
            "password_state": "сохранён",
            "cookie_count": 1,
            "cookie_domains": "game.example",
            "last_checked_at": "не проверялся",
            "last_error": "нет",
        },
        {
            "id": accounts[1]["id"],
            "game": "ExampleGame",
            "login": "bad-bob",
            "status": "unchecked",
            "filter": "unchecked",
            "password_state": "сохранён",
            "cookie_count": 2,
            "cookie_domains": "auth.example, game.example",
            "last_checked_at": "не проверялся",
            "last_error": "нет",
        },
    ]
    assert "secret-A" not in repr(admin.repository.accounts)
    assert "token-A" not in repr(admin.repository.accounts)
    assert pushes.sent[0][1] == "import.completed"

    checked = await admin.check_import("human", imported.id)

    assert (checked.total, checked.valid, checked.invalid, checked.errors) == (2, 1, 1, 0)
    assert any(event == "account.invalid" for _, event, _ in pushes.sent)
    history = await admin.list_checks("human")
    assert history[0]["scope"] == f"import:{imported.id}"
    checked_accounts = await admin.list_accounts("human")
    assert checked_accounts[1]["last_error"] == "expired [secret] [secret]"


@pytest.mark.asyncio
async def test_admin_access_and_last_admin_invariant() -> None:
    admin, _ = service()

    with pytest.raises(PermissionError, match="enabled admin"):
        await admin.list_accounts("outsider")
    with pytest.raises(AdminInputError) as error:
        await admin.update_user(
            "human",
            "usr_admin",
            username="root",
            role="viewer",
            enabled=False,
        )
    assert "последнего администратора" in error.value.field_errors["enabled"]


@pytest.mark.asyncio
async def test_admin_adds_only_current_human_team_members_and_derives_username() -> None:
    admin, _ = service()

    created = await admin.add_user(
        "human",
        mattermost_user_id="alice-id",
        role="operator",
        enabled=True,
    )

    assert created.mattermost_user_id == "alice-id"
    assert created.username == "alice"
    assert [user.id for user in await admin.list_available_team_users("human")] == ["bob-id"]
    for unavailable in ("alice-id", "bot-id", "outside-team"):
        with pytest.raises(AdminInputError) as error:
            await admin.add_user(
                "human",
                mattermost_user_id=unavailable,
                role="viewer",
                enabled=True,
            )
        assert "mattermost_user_id" in error.value.field_errors


@pytest.mark.asyncio
async def test_changed_credentials_reject_inflight_checker_result() -> None:
    admin, _ = service()
    imported = await admin.import_accounts("human", game="ExampleGame", file_id="file-1")
    verifier = BlockingVerifier()
    admin.verifier = verifier

    pending = asyncio.create_task(admin.check_account("human", imported.account_ids[0]))
    await verifier.started.wait()
    await admin.update_account(
        "human",
        imported.account_ids[0],
        login="alice",
        password="replacement",
        cookies_json="",
    )
    verifier.release.set()
    checked = await pending

    assert (checked.valid, checked.invalid, checked.errors) == (0, 0, 1)
    accounts = await admin.list_accounts("human")
    assert accounts[0]["status"] == "unchecked"
    assert accounts[0]["last_error"] == "нет"


@pytest.mark.asyncio
async def test_admin_dialog_click_budget_rows_and_masked_account_card() -> None:
    admin, _ = service()
    imported = await admin.import_accounts("human", game="ExampleGame", file_id="file-1")
    storage = MemoryStorage()
    isolation = SimpleEventIsolation()
    renderer = SpyRenderer([])
    dialog = build_admin_dialog(admin)
    key = StorageKey(
        instance_id="https://instance.test",
        bot_id="bot",
        channel_id="channel",
        user_id="human",
        destiny="mattergram_dialog",
    )

    try:
        async with DialogManager(
            DialogStorage(storage, isolation),
            key,
            DialogRegistry(dialog),
            renderer,
            actor_id="human",
        ) as manager:
            await manager.start(AdminPanel.home)
            home = renderer.calls[-1][0]
            assert isinstance(home, PostRender)
            assert len(home.control_groups) == 1
            assert [control.id for control in home.control_groups[0].controls] == ["adminHomeRefresh"]
            assert "keyboard-меню" in home.text

            keyboard = build_admin_keyboard()
            assert [len(row) for row in keyboard.rows] == [3, 2, 2]
            assert keyboard.rows[0][0].message == "Обзор"
            assert keyboard.rows[0][0].style == "primary"
            assert {button.message for row in keyboard.rows for button in row} == {
                "Обзор",
                "Аккаунты",
                "Импорт",
                "Пользователи",
                "Push-настройки",
                "История импортов",
                "История проверок",
            }
            assert [handler.callback.__name__ for handler in game_admin_router.slash_command.handlers] == [
                "open_game_admin"
            ]
            assert [handler.callback.__name__ for handler in game_admin_router.message.handlers] == [
                "respond_to_game_admin_keyboard"
            ]
            assert "Пользователи" not in GAME_ADMIN_KEYBOARD_ROUTES

            await manager.switch_to(AdminPanel.accounts)
            accounts = renderer.calls[-1][0]
            assert isinstance(accounts, PostRender)
            assert [len(row) for row in accounts.control_groups[0].control_rows] == [5, 6]
            assert len(accounts.control_groups[1].controls) == 3

            selector = manager.find("adminAccountSelect")
            selector.apply(imported.account_ids[0], manager)
            await manager.show(ShowMode.EDIT)
            card = renderer.calls[-1][0]
            assert isinstance(card, PostRender)
            assert [len(row) for row in card.control_groups[0].control_rows] == [5, 6, 2]
            detail = card.control_groups[0].detail_text
            assert detail is not None
            assert "Пароль:** сохранён (значение скрыто)" in detail
            assert "Cookies:** 1 · домены: game.example" in detail
            assert "secret-A" not in repr(card)
            assert "token-A" not in repr(card)

            await manager.switch_to(AdminPanel.user_add)
            add_form = renderer.calls[-1][0]
            assert isinstance(add_form, FormRender)
            assert [field.name for field in add_form.form.elements] == [
                "mattermost_user_id",
                "role",
                "enabled",
            ]
            picker = add_form.form.elements[0]
            assert [(option.text, option.value) for option in picker.options] == [
                ("@alice — Alice Example", "alice-id"),
                ("@bob", "bob-id"),
            ]

            manager.dialog_data["selected_user_id"] = "usr_admin"
            await manager.switch_to(AdminPanel.user_edit)
            form = renderer.calls[-1][0]
            assert isinstance(form, FormRender)
            assert form.form.elements[0].default == "root"
            assert form.form.elements[1].default == "admin"
            assert form.form.elements[2].default is True
    finally:
        await storage.close()
        await isolation.close()
