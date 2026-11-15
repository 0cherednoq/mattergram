"""Run the game-account admin dialog on an owned local Mattermost stand.

This operator tool is intentionally local-only. It replaces only the
``/game-admin`` and ``/app-users`` commands in the selected owned fixture and
keeps all application data in process memory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import secrets
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import aiohttp
from game_accounts_admin import (
    AccountCredentials,
    AdminInputError,
    AdminRole,
    AdminUser,
    CoalescingDialogJobUpdater,
    EphemeralMemoryGameAdminJobQueue,
    EphemeralMemoryGameAdminJobRepository,
    GameAccountsAdminService,
    GameAdminJobRunner,
    GameAdminJobs,
    MemoryAdminRepository,
    PushEvent,
    PushRule,
    TeamUser,
    VerificationResult,
    build_admin_dialog,
    build_admin_keyboard,
    register_admin_panel,
)
from pydantic import SecretStr

from mattergram import Bot, Dispatcher
from mattergram.contrib import admin_users as admin_users_plugin
from mattergram.contrib.composer_keyboard import MENU_CAPABILITY, ComposerKeyboardPlugin
from mattergram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from mattergram.http import CallbackSigner, HttpAdapter, SlashBinding
from mattergram.http import DialogManager as NativeDialogManager
from mattergram.plugins import PluginManager
from mattergram.ui import UIProfile
from mattergram.ui.capabilities import SERVER_FEATURES
from mattergram_dialog import (
    CoalescingDialogUpdater,
    DialogRegistry,
    DialogRuntime,
    DialogStorage,
    setup_dialogs,
)

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]


async def run_ephemeral_job_worker(
    queue: EphemeralMemoryGameAdminJobQueue,
    runner: GameAdminJobRunner,
) -> None:
    """Consume the local demo queue; production must supply durable infrastructure."""

    while True:
        job_id = await queue.dequeue()
        try:
            await runner.run(job_id)
        finally:
            queue.task_done()


@dataclass(frozen=True, slots=True)
class DemoStand:
    base_url: str
    declared_site_url: str
    team_id: str
    channel_id: str
    admin_id: str
    admin_username: str
    admin_password: str
    bot_id: str
    bot_token: str
    slash_token: str
    admin_users_slash_token: str


class DemoAccountVerifier:
    """Deterministic verifier for a UI demo, never a real game integration."""

    async def verify(self, game: str, credentials: AccountCredentials) -> VerificationResult:
        await asyncio.sleep(0.15)
        invalid = credentials.login.casefold().startswith(("bad", "invalid"))
        return VerificationResult(
            valid=not invalid,
            summary=f"{game}: demo checker rejected this login" if invalid else "demo checker accepted",
        )


class MattermostImportFileReader:
    def __init__(self, base_url: str, bot_token: str) -> None:
        self.base_url = base_url
        self.bot_token = bot_token

    async def read(self, file_id: str) -> bytes:
        timeout = aiohttp.ClientTimeout(total=15)
        headers = {"Authorization": "Bearer " + self.bot_token}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(f"{self.base_url}/api/v4/files/{file_id}") as response:
                if response.status != 200:
                    raise AdminInputError(
                        {"import_file": f"Mattermost не отдал файл (HTTP {response.status})"}
                    )
                payload = await response.content.read(1_000_001)
        if len(payload) > 1_000_000:
            raise AdminInputError({"import_file": "Файл превышает 1 МБ"})
        return payload


class MattermostPushDispatcher:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send(self, rule: PushRule, event: PushEvent, text: str) -> None:
        destination = rule.destination_id
        if rule.destination_kind == "direct":
            destination = (await self.bot.create_direct_channel(destination)).id
        await self.bot.send_message(destination, f"[{event}] {text}")


class MattermostTeamUserDirectory:
    """Uncached, team-scoped Mattermost directory backed by the runtime bot."""

    def __init__(self, bot: Bot, team_id: str) -> None:
        self.bot = bot
        self.team_id = team_id

    async def list_users(self) -> tuple[TeamUser, ...]:
        page = 0
        per_page = 100
        result: list[TeamUser] = []
        seen: set[str] = set()
        while True:
            users = await self.bot.get_users_in_team(
                self.team_id,
                page=page,
                per_page=per_page,
                active=True,
            )
            for user in users:
                if user.id in seen:
                    raise RuntimeError("Mattermost team pagination returned a duplicate user")
                seen.add(user.id)
                if not user.username:
                    raise RuntimeError("Mattermost team user has no username")
                display_name = " ".join(
                    part.strip() for part in (user.first_name, user.last_name) if part and part.strip()
                )
                result.append(
                    TeamUser(
                        id=user.id,
                        username=user.username,
                        display_name=display_name or (user.nickname or ""),
                        is_bot=user.is_bot,
                    )
                )
            if len(users) < per_page:
                return tuple(result)
            page += 1


class GameAdminUsersRepositoryAdapter:
    """Migration adapter sharing the local demo's existing in-memory user records."""

    def __init__(self, repository: MemoryAdminRepository) -> None:
        self.repository = repository

    @staticmethod
    def _plugin_user(user: AdminUser) -> admin_users_plugin.AdminUser:
        return admin_users_plugin.AdminUser(
            user.id,
            user.mattermost_user_id,
            user.username,
            user.role,
            user.enabled,
        )

    async def list_users(self) -> tuple[admin_users_plugin.AdminUser, ...]:
        async with self.repository.lock:
            return tuple(self._plugin_user(user) for user in self.repository.users.values())

    async def get_user(self, user_id: str) -> admin_users_plugin.AdminUser | None:
        async with self.repository.lock:
            user = self.repository.users.get(user_id)
            return None if user is None else self._plugin_user(user)

    async def get_by_mattermost_user_id(
        self,
        mattermost_user_id: str,
    ) -> admin_users_plugin.AdminUser | None:
        async with self.repository.lock:
            user = next(
                (
                    value
                    for value in self.repository.users.values()
                    if value.mattermost_user_id == mattermost_user_id
                ),
                None,
            )
            return None if user is None else self._plugin_user(user)

    async def save_user(self, user: admin_users_plugin.AdminUser) -> None:
        async with self.repository.lock:
            self.repository.users[user.id] = AdminUser(
                user.id,
                user.mattermost_user_id,
                user.username,
                cast(AdminRole, user.role),
                user.enabled,
            )


