from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from mattergram.contrib import game_accounts_admin as extracted_game_admin
from mattergram.contrib.game_accounts_admin import (
    GAME_ADMIN_KEYBOARD_ROUTES,
    AccountCheckData,
    AccountCredentials,
    AccountEnvironment,
    AccountListQuery,
    AdminInputError,
    AdminPanel,
    AdminUser,
    GameAccount,
    GameAccountsAdminService,
    MemoryAdminRepository,
    PushRule,
    StoredCookie,
    TeamUser,
    VerificationResult,
    build_account_check_blocks,
    build_admin_dialog,
    build_admin_keyboard,
    build_operator_keyboard,
    game_admin_router,
)
from mattergram.contrib.game_accounts_admin.mattermost_ui import AccountCard, AccountList
from mattergram.contrib.game_accounts_admin.router import (
    ACCESS_DENIED_TEXT,
    GameAdminHandlerContext,
    open_game_admin,
    respond_to_game_admin_keyboard,
)
from mattergram.fsm.storage.base import StorageKey
from mattergram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from mattergram.http import CallbackSigner
from mattergram.security import REQUIRED_ROLES_FLAG, RBACMiddleware
from mattergram.ui.actions import ActionContext
from mattergram.ui.blocks import compile_post_ui
from mattergram.ui.capabilities import UIProfile
from mattergram_dialog import (
    DialogManager,
    DialogRegistry,
    DialogStorage,
    OpenForm,
    ShowMode,
    WidgetAction,
)
from mattergram_dialog.bindings import OpenFormActions
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


class AllowCredentials:
    async def allows(self, actor_id: str, channel_id: str) -> bool:
        return actor_id == "human" and channel_id == "channel"


class DeniedEvent:
    def __init__(self, text: str) -> None:
        self.user_id = "outsider"
        self.text = text
        self.answers: list[str] = []

    async def answer(self, text: str = "") -> None:
        self.answers.append(text)


class UnusedRuntime:
    async def start(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("denied slash command must not start a dialog")

    async def respond(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("denied keyboard command must not create a post")


@dataclass
class SpyRenderer:
    calls: list[tuple[RenderPlan, ShowMode]]

    async def render(self, plan: RenderPlan, stack: Any, mode: ShowMode) -> None:
        self.calls.append((plan, mode))
        if isinstance(plan, PostRender):
            stack.last_post_id = stack.last_post_id or "post-admin"


@pytest.mark.parametrize(
    ("state", "label"),
    (
        ("pending", "Ожидает запуска"),
        ("running", "Выполняется"),
        ("success", "Успешно завершена"),
        ("failed", "Ошибка"),
        ("cancelled", "Отменена"),
    ),
)
def test_account_check_component_renders_every_state_without_fabricated_progress(
    state: Any,
    label: str,
) -> None:
    check = AccountCheckData(
        account_id="acc-1",
        job_id="job-1",
        state=state,
        stage="verify" if state == "running" else state,
        current=0,
        total=None,
        error="Проверка завершилась с ошибкой. Повторите попытку." if state == "failed" else None,
    )

    rendered = repr(build_account_check_blocks(check))
    embedded = repr(build_account_check_blocks(check, embedded=True))

    assert label in rendered
    assert "Общий объём пока неизвестен" in rendered
    assert "%" not in rendered
    assert "### Проверка" in embedded
    assert check.account_id not in embedded


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
            credential_reveal_policy=AllowCredentials(),
        ),
        pushes,
    )


@pytest.mark.asyncio
async def test_account_list_query_filters_sorts_and_pages_on_backend() -> None:
    admin, _ = service()
    for index in range(12):
        cookies = tuple(
            StoredCookie("example.com", f"cookie-{cookie}", SecretStr("value")) for cookie in range(index % 4)
        )
        admin.repository.accounts[f"account-{index:02}"] = GameAccount(
            f"account-{index:02}",
            "ExampleGame",
            AccountCredentials(
                f"user-{index:02}@example.com",
                SecretStr("password" if index % 2 else ""),
                cookies,
            ),
            environment=AccountEnvironment(country=f"Country {index:02}"),
            created_at=f"2026-01-{index + 1:02}T00:00:00+00:00",
        )

    second_page = await admin.query_accounts(
        "human",
        AccountListQuery(page=1, page_size=10, sort="oldest"),
    )
    assert second_page.total == 12
    assert second_page.pages == 2
    assert [item.id for item in second_page.items] == ["account-10", "account-11"]

    credentials = await admin.query_accounts(
        "human",
        AccountListQuery(filter="credentials", sort="newest"),
    )
    assert credentials.total == 6
    assert all(item.has_credentials for item in credentials.items)

    with_cookies = await admin.query_accounts(
        "human",
        AccountListQuery(filter="cookies", sort="cookies_desc"),
    )
    assert with_cookies.total == 9
    assert [item.cookie_count for item in with_cookies.items] == sorted(
        (item.cookie_count for item in with_cookies.items),
        reverse=True,
    )


