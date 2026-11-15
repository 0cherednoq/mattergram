"""Automatic declared-resource synchronization using a dedicated setup client and journal."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, TypeVar

from pydantic import SecretStr

from mattergram.bot import Bot
from mattergram.dispatcher.router import Router
from mattergram.exceptions import APIError, MattergramError, UnsupportedCapabilityError
from mattergram.http.auth import SlashBinding

from .client import SetupClient
from .commands import CommandSpec, collect_commands
from .models import BotAccount, ChannelMember, NativeChannel, NativeTeam, TeamMember
from .permissions import Permissions
from .resources import ProvisioningReport, ProvisioningStep, RuntimeResources
from .specs import BotSpec, ChannelSpec, MemberSpec, TeamSpec
from .store import SQLiteProvisioningStore, StoreSession, UnresolvedIntentError
from .store_models import ProvisioningIntent, ProvisioningScope, ResourceKey, canonical

T = TypeVar("T")


class ProvisioningConflict(MattergramError):
    """Ownership, visibility, identity or archived-state conflict; no alternative resource is created."""


class MissingProvisioningSecret(ProvisioningConflict):
    """Use an explicit recovery policy; do not issue another token on restart."""


@dataclass(frozen=True)
class ExistingRuntimeBot:
    user_id: str
    username: str
    owner_id: None = None  # Unobservable through a runtime credential, never fabricated.
    delete_at: int = 0


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


async def absent(operation: Awaitable[T]) -> T | None:
    try:
        return await operation
    except APIError as error:
        if error.status != 404:
            raise
        return None


def mark_uncertain(
    journal: StoreSession,
    intent: ProvisioningIntent,
    error: BaseException,
    *,
    resource_id: str | None = None,
) -> None:
    """Keep the original native failure while leaving a fail-closed recovery record."""
    try:
        journal.uncertain(intent, resource_id=resource_id)
    except BaseException as journal_error:
        error.add_note(
            "The provisioning journal could not persist the uncertain phase "
            f"({type(journal_error).__name__}); its durable pending intent still blocks retry."
        )


def record_write_failure(
    journal: StoreSession,
    intent: ProvisioningIntent,
    error: BaseException,
    *,
    resource_id: str | None = None,
) -> None:
    """Abort conclusive client rejections; retain uncertainty for every ambiguous outcome."""
    try:
        if isinstance(error, APIError) and 300 <= error.status < 500 and error.status != 408:
            journal.abort(intent)
        else:
            mark_uncertain(journal, intent, error, resource_id=resource_id)
    except BaseException as journal_error:
        error.add_note(
            "The provisioning journal could not persist the terminal failure phase "
            f"({type(journal_error).__name__}); its earlier durable intent still blocks retry."
        )


class Bootstrap:
    def __init__(
        self,
        *,
        base_url: str,
        token: str | None = None,
        application: str,
        bot: BotSpec,
        store: SQLiteProvisioningStore,
        callback_base_url: str,
        team: str | TeamSpec | None = None,
        teams: Sequence[str | TeamSpec] = (),
        channels: Sequence[str | ChannelSpec] = (),
        sync_commands: bool = True,
        change_server_settings: bool = False,
        http_path_prefix: str = "/mattergram",
        request_timeout: float = 30.0,
    ) -> None:
        if (
            type(sync_commands) is not bool
            or type(change_server_settings) is not bool
            or not isinstance(bot, BotSpec)
        ):
            raise TypeError("Declare strict provisioning flags and a BotSpec")
        if team is not None and teams:
            raise ValueError("Declare team or teams")
        if isinstance(teams, str) or isinstance(channels, str):
            raise TypeError("Teams and channels must be explicit sequences")
        if (
            isinstance(request_timeout, bool)
            or not isinstance(request_timeout, (float, int))
            or not math.isfinite(request_timeout)
            or request_timeout <= 0
        ):
            raise ValueError("request_timeout must be a positive finite number of seconds")
        self.scope = ProvisioningScope(server=base_url, application=application)
        root = ProvisioningScope(server=callback_base_url, application=application).server.rstrip("/")
        if not http_path_prefix.startswith("/") or any(
            x in http_path_prefix for x in ("..", "%", "?", "#", "\\", "//")
        ):
            raise ValueError("Expected a canonical HTTP integration path prefix")
        self.callback_url = root + http_path_prefix.rstrip("/") + "/slash"
        if len(self.callback_url.encode()) > 1024:
            raise ValueError("Callback URL exceeds the native command URL bound")
        self.path_prefix = http_path_prefix.rstrip("/")
        if token is not None and (not isinstance(token, str) or not token):
            raise ValueError("Setup token must be nonempty when provided")
        self._token, self.bot_spec, self.store = (
            SecretStr(token) if token is not None else None,
            bot,
            store,
        )
        declarations = (team,) if team is not None else tuple(teams)
        self.teams = tuple(
            TeamSpec(key=s, name=s, existing_policy="reuse") if isinstance(s, str) else s
            for s in declarations
        )
        self.channels = tuple(
            ChannelSpec(key=s, name=s, existing_policy="reuse") if isinstance(s, str) else s for s in channels
        )
        if not self.teams and self.channels:
            raise ValueError("Team scope is required for channels/command synchronization")
        if len({s.key for s in self.teams}) != len(self.teams) or len({s.key for s in self.channels}) != len(
            self.channels
        ):
            raise ValueError("Resource application keys must be unique")
        for channel in self.channels:
            if (channel.team is None and len(self.teams) != 1) or (
                channel.team is not None and channel.team not in {s.key for s in self.teams}
            ):
                raise ValueError("Each channel must resolve to one declared team key")
        self.sync_commands, self.change_settings = sync_commands, change_server_settings
        self.request_timeout = float(request_timeout)
        self._steps: list[ProvisioningStep] = []
        self._current: ResourceKey | None = None
        self._running = False
        self._runtime_verified = False

    @property
    def report(self) -> ProvisioningReport:
        return ProvisioningReport(tuple(self._steps), runtime_verified=self._runtime_verified)

    def _record(self, key: ResourceKey, status: Any, resource_id: str | None = None) -> None:
        self._steps.append(ProvisioningStep(key, status, resource_id))

    async def _write(
        self,
        journal: StoreSession,
        key: ResourceKey,
        operation: Callable[[], Awaitable[T]],
        *,
        action: Any = "create",
        identity: Callable[[T], str],
        secret: Callable[[T], SecretStr | None] = lambda _: None,
        owner: Callable[[T], str | None] = lambda _: None,
        desired: str | None = None,
        require_secret: bool = False,
        validate: Callable[[T], None] | None = None,
    ) -> T:
        self._current = key
        intent = journal.begin(key, action, fingerprint=desired, require_secret=require_secret)
        resource_id: str | None = None
        issued_secret: SecretStr | None = None
        owner_id: str | None = None
        try:
            result = await operation()
            resource_id = identity(result)
            if not resource_id:
                raise ProvisioningConflict("Native write returned an empty resource identity")
            if validate is not None:
                validate(result)
            issued_secret = secret(result)
            owner_id = owner(result)
        except BaseException as error:
            record_write_failure(journal, intent, error, resource_id=resource_id)
            raise
        assert resource_id is not None
        journal.complete(
            intent,
            resource_id=resource_id,
            secret=issued_secret,
            owner_id=owner_id,
        )
        self._record(key, "updated" if action == "update" else "created", resource_id)
        return result

    def _reuse(
        self,
        journal: StoreSession,
        key: ResourceKey,
        resource_id: str,
        *,
        secret: SecretStr | None = None,
        owner_id: str | None = None,
    ) -> None:
        intent = journal.begin(key, "reuse", require_secret=secret is not None)
        journal.complete(intent, resource_id=resource_id, secret=secret, owner_id=owner_id)
        self._record(key, "reused", resource_id)

    async def _features(self, setup: SetupClient, journal: StoreSession, commands_required: bool) -> None:
        features = await setup.get_features()
        if features.version not in {"11.1.0", "11.7.0", "11.11.0", "12.0.2"}:
            raise UnsupportedCapabilityError("Bootstrap requires a referenced native server profile")
        needs = [("EnableCommands", features.commands)] if commands_required else []
        if (
            journal.binding(ResourceKey(kind="bot", name="runtime")) is None
            and self.bot_spec.existing_id is None
        ):
            needs += [("EnableBotAccountCreation", features.bot_creation)]
        if (
            journal.binding(ResourceKey(kind="bot-token", name="runtime")) is None
            and self.bot_spec.existing_token is None
        ):
            needs += [("EnableUserAccessTokens", features.access_tokens)]
        missing = tuple(name for name, enabled in needs if enabled is not True)
        if missing:
            if not self.change_settings:
                raise UnsupportedCapabilityError("Required provisioning feature is disabled or unobservable")
            key = ResourceKey(kind="settings", name=",".join(sorted(missing)))
            intent = journal.begin(key, "create")
            try:
                await setup.enable_settings(*missing)
            except BaseException as error:
                record_write_failure(journal, intent, error)
                raise
            journal.complete_step(intent)
            self._record(key, "updated")
            verified = await setup.get_features()
            flags = {
                "EnableCommands": verified.commands,
                "EnableBotAccountCreation": verified.bot_creation,
                "EnableUserAccessTokens": verified.access_tokens,
            }
            if any(flags[name] is not True for name in missing):
                raise UnsupportedCapabilityError(
                    "Server/environment policy did not enable the requested features"
                )

    async def _team(
        self, spec: TeamSpec, setup: SetupClient, permissions: Permissions, journal: StoreSession
    ) -> NativeTeam:
        key = ResourceKey(kind="team", name=spec.key)
        self._current = key
        binding = journal.binding(key)
        team = (
            await setup.get_team(binding.resource_id)
            if binding
            else (
                await setup.get_team(spec.team_id)
                if spec.team_id
                else await absent(setup.get_team_by_name(spec.name or ""))
            )
        )
        if team is not None:
            self._check_team(team, spec)
        if team is None:
            if not spec.create_if_missing:
                raise ProvisioningConflict("Referenced team does not exist")
            await permissions.require("create_team")
            team = await self._write(
                journal,
                key,
                lambda: setup.create_team(
                    name=spec.name or "",
                    display_name=spec.display_name or "",
                    type="O" if spec.visibility == "open" else "I",
                ),
                identity=lambda x: x.id,
                validate=lambda x: self._check_team(x, spec),
            )
        elif binding is None:
            if spec.existing_policy != "reuse":
                raise ProvisioningConflict("Existing team requires explicit reuse")
            self._reuse(journal, key, team.id)
        else:
            self._record(key, "reused", team.id)
        if (
            team.delete_at
            or (spec.name is not None and team.name != spec.name)
            or (spec.team_id is not None and team.id != spec.team_id)
            or (spec.visibility and team.type != ("O" if spec.visibility == "open" else "I"))
        ):
            raise ProvisioningConflict("Team identity/privacy/archive drift")
        if (
            binding is not None
            and binding.ownership == "owned"
            and spec.display_name is not None
            and team.display_name != spec.display_name
        ):
            await permissions.require("manage_team", team_id=team.id)
            team = await self._write(
                journal,
                key,
                lambda: setup.patch_team(team.id, display_name=spec.display_name or ""),
                action="update",
                identity=lambda x: x.id,
                validate=lambda x: self._check_team(x, spec),
            )
        return team

    @staticmethod
    def _check_team(team: NativeTeam, spec: TeamSpec) -> None:
        if (
            team.delete_at
            or (spec.name is not None and team.name != spec.name)
            or (spec.team_id is not None and team.id != spec.team_id)
            or (spec.visibility and team.type != ("O" if spec.visibility == "open" else "I"))
        ):
            raise ProvisioningConflict("Team identity/privacy/archive drift")

    async def _bot(
        self, setup: SetupClient, permissions: Permissions, journal: StoreSession
    ) -> BotAccount | ExistingRuntimeBot:
        key = ResourceKey(kind="bot", name="runtime")
        self._current = key
        binding = journal.binding(key)
        if (binding is not None and binding.ownership == "reused") or (
            binding is None and self.bot_spec.existing_id is not None
        ):
            identifier = binding.resource_id if binding else self.bot_spec.existing_id
            token = (
                journal.secret(ResourceKey(kind="bot-token", name="runtime")) or self.bot_spec.existing_token
            )
            if token is None:
                raise MissingProvisioningSecret("Existing bot requires its explicit available credential")
            if self.bot_spec.existing_id is not None and identifier != self.bot_spec.existing_id:
                raise ProvisioningConflict("Existing bot ID differs from its persisted binding")
            async with Bot(token=token.get_secret_value(), base_url=self.scope.server) as verified:
                identity = await verified.get_me()
                if (
                    identity.id != identifier
                    or identity.username != self.bot_spec.username
                    or identity.raw.get("is_bot") is not True
                    or identity.raw.get("delete_at", 0)
                    or "system_admin" in str(identity.raw.get("roles", "")).split()
                ):
                    raise ProvisioningConflict(
                        "Explicit existing runtime identity differs or is not an active non-admin bot"
                    )
            if binding is None:
                self._reuse(journal, key, identity.id)
            else:
                self._record(key, "reused", identity.id)
            return ExistingRuntimeBot(identity.id, self.bot_spec.username)
        if binding:
            account = await setup.get_bot(binding.resource_id)
            if binding.owner_id is None or account.owner_id != binding.owner_id:
                raise ProvisioningConflict("Managed bot native owner differs from persisted evidence")
            self._record(key, "reused", account.user_id)
        else:
            existing_user = await absent(setup.get_user_by_username(self.bot_spec.username))
            if existing_user is not None:
                raise ProvisioningConflict("A matching username does not prove ownership")
            await permissions.require("create_bot")
            account = await self._write(
                journal,
                key,
                lambda: setup.create_bot(
                    username=self.bot_spec.username,
                    display_name=self.bot_spec.display_name,
                    description=self.bot_spec.description,
                ),
                identity=lambda x: x.user_id,
                owner=lambda x: x.owner_id,
            )
        if account.username != self.bot_spec.username or account.delete_at:
            raise ProvisioningConflict("Bot username/archive drift")
        return account

    async def _credential(
        self,
        account: BotAccount | ExistingRuntimeBot,
        setup: SetupClient,
        permissions: Permissions,
        journal: StoreSession,
    ) -> SecretStr:
        key = ResourceKey(kind="bot-token", name="runtime")
        self._current = key
        if journal.binding(key):
            secret = journal.secret(key)
            if secret is None:
                raise MissingProvisioningSecret("Runtime secret is missing; explicit recovery is required")
            self._record(key, "reused")
            return secret
        if self.bot_spec.existing_token is not None:
            self._reuse(journal, key, account.user_id, secret=self.bot_spec.existing_token)
            return self.bot_spec.existing_token
        if isinstance(account, ExistingRuntimeBot) or any(i.key == key for i in journal.intents()):
            raise MissingProvisioningSecret(
                "Runtime credential/binding was lost; explicit recovery is required"
            )
        await permissions.require("create_user_access_token")
        await permissions.require(
            "manage_bots" if account.owner_id == setup.user_id else "manage_others_bots"
        )
        issued = await self._write(
            journal,
            key,
            lambda: setup.issue_access_token(
                account.user_id, description="mattergram managed runtime credential"
            ),
            identity=lambda x: x.id,
            secret=lambda x: x.token,
            require_secret=True,
        )
        return issued.token

    async def _member(
        self,
        setup: SetupClient,
        permissions: Permissions,
        journal: StoreSession,
        team_id: str,
        user_id: str,
        channel: NativeChannel | None = None,
    ) -> None:
        key = ResourceKey(
            kind="membership", team_id=team_id, name=(channel.id if channel else "team") + ":" + user_id
        )
        self._current = key
        pending = next(
            (i for i in journal.intents() if i.key == key and i.phase in {"pending", "indeterminate"}), None
        )
        member: TeamMember | ChannelMember | None = await absent(
            setup.get_channel_member(channel.id, user_id)
            if channel
            else setup.get_team_member(team_id, user_id)
        )
        if member is not None and (
            member.user_id != user_id
            or (isinstance(member, TeamMember) and member.team_id != team_id)
            or (isinstance(member, ChannelMember) and (channel is None or member.channel_id != channel.id))
        ):
            raise ProvisioningConflict("Native membership identity/scope differs")
        if member is not None and (not isinstance(member, TeamMember) or member.delete_at == 0):
            if pending:
                journal.complete_step(pending)
            self._record(key, "reused", user_id)
            return
        permission = (
            ("manage_private_channel_members" if channel.type == "P" else "manage_public_channel_members")
            if channel
            else "add_user_to_team"
        )
        await permissions.require(permission, team_id=team_id, channel_id=channel.id if channel else None)
        intent = pending or journal.begin(key, "create")
        try:
            member = await (
                setup.add_channel_member(channel.id, user_id)
                if channel
                else setup.add_team_member(team_id, user_id)
            )
            if (
                member.user_id != user_id
                or (isinstance(member, TeamMember) and member.team_id != team_id)
                or (
                    isinstance(member, ChannelMember) and (channel is None or member.channel_id != channel.id)
                )
            ):
                raise ProvisioningConflict("Native membership response identifies another user")
        except BaseException as error:
            record_write_failure(journal, intent, error)
            raise
        journal.complete_step(intent)
        self._record(key, "created", user_id)

    async def _channel(
        self,
        spec: ChannelSpec,
        team: NativeTeam,
        setup: SetupClient,
        permissions: Permissions,
        journal: StoreSession,
    ) -> NativeChannel:
        key = ResourceKey(kind="channel", team_id=team.id, name=spec.key)
        self._current = key
        binding = journal.binding(key)
        channel = (
            await setup.get_channel(binding.resource_id)
            if binding
            else (
                await setup.get_channel(spec.channel_id)
                if spec.channel_id
                else await absent(setup.get_channel_by_name(team.id, spec.name or ""))
            )
        )
        if channel is not None:
            self._check_channel(channel, spec, team)
        if channel is None:
            if not spec.create_if_missing:
                raise ProvisioningConflict("Referenced channel is missing or inaccessible")
            await permissions.require(
                "manage_team", team_id=team.id
            )  # Required to distinguish hidden private names.
            await permissions.require(
                "create_private_channel" if spec.visibility == "private" else "create_public_channel",
                team_id=team.id,
            )
            channel = await self._write(
                journal,
                key,
                lambda: setup.create_channel(
                    team_id=team.id,
                    name=spec.name or "",
                    display_name=spec.display_name or "",
                    type="P" if spec.visibility == "private" else "O",
                    purpose=spec.purpose or "",
                    header=spec.header or "",
                ),
                identity=lambda x: x.id,
                validate=lambda x: self._check_channel(x, spec, team),
            )
            binding = journal.binding(key)
        elif binding is None:
            if spec.existing_policy != "reuse":
                raise ProvisioningConflict("Existing channel requires explicit reuse")
            self._reuse(journal, key, channel.id)
        else:
            self._record(key, "reused", channel.id)
        if (
            channel.team_id != team.id
            or channel.delete_at
            or (spec.name is not None and channel.name != spec.name)
            or (spec.visibility and channel.type != ("P" if spec.visibility == "private" else "O"))
        ):
            raise ProvisioningConflict("Channel team/name/privacy/archive drift")
        changes = {
            name: value
            for name, value in (
                ("display_name", spec.display_name),
                ("purpose", spec.purpose),
                ("header", spec.header),
            )
            if value is not None and getattr(channel, name) != value
        }
        if changes and binding is not None and binding.ownership == "owned":
            await permissions.require(
                "manage_private_channel_properties"
                if channel.type == "P"
                else "manage_public_channel_properties",
                team_id=team.id,
                channel_id=channel.id,
            )
            channel = await self._write(
                journal,
                key,
                lambda: setup.patch_channel(channel.id, **changes),
                action="update",
                identity=lambda x: x.id,
            )
        return channel

    @staticmethod
    def _check_channel(channel: NativeChannel, spec: ChannelSpec, team: NativeTeam) -> None:
        if (
            channel.team_id != team.id
            or channel.delete_at
            or (spec.name is not None and channel.name != spec.name)
            or (spec.channel_id is not None and channel.id != spec.channel_id)
            or (spec.visibility and channel.type != ("P" if spec.visibility == "private" else "O"))
        ):
            raise ProvisioningConflict("Channel team/name/privacy/archive drift")

    async def _commands(
        self,
        commands: tuple[CommandSpec, ...],
        team: NativeTeam,
        setup: SetupClient,
        permissions: Permissions,
        journal: StoreSession,
    ) -> tuple[SlashBinding, ...]:
        if not commands:
            return ()
        await permissions.require("view_team", team_id=team.id)
        await permissions.require("manage_own_slash_commands", team_id=team.id)
        available = await setup.list_commands(team.id)
        result = []
        for spec in commands:
            key = ResourceKey(kind="command", team_id=team.id, name=spec.trigger)
            self._current = key
            binding = journal.binding(key)
            collisions = [
                c
                for c in available
                if c.trigger.casefold() == spec.trigger.casefold()
                and (binding is None or c.id != binding.resource_id)
            ]
            if collisions:
                raise ProvisioningConflict("Foreign or built-in command trigger conflict")
            payload = spec.to_payload(team_id=team.id, url=self.callback_url)
            desired = fingerprint(payload)
            if binding:
                if binding.ownership != "owned":
                    raise ProvisioningConflict("Command is not owned by this application")
                saved = await setup.get_command(binding.resource_id)
                if (
                    saved.team_id != team.id
                    or saved.trigger != spec.trigger
                    or binding.owner_id is None
                    or saved.creator_id != binding.owner_id
                ):
                    raise ProvisioningConflict("Native command ownership/team/trigger drift")
                token = journal.secret(key)
                if token is None:
                    raise MissingProvisioningSecret(
                        "Command secret is missing; explicit recovery is required"
                    )
                if saved.token is not None and saved.token != token:
                    raise MissingProvisioningSecret("Command token changed outside managed reconciliation")
                if any(getattr(saved, name) != value for name, value in payload.items()):
                    if saved.creator_id != setup.user_id:
                        await permissions.require("manage_others_slash_commands", team_id=team.id)
                    updated = await self._write(
                        journal,
                        key,
                        lambda: setup.update_command(saved.id, spec, team_id=team.id, url=self.callback_url),
                        action="update",
                        identity=lambda x: x.id,
                        secret=lambda x: x.token,
                        desired=desired,
                        require_secret=True,
                    )
                    if updated.token is not None:
                        token = updated.token
                else:
                    if binding.fingerprint != desired:
                        evidence = journal.begin(
                            key,
                            "update",
                            fingerprint=desired,
                            require_secret=True,
                        )
                        journal.complete(
                            evidence,
                            resource_id=saved.id,
                            secret=token,
                            owner_id=binding.owner_id,
                        )
                    self._record(key, "reused", saved.id)
            else:
                saved = await self._write(
                    journal,
                    key,
                    lambda: setup.create_command(spec, team_id=team.id, url=self.callback_url),
                    identity=lambda x: x.id,
                    secret=lambda x: x.token,
                    owner=lambda x: x.creator_id,
                    desired=desired,
                    require_secret=True,
                )
                token = saved.token
                assert token is not None
            result.append(SlashBinding(team.id, spec.trigger, token, method=spec.method))
        return tuple(result)

    async def prepare(self, router: Router) -> Bot:
        if self._running:
            raise RuntimeError("Bootstrap is already running")
        setup_token = self._token
        if setup_token is None:
            raise MissingProvisioningSecret(
                "A setup credential is required for reconciliation; use load_runtime after a completed setup"
            )
        commands = collect_commands(router).commands if self.sync_commands else ()
        if len({command.trigger.casefold() for command in commands}) != len(commands):
            raise ProvisioningConflict(
                "Case-overlapping command declarations require an explicit unique trigger"
            )
        if commands and not self.teams:
            raise ValueError("Slash commands require declared team scopes")
        self._running, self._runtime_verified = True, False
        self._steps, self._current = [], None
        runtime: Bot | None = None
        try:
            async with self.store.session(self.scope) as journal:
                unresolved = [
                    i
                    for i in journal.intents()
                    if i.phase in {"pending", "indeterminate"} and i.key.kind != "membership"
                ]
                if unresolved:
                    raise UnresolvedIntentError("Reconcile prior resource writes before another Bootstrap")
                async with SetupClient(
                    token=setup_token.get_secret_value(),
                    base_url=self.scope.server,
                    request_timeout=self.request_timeout,
                ) as setup:
                    actor = await setup.get_me()
                    permissions = Permissions(setup, actor.id, str(actor.raw.get("roles", "")))
                    await self._features(setup, journal, bool(commands))
                    if (
                        journal.binding(ResourceKey(kind="bot", name="runtime")) is None
                        and self.bot_spec.existing_id is None
                    ):
                        await permissions.require("create_bot")
                    teams = {
                        spec.key: await self._team(spec, setup, permissions, journal) for spec in self.teams
                    }
                    account = await self._bot(setup, permissions, journal)
                    credential = await self._credential(account, setup, permissions, journal)
                    runtime = Bot(
                        token=credential.get_secret_value(),
                        base_url=self.scope.server,
                        request_timeout=self.request_timeout,
                    )
                    identity = await runtime.get_me()
                    if (
                        identity.id != account.user_id
                        or identity.raw.get("is_bot") is not True
                        or "system_admin" in str(identity.raw.get("roles", "")).split()
                    ):
                        raise ProvisioningConflict(
                            "Runtime credential must identify the declared non-admin bot"
                        )
                    for team in teams.values():
                        await self._member(setup, permissions, journal, team.id, account.user_id)
                    channels = {}
                    for spec in self.channels:
                        team = teams[spec.team] if spec.team else next(iter(teams.values()))
                        channel = await self._channel(spec, team, setup, permissions, journal)
                        members: list[str] = [account.user_id] if spec.add_bot else []
                        for declared_member in spec.members:
                            if isinstance(declared_member, MemberSpec):
                                user = (
                                    await setup.get_user(declared_member.user_id)
                                    if declared_member.user_id is not None
                                    else await setup.get_user_by_username(declared_member.username or "")
                                )
                                if (
                                    declared_member.user_id is not None and user.id != declared_member.user_id
                                ) or (
                                    declared_member.username is not None
                                    and user.username != declared_member.username
                                ):
                                    raise ProvisioningConflict("Declared member identity differs")
                                members.append(user.id)
                            else:
                                user = await setup.get_user(declared_member)
                                if user.id != declared_member:
                                    raise ProvisioningConflict("Declared member ID differs")
                                members.append(user.id)
                        for user_id in dict.fromkeys(members):
                            await self._member(setup, permissions, journal, team.id, user_id)
                            await self._member(setup, permissions, journal, team.id, user_id, channel)
                        channels[spec.key] = channel
                    bindings_list: list[SlashBinding] = []
                    if self.sync_commands:
                        for team in teams.values():
                            bindings_list.extend(
                                await self._commands(commands, team, setup, permissions, journal)
                            )
                    bindings = tuple(bindings_list)
                    async with SetupClient(
                        token=credential.get_secret_value(),
                        base_url=self.scope.server,
                        request_timeout=self.request_timeout,
                    ) as checker:
                        access = Permissions(checker, identity.id, str(identity.raw.get("roles", "")))
                        for channel in channels.values():
                            await checker.get_channel(channel.id)
                            await access.require(
                                "read_channel", team_id=channel.team_id, channel_id=channel.id
                            )
                            await access.require(
                                "create_post", team_id=channel.team_id, channel_id=channel.id
                            )
                    self._runtime_verified = True
                    runtime._resources = RuntimeResources(
                        MappingProxyType(teams),
                        MappingProxyType(channels),
                        bindings,
                        self.callback_url,
                        self.path_prefix,
                        self.report,
                    )
                    return runtime
        except BaseException as error:
            if self._current is not None:
                self._steps.append(ProvisioningStep(self._current, "failed", error_type=type(error).__name__))
            if runtime is not None:
                await runtime.close()
            raise
        finally:
            self._running = False

    async def load_runtime(self, router: Router) -> Bot:
        """Load a completed deployment using only persisted runtime/command secrets."""
        if self._running:
            raise RuntimeError("Bootstrap is already running")
        commands = collect_commands(router).commands if self.sync_commands else ()
        if len({command.trigger.casefold() for command in commands}) != len(commands):
            raise ProvisioningConflict(
                "Case-overlapping command declarations require an explicit unique trigger"
            )
        if commands and not self.teams:
            raise ValueError("Slash commands require declared team scopes")
        self._running, self._runtime_verified = True, False
        self._steps, self._current = [], None
        runtime: Bot | None = None
        try:
            async with self.store.session(self.scope) as journal:
                if any(i.phase in {"pending", "indeterminate"} for i in journal.intents()):
                    raise UnresolvedIntentError("Reconcile prior provisioning writes before loading runtime")
                bot_key = ResourceKey(kind="bot", name="runtime")
                token_key = ResourceKey(kind="bot-token", name="runtime")
                bot_binding = journal.binding(bot_key)
                credential = journal.secret(token_key)
                if bot_binding is None or credential is None:
                    raise MissingProvisioningSecret(
                        "Completed bot and runtime credential bindings are required"
                    )
                runtime = Bot(
                    token=credential.get_secret_value(),
                    base_url=self.scope.server,
                    request_timeout=self.request_timeout,
                )
                identity = await runtime.get_me()
                if (
                    identity.id != bot_binding.resource_id
                    or identity.username != self.bot_spec.username
                    or identity.raw.get("is_bot") is not True
                    or identity.raw.get("delete_at", 0)
                    or "system_admin" in str(identity.raw.get("roles", "")).split()
                ):
                    raise ProvisioningConflict(
                        "Persisted runtime credential differs or identifies an inactive/admin account"
                    )
                self._record(bot_key, "reused", identity.id)
                self._record(token_key, "reused")
                teams: dict[str, NativeTeam] = {}
                channels: dict[str, NativeChannel] = {}
                bindings_list: list[SlashBinding] = []
                async with SetupClient(
                    token=credential.get_secret_value(),
                    base_url=self.scope.server,
                    request_timeout=self.request_timeout,
                ) as checker:
                    access = Permissions(checker, identity.id, str(identity.raw.get("roles", "")))
                    for team_spec in self.teams:
                        key = ResourceKey(kind="team", name=team_spec.key)
                        binding = journal.binding(key)
                        if binding is None:
                            raise ProvisioningConflict("Declared team has no completed binding")
                        team = await checker.get_team(binding.resource_id)
                        self._check_team(team, team_spec)
                        teams[team_spec.key] = team
                        self._record(key, "reused", team.id)
                    for channel_spec in self.channels:
                        team = teams[channel_spec.team] if channel_spec.team else next(iter(teams.values()))
                        key = ResourceKey(kind="channel", team_id=team.id, name=channel_spec.key)
                        binding = journal.binding(key)
                        if binding is None:
                            raise ProvisioningConflict("Declared channel has no completed binding")
                        channel = await checker.get_channel(binding.resource_id)
                        self._check_channel(channel, channel_spec, team)
                        await access.require("read_channel", team_id=channel.team_id, channel_id=channel.id)
                        await access.require("create_post", team_id=channel.team_id, channel_id=channel.id)
                        channels[channel_spec.key] = channel
                        self._record(key, "reused", channel.id)
                    if self.sync_commands:
                        for team in teams.values():
                            for command_spec in commands:
                                key = ResourceKey(kind="command", team_id=team.id, name=command_spec.trigger)
                                binding = journal.binding(key)
                                secret = journal.secret(key)
                                desired = fingerprint(
                                    command_spec.to_payload(
                                        team_id=team.id,
                                        url=self.callback_url,
                                    )
                                )
                                if (
                                    binding is None
                                    or binding.ownership != "owned"
                                    or binding.owner_id is None
                                    or secret is None
                                ):
                                    raise MissingProvisioningSecret(
                                        "Every declared command requires an owned binding and secret"
                                    )
                                if binding.fingerprint != desired:
                                    raise ProvisioningConflict(
                                        "Command manifest changed; run reconciliation with a setup credential"
                                    )
                                bindings_list.append(
                                    SlashBinding(
                                        team.id,
                                        command_spec.trigger,
                                        secret,
                                        method=command_spec.method,
                                    )
                                )
                                self._record(key, "reused", binding.resource_id)
                self._runtime_verified = True
                runtime._resources = RuntimeResources(
                    MappingProxyType(teams),
                    MappingProxyType(channels),
                    tuple(bindings_list),
                    self.callback_url,
                    self.path_prefix,
                    self.report,
                )
                return runtime
        except BaseException as error:
            if self._current is not None:
                self._steps.append(ProvisioningStep(self._current, "failed", error_type=type(error).__name__))
            if runtime is not None:
                await runtime.close()
            raise
        finally:
            self._running = False