class GameAdminTeamDirectoryAdapter:
    def __init__(self, directory: MattermostTeamUserDirectory) -> None:
        self.directory = directory

    async def list_users(self) -> tuple[admin_users_plugin.TeamUser, ...]:
        return tuple(
            admin_users_plugin.TeamUser(user.id, user.username, user.display_name, user.is_bot)
            for user in await self.directory.list_users()
        )


async def _request_json(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    expected: tuple[int, ...] = (200, 201),
) -> Any:
    async with session.request(method, url, headers=headers, json=json_body) as response:
        if response.status not in expected:
            detail = (await response.text())[:300]
            raise RuntimeError(f"Owned Mattermost {method} {url} returned {response.status}: {detail}")
        if response.content_type != "application/json":
            return None
        return await response.json()


async def provision_demo(
    state_path: Path,
    *,
    base_url: str,
    callback_base: str,
) -> DemoStand:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            base_url + "/api/v4/users/login",
            json={"login_id": state["admin_username"], "password": state["admin_password"]},
        ) as response:
            login = await response.json()
            admin_token = response.headers.get("Token")
            if response.status != 200 or not admin_token or login.get("id") != state["admin_id"]:
                raise RuntimeError("Owned Mattermost administrator login failed")
        headers = {"Authorization": "Bearer " + admin_token}
        await _request_json(
            session,
            "POST",
            base_url + f"/api/v4/teams/{state['team_id']}/members",
            headers=headers,
            json_body={"team_id": state["team_id"], "user_id": state["bot_id"]},
            expected=(201, 400),
        )
        await _request_json(
            session,
            "POST",
            base_url + f"/api/v4/channels/{state['channel_id']}/members",
            headers=headers,
            json_body={"user_id": state["bot_id"]},
            expected=(201, 400),
        )
        commands = await _request_json(
            session,
            "GET",
            base_url + "/api/v4/commands?team_id=" + state["team_id"] + "&custom_only=true",
            headers=headers,
        )
        for command in commands:
            if command.get("trigger") in {"game-admin", "app-users"}:
                await _request_json(
                    session,
                    "DELETE",
                    base_url + "/api/v4/commands/" + command["id"],
                    headers=headers,
                    expected=(200, 204),
                )
        command = await _request_json(
            session,
            "POST",
            base_url + "/api/v4/commands",
            headers=headers,
            json_body={
                "team_id": state["team_id"],
                "trigger": "game-admin",
                "method": "P",
                "url": callback_base + "/mattergram/slash",
                "display_name": "Game accounts administration",
                "description": "Users, push routes, imports and account checks",
                "auto_complete": True,
                "auto_complete_desc": "Open the game-account administrator panel",
            },
        )
        admin_users_command = await _request_json(
            session,
            "POST",
            base_url + "/api/v4/commands",
            headers=headers,
            json_body={
                "team_id": state["team_id"],
                "trigger": "app-users",
                "method": "P",
                "url": callback_base + "/mattergram/slash",
                "display_name": "Application users",
                "description": "Manage application-scoped users and roles",
                "auto_complete": True,
                "auto_complete_desc": "Open application user administration",
            },
        )
    if (
        not isinstance(command, dict)
        or not isinstance(command.get("token"), str)
        or not isinstance(admin_users_command, dict)
        or not isinstance(admin_users_command.get("token"), str)
    ):
        raise RuntimeError("Owned Mattermost command response omitted its token")
    return DemoStand(
        base_url,
        state["base_url"],
        state["team_id"],
        state["channel_id"],
        state["admin_id"],
        state["admin_username"],
        state["admin_password"],
        state["bot_id"],
        state["bot_token"],
        command["token"],
        admin_users_command["token"],
    )


