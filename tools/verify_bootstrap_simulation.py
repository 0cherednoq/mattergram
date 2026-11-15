"""Exercise Bootstrap, recovery and runtime-only handoff through the local Postman simulation."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import aiohttp
from cryptography.fernet import Fernet
from pydantic import SecretBytes, SecretStr

from mattergram import Router
from mattergram.exceptions import PermissionDeniedError, RequestOutcomeUnknown, UnsupportedCapabilityError
from mattergram.filters import Command
from mattergram.provisioning import (
    Bootstrap,
    BotSpec,
    ChannelSpec,
    FernetCodec,
    MemberSpec,
    ProvisioningConflict,
    ProvisioningScope,
    SQLiteProvisioningStore,
    TeamSpec,
)
from mattergram.provisioning.client import SetupClient
from mattergram.provisioning.store import UnresolvedIntentError
from tools._common import ROOT, write_json


async def callback(event: object) -> None:
    return None


def routes(*, changed: bool = False) -> Router:
    router = Router()
    router.slash_command.register(
        callback,
        Command("help"),
        description="Changed help" if changed else "Help",
        hint="[changed]" if changed else "[topic]",
    )
    return router


def declarations() -> tuple[TeamSpec, ChannelSpec]:
    return (
        TeamSpec(
            key="engineering",
            name="engineering",
            display_name="Engineering",
            visibility="open",
            create_if_missing=True,
            existing_policy="reuse",
        ),
        ChannelSpec(
            key="alerts",
            team="engineering",
            name="alerts",
            display_name="Alerts",
            visibility="private",
            header="Initial",
            create_if_missing=True,
            existing_policy="reuse",
            members=(MemberSpec(username="ordinary"),),
        ),
    )


def deployment(
    base: str,
    state_dir: Path,
    master: bytes,
    *,
    application: str,
    setup_token: str | None = "fixture-bootstrap-setup",
    changed: bool = False,
    change_server_settings: bool = False,
    request_timeout: float = 2.0,
) -> Bootstrap:
    team, channel = declarations()
    if changed:
        channel = channel.model_copy(update={"header": "Changed"})
    return Bootstrap(
        base_url=base,
        token=setup_token,
        application=application,
        bot=BotSpec(username="bootstrap-bot"),
        store=SQLiteProvisioningStore(
            state_dir,
            codec=FernetCodec(SecretBytes(master)),
        ),
        callback_base_url="https://callback.test",
        teams=(team,),
        channels=(channel,),
        change_server_settings=change_server_settings,
        request_timeout=request_timeout,
    )


async def verify(base: str) -> None:
    checks: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="mattergram-bootstrap-sim-") as temporary:
        root = Path(temporary)
        master = Fernet.generate_key()
        async with aiohttp.ClientSession() as control:

            async def call(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
                method = control.post if body is not None else control.get
                request = method(base + path, json=body) if body is not None else method(base + path)
                async with request as response:
                    assert response.status == 200, (path, response.status)
                    result: dict[str, Any] = await response.json()
                    return result

            async def reset(scenario: str = "normal") -> None:
                await call("/__control/reset", {"scenario": scenario})

            async def state() -> dict[str, Any]:
                return await call("/__control/state")

            await reset()
            state_dir = root / "repeat"
            first_deployment = deployment(base, state_dir, master, application="repeat")
            first = await first_deployment.prepare(routes())
            first_ids = {
                "team": first.resources.teams["engineering"].id,
                "channel": first.resources.channels["alerts"].id,
                "command": first.resources.report.steps[-1].resource_id,
            }
            first_command_secret = first.resources.slash_bindings[0].token.get_secret_value()
            await first.close()

            second = await deployment(base, state_dir, master, application="repeat").prepare(routes())
            assert all(step.status == "reused" for step in second.resources.report.steps)
            assert second.resources.slash_bindings[0].token.get_secret_value() == first_command_secret
            await second.close()

            changed = await deployment(base, state_dir, master, application="repeat", changed=True).prepare(
                routes(changed=True)
            )
            assert sum(step.status == "updated" for step in changed.resources.report.steps) == 2
            assert changed.resources.slash_bindings[0].token.get_secret_value() == first_command_secret
            assert changed.resources.teams["engineering"].id == first_ids["team"]
            assert changed.resources.channels["alerts"].id == first_ids["channel"]
            await changed.close()
            repeated = await state()
            assert repeated["counts"] == {
                "bots": 1,
                "teams": 1,
                "channels": 1,
                "commands": 1,
                "tokens": 1,
                "team_members": 3,
                "channel_members": 3,
            }
            assert repeated["command_metadata"][0]["description"] == "Changed help"
            assert repeated["channel_metadata"][0]["header"] == "Changed"
            checks["repeat_and_update"] = {
                "identical_second_run": "all steps reused",
                "updates": 2,
                "resource_ids_preserved": True,
                "runtime_and_command_secrets_preserved": True,
                "duplicates": 0,
            }

            before_runtime = len(repeated["calls"])
            runtime = await deployment(
                base,
                state_dir,
                master,
                application="repeat",
                setup_token=None,
                changed=True,
            ).load_runtime(routes(changed=True))
            assert runtime.resources.report.runtime_verified
            assert not runtime.resources.report.callback_verified
            assert runtime.resources.slash_bindings[0].token.get_secret_value() == first_command_secret
            await runtime.close()
            runtime_calls = (await state())["calls"][before_runtime:]
            assert runtime_calls and {entry["actor"] for entry in runtime_calls} == {"bootstrap-bot-1"}
            assert not any(entry["method"] != "GET" for entry in runtime_calls)
            checks["runtime_without_setup"] = {
                "setup_actor_calls": 0,
                "writes": 0,
                "runtime_verified": True,
                "callback_readiness_inferred": False,
            }

            await reset("forbid-team")
            denied_dir = root / "denied"
            denied = deployment(base, denied_dir, master, application="denied")
            try:
                await denied.prepare(routes())
            except PermissionDeniedError:
                pass
            else:
                raise AssertionError("403 provisioning write was accepted")
            denied_store = SQLiteProvisioningStore(denied_dir, codec=FernetCodec(SecretBytes(master)))
            denied_scope = ProvisioningScope(server=base, application="denied")
            async with denied_store.session(denied_scope) as journal:
                assert journal.intents()[-1].phase == "aborted"
                assert not any(intent.phase in {"pending", "indeterminate"} for intent in journal.intents())
            await call("/__control/scenario", {"scenario": "normal"})
            retried = await denied.prepare(routes())
            await retried.close()
            assert (await state())["counts"]["teams"] == 1
            checks["conclusive_403"] = "aborted journal step, explicit error, clean retry, one team"

            await reset("team-write-delay")
            lost_dir = root / "lost"
            lost = deployment(
                base,
                lost_dir,
                master,
                application="lost",
                request_timeout=0.25,
            )
            try:
                await lost.prepare(routes())
            except RequestOutcomeUnknown:
                pass
            else:
                raise AssertionError("Committed-before-timeout team write was confirmed")
            assert (await state())["counts"]["teams"] == 1
            try:
                await deployment(base, lost_dir, master, application="lost", request_timeout=2).prepare(
                    routes()
                )
            except UnresolvedIntentError:
                pass
            else:
                raise AssertionError("Unreconciled lost response was retried")
            await call("/__control/scenario", {"scenario": "normal"})
            async with SetupClient(
                token="fixture-bootstrap-setup", base_url=base, request_timeout=2
            ) as setup:
                recovered_team = await setup.get_team_by_name("engineering")
            lost_store = SQLiteProvisioningStore(lost_dir, codec=FernetCodec(SecretBytes(master)))
            lost_scope = ProvisioningScope(server=base, application="lost")
            async with lost_store.session(lost_scope) as journal:
                unresolved = [
                    intent for intent in journal.intents() if intent.phase in {"pending", "indeterminate"}
                ]
                assert len(unresolved) == 1 and unresolved[0].key.kind == "team"
                journal.complete(unresolved[0], resource_id=recovered_team.id)
            recovered = await deployment(
                base, lost_dir, master, application="lost", request_timeout=2
            ).prepare(routes())
            await recovered.close()
            assert (await state())["counts"]["teams"] == 1
            checks["lost_response_recovery"] = {
                "write_attempts": 1,
                "automatic_retry_blocked": True,
                "explicit_native_id_reconciled": True,
                "duplicates": 0,
            }

            await reset("foreign-command")
            conflict_dir = root / "conflict"
            conflict = deployment(base, conflict_dir, master, application="conflict")
            try:
                await conflict.prepare(routes())
            except ProvisioningConflict:
                pass
            else:
                raise AssertionError("Foreign trigger was adopted or overwritten")
            assert (await state())["counts"]["commands"] == 0
            await call("/__control/scenario", {"scenario": "normal"})
            resumed = await conflict.prepare(routes())
            await resumed.close()
            conflict_state = await state()
            assert conflict_state["counts"]["commands"] == 1
            assert conflict_state["counts"]["bots"] == 1
            checks["foreign_trigger"] = (
                "explicit conflict; foreign command untouched; owned partial resources resumed"
            )

            await reset()
            await call(
                "/__control/features",
                {"commands": False, "bot_creation": False, "access_tokens": False},
            )
            settings_dir = root / "settings"
            try:
                await deployment(base, settings_dir, master, application="settings").prepare(routes())
            except UnsupportedCapabilityError:
                pass
            else:
                raise AssertionError("Disabled features were ignored")
            assert not any(entry["method"] != "GET" for entry in (await state())["calls"])
            configured = await deployment(
                base,
                settings_dir,
                master,
                application="settings",
                change_server_settings=True,
            ).prepare(routes())
            assert any(
                step.key.kind == "settings" and step.status == "updated"
                for step in configured.resources.report.steps
            )
            await configured.close()
            checks["feature_allowlist"] = (
                "disabled/unknown failed closed; explicit allowlist patch read back before provisioning"
            )

            await call("/__control/seed-limited", {"team_role": "team_admin"})
            limited_dir = root / "limited"
            limited_store = SQLiteProvisioningStore(limited_dir, codec=FernetCodec(SecretBytes(master)))
            limited = Bootstrap(
                base_url=base,
                token="fixture-limited-setup",
                application="limited",
                bot=BotSpec(
                    username="existing-bot",
                    existing_id="limited-bot",
                    existing_token=SecretStr("fixture-existing-runtime"),
                ),
                store=limited_store,
                callback_base_url="https://callback.test",
                team=TeamSpec(key="engineering", name="engineering", existing_policy="reuse"),
                request_timeout=2,
            )
            limited_runtime = await limited.prepare(routes())
            await limited_runtime.close()
            limited_state = await state()
            writes = [entry["path"] for entry in limited_state["calls"] if entry["method"] != "GET"]
            assert writes == ["/api/v4/commands"]
            checks["least_privileged_command_sync"] = {
                "global_bot_or_token_creation": False,
                "writes": ["command"],
                "runtime_is_non_admin": True,
            }

            await call("/__control/seed-limited", {"team_role": "team_user"})
            try:
                await Bootstrap(
                    base_url=base,
                    token="fixture-limited-setup",
                    application="limited-denied",
                    bot=BotSpec(
                        username="existing-bot",
                        existing_id="limited-bot",
                        existing_token=SecretStr("fixture-existing-runtime"),
                    ),
                    store=SQLiteProvisioningStore(
                        root / "limited-denied",
                        codec=FernetCodec(SecretBytes(master)),
                    ),
                    callback_base_url="https://callback.test",
                    team=TeamSpec(key="engineering", name="engineering", existing_policy="reuse"),
                    request_timeout=2,
                ).prepare(routes())
            except PermissionDeniedError:
                pass
            else:
                raise AssertionError("Missing manage_own_slash_commands was ignored")
            denied_state = await state()
            assert denied_state["counts"]["commands"] == 0
            assert not any(entry["path"] == "/api/v4/commands" for entry in denied_state["calls"])
            checks["least_privileged_denial"] = "permission denied before incomplete command list or write"

            await reset()
            race_dir = root / "race"
            race_a = deployment(base, race_dir, master, application="race")
            race_b = deployment(base, race_dir, master, application="race")
            race_results = await asyncio.gather(race_a.prepare(routes()), race_b.prepare(routes()))
            try:
                race_state = await state()
                assert race_state["counts"]["bots"] == 1
                assert race_state["counts"]["teams"] == 1
                assert race_state["counts"]["channels"] == 1
                assert race_state["counts"]["commands"] == 1
                created = [
                    sum(step.status == "created" for step in bot.resources.report.steps)
                    for bot in race_results
                ]
                assert min(created) == 0 and max(created) > 0
            finally:
                await asyncio.gather(*(bot.close() for bot in race_results))
            checks["replica_race"] = (
                "shared OS lock serialized two Bootstrap instances; one native resource set"
            )

    serialized = json.dumps(checks, ensure_ascii=False)
    for secret in (
        "fixture-bootstrap-setup",
        "fixture-existing-runtime",
        "bootstrap-runtime-secret-1",
        "bootstrap-command-secret-1",
    ):
        assert secret not in serialized
    write_json(
        ROOT / "build/reports/bootstrap-simulation.json",
        {
            "result": "passed",
            "scope": "Real Bootstrap/HTTP transport against an authored local Postman stand-in; provider fault behavior remains unclaimed",
            "checks": checks,
        },
    )
    print("Local Postman Bootstrap reconciliation/fault/runtime verification passed")


def verify_log(path: Path) -> None:
    payload = path.read_bytes()
    raw = (
        payload.decode("utf-16")
        if payload.startswith((b"\xff\xfe", b"\xfe\xff"))
        else payload.decode("utf-8")
    )
    for secret in (
        "fixture-bootstrap-setup",
        "fixture-existing-runtime",
        "bootstrap-runtime-secret",
        "bootstrap-command-secret",
    ):
        assert secret not in raw
    events = [json.loads(line) for line in raw.splitlines() if line]
    listening = [event for event in events if event.get("event") == "listening"]
    requests = [event for event in events if event.get("event") == "request"]
    assert len(listening) == 1 and requests
    assert {event.get("routeKey") for event in requests} == {"bootstrap"}
    methods = Counter(str(event.get("method")) for event in requests)
    statuses = Counter(int(event.get("statusCode")) for event in requests)
    paths = Counter(str(event.get("path")) for event in requests)
    for required in (
        "/api/v4/users/me",
        "/api/v4/config/client",
        "/api/v4/teams",
        "/api/v4/bots",
        "/api/v4/channels",
        "/api/v4/commands",
    ):
        assert any(
            count > 0 and (observed == required or observed.startswith(required + "?"))
            for observed, count in paths.items()
        )
    assert methods["GET"] > 0 and methods["POST"] > 0 and methods["PUT"] > 0
    assert statuses[200] > 0 and statuses[201] > 0 and statuses[403] > 0
    write_json(
        ROOT / "build/reports/bootstrap-simulation-log-check.json",
        {
            "result": "passed",
            "listening_events": len(listening),
            "request_events": len(requests),
            "route_keys": ["bootstrap"],
            "methods": dict(sorted(methods.items())),
            "statuses": {str(key): value for key, value in sorted(statuses.items())},
            "required_paths_observed": True,
            "known_credentials_absent": True,
        },
    )
    print("Postman Bootstrap simulation log verification passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url")
    parser.add_argument("--log", type=Path)
    arguments = parser.parse_args()
    if (arguments.base_url is None) == (arguments.log is None):
        parser.error("declare exactly one of --base-url or --log")
    if arguments.log is not None:
        verify_log(arguments.log)
    else:
        asyncio.run(verify(arguments.base_url))
