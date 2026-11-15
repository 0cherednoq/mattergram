from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from pydantic import SecretBytes, SecretStr, ValidationError

from mattergram import Router
from mattergram.exceptions import PermissionDeniedError, RequestOutcomeUnknown
from mattergram.filters import Command
from mattergram.provisioning import (
    Bootstrap,
    BotSpec,
    ChannelSpec,
    FernetCodec,
    MemberSpec,
    MissingProvisioningSecret,
    ProvisioningConflict,
    ProvisioningScope,
    ResourceKey,
    SQLiteProvisioningStore,
    TeamSpec,
)
from mattergram.provisioning.bootstrap import fingerprint
from mattergram.provisioning.commands import CommandSpec
from mattergram.provisioning.models import NativeRole, NativeTeam, TeamMember
from mattergram.provisioning.permissions import Permissions
from mattergram.types import User


async def callback(event):
    return None


class FakeRuntimeBot:
    issued: list[FakeRuntimeBot] = []

    def __init__(self, *, token: str, base_url: str, request_timeout: float = 30.0) -> None:
        self.token, self.base_url = token, base_url
        self._resources = None
        self.closed = False
        self.issued.append(self)

    @property
    def resources(self):
        if self._resources is None:
            raise RuntimeError("No prepared resources")
        return self._resources

    async def get_me(self) -> User:
        return User.from_wire(
            {
                "id": "runtime-bot",
                "username": "fixture-bot",
                "roles": "system_user",
                "is_bot": True,
                "delete_at": 0,
            }
        )

    async def close(self) -> None:
        self.closed = True

    async def __aenter__(self):
        await self.get_me()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()


class RuntimeReadClient:
    tokens: list[str] = []
    calls: list[str] = []

    def __init__(self, *, token: str, base_url: str, request_timeout: float = 30.0) -> None:
        self.token, self.base_url = token, base_url
        self.tokens.append(token)

    @property
    def user_id(self) -> str:
        return "runtime-bot"

    async def get_me(self) -> User:
        self.calls.append("get_me")
        return await FakeRuntimeBot(token=self.token, base_url=self.base_url).get_me()

    async def get_team(self, team_id: str) -> NativeTeam:
        self.calls.append("get_team")
        return NativeTeam(id=team_id, name="engineering", display_name="Engineering", type="O")

    async def __aenter__(self):
        await self.get_me()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class PermissionClient:
    def __init__(self) -> None:
        self.roles = {
            "system_admin": NativeRole(name="system_admin", permissions=["manage_system"]),
            "system_user": NativeRole(name="system_user", permissions=[]),
            "team_member": NativeRole(name="team_member", permissions=["view_team"]),
            "channel_member": NativeRole(name="channel_member", permissions=["read_channel", "create_post"]),
            "deleted": NativeRole(name="deleted", permissions=["create_bot"], delete_at=1),
        }

    async def get_role(self, name: str) -> NativeRole:
        return self.roles[name]

    async def get_team_member(self, team_id: str, user_id: str) -> TeamMember:
        return TeamMember(team_id=team_id, user_id=user_id, roles="team_member", delete_at=0)

    async def get_channel_member(self, channel_id: str, user_id: str):
        from mattergram.provisioning.models import ChannelMember

        return ChannelMember(channel_id=channel_id, user_id=user_id, roles="channel_member")


class BootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="mattergram-bootstrap-")
        self.store = SQLiteProvisioningStore(
            Path(self.temporary.name) / "state",
            codec=FernetCodec(SecretBytes(Fernet.generate_key())),
        )
        self.scope = ProvisioningScope(server="https://server.test", application="fixture")

    async def asyncTearDown(self) -> None:
        self.temporary.cleanup()

    def deployment(self, *, token: str | None = "setup-secret") -> Bootstrap:
        return Bootstrap(
            base_url=self.scope.server,
            token=token,
            application=self.scope.application,
            bot=BotSpec(username="fixture-bot"),
            store=self.store,
            callback_base_url="https://callback.test",
            team=TeamSpec(
                key="engineering",
                name="engineering",
                existing_policy="reuse",
            ),
        )

    async def seed_runtime(self) -> None:
        async with self.store.session(self.scope) as journal:
            bot = journal.begin(ResourceKey(kind="bot", name="runtime"), "create")
            journal.complete(bot, resource_id="runtime-bot", owner_id="provisioner")
            token = journal.begin(
                ResourceKey(kind="bot-token", name="runtime"),
                "create",
                require_secret=True,
            )
            journal.complete(
                token,
                resource_id="native-pat",
                secret=SecretStr("runtime-secret"),
            )
            team = journal.begin(ResourceKey(kind="team", name="engineering"), "reuse")
            journal.complete(team, resource_id="native-team")
            command = journal.begin(
                ResourceKey(kind="command", team_id="native-team", name="help"),
                "create",
                fingerprint=fingerprint(
                    CommandSpec("help").to_payload(
                        team_id="native-team",
                        url="https://callback.test/mattergram/slash",
                    )
                ),
                require_secret=True,
            )
            journal.complete(
                command,
                resource_id="native-command",
                secret=SecretStr("command-secret"),
                owner_id="provisioner",
            )

    async def test_runtime_load_uses_only_persisted_non_admin_credentials(self) -> None:
        await self.seed_runtime()
        router = Router()
        router.slash_command.register(callback, Command("help"))
        deployment = self.deployment(token=None)
        RuntimeReadClient.tokens.clear()
        RuntimeReadClient.calls.clear()
        FakeRuntimeBot.issued.clear()
        with (
            patch("mattergram.provisioning.bootstrap.Bot", FakeRuntimeBot),
            patch("mattergram.provisioning.bootstrap.SetupClient", RuntimeReadClient),
        ):
            with self.assertRaises(MissingProvisioningSecret):
                await deployment.prepare(router)
            runtime = await deployment.load_runtime(router)
        self.assertEqual(RuntimeReadClient.tokens, ["runtime-secret"])
        self.assertEqual(RuntimeReadClient.calls, ["get_me", "get_team"])
        self.assertEqual(runtime.token, "runtime-secret")
        self.assertTrue(runtime.resources.report.runtime_verified)
        self.assertFalse(runtime.resources.report.callback_verified)
        self.assertEqual(runtime.resources.teams["engineering"].id, "native-team")
        self.assertEqual(runtime.resources.slash_bindings[0].token.get_secret_value(), "command-secret")
        self.assertNotIn("runtime-secret", repr(runtime.resources))
        self.assertNotIn("command-secret", repr(runtime.resources))
        with self.assertRaises(TypeError):
            runtime.resources.teams["other"] = runtime.resources.teams["engineering"]
        await runtime.close()

    async def test_native_write_validation_records_known_id_as_uncertain(self) -> None:
        deployment = self.deployment()
        key = ResourceKey(kind="channel", team_id="native-team", name="alerts")

        class Result:
            id = "native-channel"

        async def operation():
            return Result()

        def reject(result) -> None:
            raise ProvisioningConflict("wrong native scope")

        async with self.store.session(self.scope) as journal:
            with self.assertRaises(ProvisioningConflict):
                await deployment._write(
                    journal,
                    key,
                    operation,
                    identity=lambda result: result.id,
                    validate=reject,
                )
            intent = journal.intents()[0]
            self.assertEqual(intent.phase, "indeterminate")
            self.assertEqual(intent.resource_id, "native-channel")
            self.assertIsNone(journal.binding(key))

    async def test_uncertain_persistence_failure_does_not_mask_native_error(self) -> None:
        deployment = self.deployment()
        key = ResourceKey(kind="team", name="engineering")

        async def operation():
            raise RequestOutcomeUnknown("write outcome unknown")

        async with self.store.session(self.scope) as journal:
            with patch.object(journal, "uncertain", side_effect=OSError("disk")):
                with self.assertRaises(RequestOutcomeUnknown) as caught:
                    await deployment._write(
                        journal,
                        key,
                        operation,
                        identity=lambda result: result.id,
                    )
            self.assertIn("write outcome unknown", str(caught.exception))
            self.assertIn("durable pending intent", " ".join(caught.exception.__notes__))
            self.assertEqual(journal.intents()[0].phase, "pending")

    async def test_conclusive_permission_rejection_is_aborted_and_retryable(self) -> None:
        deployment = self.deployment()
        key = ResourceKey(kind="team", name="engineering")

        async def operation():
            raise PermissionDeniedError(403, error_id="native.denied")

        async with self.store.session(self.scope) as journal:
            with self.assertRaises(PermissionDeniedError):
                await deployment._write(
                    journal,
                    key,
                    operation,
                    identity=lambda result: result.id,
                )
            self.assertEqual(journal.intents()[0].phase, "aborted")
            replacement = journal.begin(key, "create")
            journal.confirm_absent(replacement)

    async def test_runtime_load_fails_closed_for_missing_command_secret(self) -> None:
        await self.seed_runtime()
        router = Router()
        router.slash_command.register(callback, Command("new"))
        with (
            patch("mattergram.provisioning.bootstrap.Bot", FakeRuntimeBot),
            patch("mattergram.provisioning.bootstrap.SetupClient", RuntimeReadClient),
            self.assertRaises(MissingProvisioningSecret),
        ):
            await self.deployment(token=None).load_runtime(router)

    async def test_runtime_load_requires_setup_sync_after_manifest_change(self) -> None:
        await self.seed_runtime()
        router = Router()
        router.slash_command.register(
            callback,
            Command("help"),
            description="changed without setup reconciliation",
        )
        with (
            patch("mattergram.provisioning.bootstrap.Bot", FakeRuntimeBot),
            patch("mattergram.provisioning.bootstrap.SetupClient", RuntimeReadClient),
            self.assertRaises(ProvisioningConflict),
        ):
            await self.deployment(token=None).load_runtime(router)

    async def test_declarations_are_explicit_and_member_identity_is_unambiguous(self) -> None:
        self.assertEqual(
            ChannelSpec(
                key="alerts",
                name="alerts",
                members=["native-id", MemberSpec(username="alice")],
                existing_policy="reuse",
            ).members,
            ("native-id", MemberSpec(username="alice")),
        )
        for declaration in (
            lambda: MemberSpec(),
            lambda: MemberSpec(user_id="id", username="alice"),
            lambda: TeamSpec(key="team", name="name", team_id="id"),
            lambda: ChannelSpec(key="alerts", name="alerts", members=["id", "id"]),
            lambda: ChannelSpec(
                key="alerts",
                name="alerts",
                members=[MemberSpec(username="alice"), MemberSpec(username="alice")],
            ),
        ):
            with self.subTest(declaration=declaration), self.assertRaises(ValidationError):
                declaration()

    async def test_permissions_use_native_role_sets_without_global_admin_fallback(self) -> None:
        client = PermissionClient()
        admin = Permissions(client, "actor", "system_admin deleted")
        with self.assertRaises(PermissionDeniedError):
            await admin.require("create_bot")
        await admin.require("create_post", team_id="native-team", channel_id="native-channel")
        member = Permissions(client, "actor", "system_user")
        await member.require("view_team", team_id="native-team")
        await member.require("create_post", team_id="native-team", channel_id="native-channel")
        with self.assertRaises(PermissionDeniedError):
            await member.require("create_bot")