@pytest.mark.asyncio
async def test_account_delete_requires_operator_and_removes_the_selected_record() -> None:
    admin, _ = service()
    imported = await admin.import_accounts("human", game="ExampleGame", file_id="file-1")
    account_id = imported.account_ids[0]

    with pytest.raises(PermissionError, match="admin or operator"):
        await admin.delete_account("outsider", account_id)
    deleted = await admin.delete_account("human", account_id)

    assert deleted.id == account_id
    assert account_id not in admin.repository.accounts
    with pytest.raises(AdminInputError) as missing:
        await admin.delete_account("human", account_id)
    assert missing.value.field_errors == {"account": "Аккаунт больше не существует"}


@pytest.mark.asyncio
async def test_extracted_getters_and_actions_work_without_http_ingress() -> None:
    admin, _ = service()
    imported = await admin.import_accounts("human", game="ExampleGame", file_id="file-1")
    manager = SimpleNamespace(
        actor_id="human",
        dialog_data={"selected_account_id": imported.account_ids[0]},
        key=SimpleNamespace(channel_id="channel"),
    )
    account_list = AccountList(
        id="adminAccountList",
        page="account_page",
    )
    context = SimpleNamespace(widget_data={})
    manager.find = lambda widget_id: account_list if widget_id == "adminAccountList" else None
    manager.current_context = lambda: context

    getters = extracted_game_admin.GameAdminGetters(admin)
    actions = extracted_game_admin.GameAdminActions(admin)
    accounts = await getters.accounts(manager)
    await actions.check_selected(manager)

    assert {item.login for item in accounts["account_page"].items} == {"alice", "bad-bob"}
    assert manager.dialog_data["check_summary"].startswith("Проверка: действительных 1")
    assert GameAccountsAdminService is extracted_game_admin.GameAccountsAdminService
    assert build_admin_dialog is extracted_game_admin.build_admin_dialog
    assert extracted_game_admin.GameAdminGetters.__module__.endswith(".getters")
    assert extracted_game_admin.GameAdminActions.__module__.endswith(".actions")
    assert extracted_game_admin.build_admin_dialog.__module__.endswith(".dialog")
    assert extracted_game_admin.register_admin_panel.__module__.endswith(".router")


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
            "status_label": "Не проверялся",
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
            "status_label": "Не проверялся",
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
    safe_card = await admin.account_card("human", accounts[0]["id"])
    assert safe_card.has_password is True
    assert [(cookie.domain, cookie.name) for cookie in safe_card.cookies] == [("game.example", "sid")]
    assert "secret-A" not in repr(safe_card)
    assert "token-A" not in repr(safe_card)
    assert pushes.sent[0][1] == "import.completed"

    checked = await admin.check_import("human", imported.id)

    assert (checked.total, checked.valid, checked.invalid, checked.errors) == (2, 1, 1, 0)
    assert any(event == "account.invalid" for _, event, _ in pushes.sent)
    history = await admin.list_checks("human")
    assert history[0]["scope"] == f"Импорт {imported.id}"
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
async def test_operator_can_manage_game_accounts_but_not_application_users() -> None:
    admin, _ = service()
    admin.repository.users["usr_operator"] = AdminUser(
        "usr_operator",
        "alice-id",
        "alice",
        "operator",
    )

    assert await admin.dashboard("alice-id") == {
        "users": 2,
        "push_rules": 0,
        "accounts": 0,
        "invalid_accounts": 0,
    }
    assert await admin.list_accounts("alice-id") == []
    assert await admin.list_push_rules("alice-id") == []
    assert await admin.list_imports("alice-id") == []
    assert await admin.list_checks("alice-id") == []
    with pytest.raises(PermissionError, match="enabled admin"):
        await admin.list_users("alice-id")
    with pytest.raises(PermissionError, match="enabled admin"):
        await admin.add_user(
            "alice-id",
            mattermost_user_id="bob-id",
            role="viewer",
            enabled=True,
        )