async def run(args: argparse.Namespace) -> None:
    base_url = args.base_url.rstrip("/")
    callback_base = args.callback_base.rstrip("/")
    stand = await provision_demo(args.state, base_url=base_url, callback_base=callback_base)
    bot = Bot(token=stand.bot_token, base_url=stand.base_url)
    dispatcher = Dispatcher()
    storage = MemoryStorage()
    isolation = SimpleEventIsolation()
    dialog_storage = DialogStorage(storage, isolation)
    job_worker: asyncio.Task[None] | None = None
    dialog_updates: CoalescingDialogUpdater | None = None
    try:
        identity = await bot.get_me()
        if identity.id != stand.bot_id:
            raise RuntimeError("Owned stand bot identity changed")
        action_ttl = 7 * 24 * 60 * 60
        signer = CallbackSigner(
            instance_id=bot.instance_id,
            bot_id=identity.id,
            keys={"admin-demo": secrets.token_bytes(32)},
            active_key_id="admin-demo",
            max_ttl=action_ttl,
        )
        profile = UIProfile(
            "12.0.2",
            client="owned-game-accounts-admin-demo",
            client_features=SERVER_FEATURES["12.0.2"],
            client_evidence="Owned local Mattermost 12.0.2 web demo",
            mm_blocks_enabled=True,
        )
        native_dialogs = NativeDialogManager(
            bot,
            profile=profile,
            callback_url=callback_base + "/mattergram/dialog",
        )
        user_directory = MattermostTeamUserDirectory(bot, stand.team_id)
        await user_directory.list_users()
        service = GameAccountsAdminService(
            MemoryAdminRepository(
                users=(
                    AdminUser(
                        "local-admin",
                        stand.admin_id,
                        stand.admin_username,
                        "admin",
                    ),
                )
            ),
            games=("ExampleGame", "SecondGame"),
            verifier=DemoAccountVerifier(),
            import_reader=MattermostImportFileReader(stand.base_url, stand.bot_token),
            push_dispatcher=MattermostPushDispatcher(bot),
            user_directory=user_directory,
        )
        job_repository = EphemeralMemoryGameAdminJobRepository()
        job_queue = EphemeralMemoryGameAdminJobQueue()
        jobs = GameAdminJobs(service, job_repository, job_queue)
        public_keyboard = await bot.get_composer_keyboard(stand.channel_id)
        if public_keyboard is not None:
            await bot.delete_composer_keyboard(stand.channel_id)
        direct_channel = await bot.create_direct_channel(stand.admin_id)
        admin_users = admin_users_plugin.AdminUsersPlugin(
            repository=GameAdminUsersRepositoryAdapter(service.repository),
            directory=GameAdminTeamDirectoryAdapter(user_directory),
            initial_admin_ids=(stand.admin_id,),
            command="app-users",
            messages=("Пользователи",),
        )
        registry = DialogRegistry(build_admin_dialog(service, jobs=jobs))
        plugin_manager = PluginManager(
            bot=bot,
            dispatcher=dispatcher,
            dialog_registry=registry,
            plugins=(
                admin_users,
                ComposerKeyboardPlugin(
                    targets=(direct_channel.id,),
                    keyboard=build_admin_keyboard(),
                ),
            ),
            capabilities=(MENU_CAPABILITY,),
        )
        plugin_manager.setup()
        runtime = DialogRuntime(
            bot,
            dialog_storage,
            registry,
            instance_id=bot.instance_id,
            bot_id=identity.id,
            callback_url=callback_base + "/mattergram/callback",
            callback_signer=signer,
            ui_profile=profile,
            action_ttl=action_ttl,
        )
        dialog_updates = CoalescingDialogUpdater(runtime, interval=0.25)
        job_runner = GameAdminJobRunner(
            service,
            job_repository,
            CoalescingDialogJobUpdater(dialog_updates),
        )
        job_worker = asyncio.create_task(run_ephemeral_job_worker(job_queue, job_runner))
        dispatcher.workflow_data[admin_users_plugin.ADMIN_USERS_RUNTIME.name] = runtime
        setup_dialogs(dispatcher, runtime)
        register_admin_panel(dispatcher, runtime, service)
        http = HttpAdapter(
            bot,
            dispatcher,
            trusted_peers=("127.0.0.1/32",),
            slash_bindings=(
                SlashBinding(stand.team_id, "game-admin", SecretStr(stand.slash_token)),
                SlashBinding(
                    stand.team_id,
                    "app-users",
                    SecretStr(stand.admin_users_slash_token),
                ),
            ),
            callback_signer=signer,
            dialogs=native_dialogs,
            own_dialogs=True,
            response_origins=(stand.base_url, stand.declared_site_url),
        )
        logger.info("Game admin demo ready on %s; use /game-admin", stand.base_url)
        await dispatcher.start(bot, http=http, host=args.host, port=args.port)
    finally:
        if job_worker is not None:
            job_worker.cancel()
            with suppress(asyncio.CancelledError):
                await job_worker
        if dialog_updates is not None:
            await dialog_updates.close()
        await dispatcher.close()
        await bot.close()
        await storage.close()
        await isolation.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=ROOT / "build/ui-stand-state.json")
    parser.add_argument("--base-url", default="http://127.0.0.1:45400")
    parser.add_argument("--callback-base", default="http://host.docker.internal:18766")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18766)
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