@pytest.mark.asyncio
async def test_denied_global_entries_answer_without_crashing_dispatcher() -> None:
    admin, _ = service()
    context = GameAdminHandlerContext(UnusedRuntime(), admin)  # type: ignore[arg-type]
    slash = DeniedEvent("game-admin")
    message = DeniedEvent("Обзор")

    async def invoke_slash(event: Any, data: dict[str, Any]) -> None:
        del data
        await open_game_admin(event, context)

    async def invoke_message(event: Any, data: dict[str, Any]) -> None:
        del data
        await respond_to_game_admin_keyboard(event, context)

    class ProtectedHandler:
        flags = {REQUIRED_ROLES_FLAG: ("admin", "operator")}

    middleware = RBACMiddleware(admin, denied_text=ACCESS_DENIED_TEXT)

    await middleware(invoke_slash, slash, {"handler": ProtectedHandler()})  # type: ignore[arg-type]
    await middleware(invoke_message, message, {"handler": ProtectedHandler()})  # type: ignore[arg-type]

    assert slash.answers == [ACCESS_DENIED_TEXT]
    assert message.answers == [ACCESS_DENIED_TEXT]


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
async def test_admin_dialog_click_budget_rows_and_account_card() -> None:
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
            assert len(home.control_groups) == 2
            assert [control.id for control in home.control_groups[0].controls] == ["adminHomeRefresh"]
            assert [control.id for control in home.control_groups[1].controls] == [
                "adminHomeImport",
                "adminHomePushes",
                "adminHomeImports",
                "adminHomeChecks",
            ]
            assert "keyboard-меню" in home.text

            keyboard = build_admin_keyboard()
            assert [len(row) for row in keyboard.rows] == [3]
            assert keyboard.rows[0][0].message == "Обзор"
            assert keyboard.rows[0][0].style == "primary"
            assert {button.message for row in keyboard.rows for button in row} == {
                "Обзор",
                "Аккаунты",
                "Пользователи",
            }
            operator_keyboard = build_operator_keyboard()
            assert [len(row) for row in operator_keyboard.rows] == [2]
            assert {button.message for row in operator_keyboard.rows for button in row} == {
                "Обзор",
                "Аккаунты",
            }
            assert [handler.callback.__name__ for handler in game_admin_router.slash_command.handlers] == [
                "open_game_admin"
            ]
            assert [handler.callback.__name__ for handler in game_admin_router.message.handlers] == [
                "respond_to_game_admin_keyboard"
            ]
            assert game_admin_router.slash_command.handlers[0].flags[REQUIRED_ROLES_FLAG] == (
                "admin",
                "operator",
            )
            assert game_admin_router.message.handlers[0].flags[REQUIRED_ROLES_FLAG] == (
                "admin",
                "operator",
            )
            assert "Пользователи" not in GAME_ADMIN_KEYBOARD_ROUTES
            assert GAME_ADMIN_KEYBOARD_ROUTES == {
                "Обзор": AdminPanel.home,
                "Аккаунты": AdminPanel.accounts,
            }

            await manager.switch_to(AdminPanel.accounts)
            accounts = renderer.calls[-1][0]
            assert isinstance(accounts, PostRender)
            assert len(accounts.control_groups[0].controls) == 10
            assert [control.label for control in accounts.control_groups[0].controls[:3]] == [
                "Все",
                "Креды",
                "Cookies",
            ]
            assert [control.style for control in accounts.control_groups[0].controls[:3]] == [
                "primary",
                None,
                None,
            ]
            assert accounts.control_groups[0].native_control_blocks is not None
            action_context = ActionContext(
                signer=CallbackSigner(
                    instance_id="https://instance.test",
                    bot_id="bot",
                    keys={"key": b"x" * 32},
                    active_key_id="key",
                ),
                channel_id="channel",
                user_id="human",
            )
            profile = UIProfile(
                "12.0.2",
                client="test",
                client_features=frozenset({"blocks"}),
                client_evidence="unit test",
                mm_blocks_enabled=True,
            )
            bound = OpenFormActions(
                "https://app.test/callback",
                action_context,
                ui_profile=profile,
            )(accounts.control_groups)
            accounts_message, accounts_blocks = accounts.compose_native(bound.blocks)
            props = compile_post_ui(
                blocks=accounts_blocks,
                profile=bound.profile,
                context=bound.context,
                message=accounts_message,
            )
            assert accounts_message == ""
            assert len(props["mm_blocks_actions"]) == 13
            assert len(props["mm_blocks"]) == 1
            assert props["mm_blocks"][0]["type"] == "container"
            assert props["mm_blocks"][0]["border"] is True
            assert props["mm_blocks"][0]["background"] == "gray"
            assert props["mm_blocks"][0]["accent_color"] == "primary"
            assert "Игровые аккаунты" in props["mm_blocks"][0]["content"][0]["text"]
            nested_blocks: list[dict[str, object]] = []

            def collect_blocks(block: dict[str, object]) -> None:
                nested_blocks.append(block)
                for children in block.values():
                    if isinstance(children, list):
                        for child in children:
                            if isinstance(child, dict):
                                collect_blocks(child)

            collect_blocks(props["mm_blocks"][0])
            assert [block for block in nested_blocks if block.get("accent_color") == "primary"] == [
                props["mm_blocks"][0]
            ]
            assert [block for block in nested_blocks if block.get("border") is True] == [
                props["mm_blocks"][0]
            ]
            assert "**Фильтрация**" in repr(props["mm_blocks"])
            assert "**Сортировка**" in repr(props["mm_blocks"])
            assert "⚲" not in repr(props["mm_blocks"])
            assert "↕" not in repr(props["mm_blocks"])
            account_list = manager.find("adminAccountList")
            assert isinstance(account_list, AccountList)
            await account_list.process_action(
                WidgetAction("account_filter", value="credentials"),
                SimpleNamespace(),
                manager,
            )
            await account_list.process_action(
                WidgetAction("account_sort"),
                SimpleNamespace(selected_option="cookies_desc"),
                manager,
            )
            assert account_list.query(manager) == AccountListQuery(
                filter="credentials",
                sort="cookies_desc",
            )
            await account_list.process_action(
                WidgetAction("account_filter", value="all"),
                SimpleNamespace(),
                manager,
            )
            result = await account_list.process_action(
                WidgetAction("account_select"),
                SimpleNamespace(selected_option=imported.account_ids[0]),
                manager,
            )
            assert result.target_state is None
            post_id = manager.stack.last_post_id
            await manager.show(ShowMode.EDIT)
            account_detail = renderer.calls[-1][0]
            assert isinstance(account_detail, PostRender)
            assert manager.current_context().state == AdminPanel.accounts
            assert manager.stack.last_post_id == post_id
            detail_bound = OpenFormActions(
                "https://app.test/callback",
                action_context,
                ui_profile=profile,
            )(account_detail.control_groups)
            card_group = next(
                group
                for group in account_detail.control_groups
                if any(control.id == "adminAccountCardEdit" for control in group.controls)
            )
            assert [control.legacy_label for control in card_group.controls] == [
                None,
                "Редактировать",
                "Удалить аккаунт",
            ]
            legacy_card = OpenFormActions(
                "https://app.test/callback",
                action_context,
            )((card_group,))
            assert [action.name for action in legacy_card.attachments[0].actions.items] == [
                "Проверить",
                "Редактировать",
                "Удалить аккаунт",
            ]
            detail_message, detail_blocks = account_detail.compose_native(detail_bound.blocks)
            detail_props = compile_post_ui(
                blocks=detail_blocks,
                profile=detail_bound.profile,
                context=detail_bound.context,
                message=detail_message,
            )
            assert "secret-A" not in repr(detail_props["mm_blocks"])
            assert "token-A" not in repr(detail_props["mm_blocks"])
            assert '"value"' not in repr(detail_props["mm_blocks"])
            assert "Пароль" in repr(detail_props["mm_blocks"])
            assert "задан" in repr(detail_props["mm_blocks"])
            assert "Выбранный аккаунт" in repr(detail_props["mm_blocks"])
            assert "Аккаунт / логин" not in repr(detail_props["mm_blocks"])
            assert "Логин" in repr(detail_props["mm_blocks"])
            assert len(detail_props["mm_blocks_actions"]) == 16
            assert "Редактировать аккаунт" in repr(detail_props["mm_blocks"])
            assert "Удалить аккаунт" in repr(detail_props["mm_blocks"])
            nested_blocks.clear()
            collect_blocks(detail_props["mm_blocks"][0])
            edit_button = next(block for block in nested_blocks if block.get("text") == "✏️")
            delete_button = next(block for block in nested_blocks if block.get("text") == "🗑️")
            card_block = next(
                block
                for block in nested_blocks
                if block.get("type") == "collapsible" and "🗑️" in repr(block.get("header"))
            )
            header_row = card_block["header"][0]
            assert header_row["type"] == "column_set"
            assert [column["width"] for column in header_row["columns"]] == [
                "stretch",
                "auto",
                "auto",
            ]
            assert "✏️" in repr(header_row)
            assert "🗑️" in repr(header_row)
            assert "✏️" not in repr(card_block["content"])
            assert "🗑️" not in repr(card_block["content"])
            assert edit_button["tooltip"] == "Редактировать аккаунт"
            assert delete_button["style"] == "danger"
            assert delete_button["tooltip"] == "Удалить аккаунт"
            account_card = manager.find("adminAccountCard")
            assert isinstance(account_card, AccountCard)
            assert account_card.edit_state == AdminPanel.account_edit
            assert account_card.delete_state == AdminPanel.account_delete
            edit_result = await account_card.process_action(
                WidgetAction("account_edit", target_state=AdminPanel.account_edit.state),
                SimpleNamespace(),
                manager,
            )
            delete_result = await account_card.process_action(
                WidgetAction("account_delete", target_state=AdminPanel.account_delete.state),
                SimpleNamespace(),
                manager,
            )
            assert edit_result.target_state == AdminPanel.account_edit.state
            assert delete_result.target_state == AdminPanel.account_delete.state
            await account_card.process_action(
                WidgetAction("account_check"),
                SimpleNamespace(),
                manager,
            )
            assert manager.dialog_data["check_summary"] == (
                "Проверка: действительных 1, недействительных 0, ошибок 0"
            )
            completed_check = renderer.calls[-1][0]
            assert isinstance(completed_check, PostRender)
            completed_bound = OpenFormActions(
                "https://app.test/callback",
                action_context,
                ui_profile=profile,
            )(completed_check.control_groups)
            completed_message, completed_blocks = completed_check.compose_native(completed_bound.blocks)
            completed_props = compile_post_ui(
                blocks=completed_blocks,
                profile=completed_bound.profile,
                context=completed_bound.context,
                message=completed_message,
            )
            completed_card_group = next(
                group
                for group in completed_check.control_groups
                if any(control.id == "adminAccountCardEdit" for control in group.controls)
            )
            assert completed_card_group.text.count(imported.account_ids[0]) == 1
            assert "Проверка: Успешно завершена" in completed_card_group.text
            nested_blocks.clear()
            collect_blocks(completed_props["mm_blocks"][0])
            completed_card = next(
                block
                for block in nested_blocks
                if block.get("type") == "collapsible" and "🗑️" in repr(block.get("header"))
            )
            embedded_check = next(
                block
                for block in completed_card["content"]
                if block.get("type") == "collapsible" and "### Проверка" in repr(block.get("header"))
            )
            assert imported.account_ids[0] not in repr(embedded_check)
            assert "Успешно завершена" in repr(completed_props["mm_blocks"])
            assert "действительных 1" in repr(completed_props["mm_blocks"])
            assert len(completed_props["mm_blocks_actions"]) == 16

            await manager.switch_to(AdminPanel.account_delete)
            delete_form = renderer.calls[-1][0]
            assert isinstance(delete_form, FormRender)
            assert [field.name for field in delete_form.form.elements] == ["confirm_account_delete"]
            assert delete_form.form.submit_label == "Удалить аккаунт"

            await manager.switch_to(AdminPanel.pushes)
            push_edit = manager.find("adminPushEdit")
            assert isinstance(push_edit, OpenForm)
            assert push_edit.state == AdminPanel.push_edit

            await manager.switch_to(AdminPanel.users)
            user_edit = manager.find("adminUserEdit")
            assert isinstance(user_edit, OpenForm)
            assert user_edit.state == AdminPanel.user_edit

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
