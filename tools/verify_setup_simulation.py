"""Run the real setup client through local Postman HTTP/state, including lost writes."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

import aiohttp

from mattergram.exceptions import APIError, PermissionDeniedError, RateLimitError, RequestOutcomeUnknown
from mattergram.provisioning import CommandSpec, SetupClient
from tools._common import ROOT, write_json


async def verify(base: str) -> None:
    spec = CommandSpec("setupcheck", description="Owned", hint="[topic]")
    checks: dict[str, Any] = {}
    async with aiohttp.ClientSession() as control:

        async def reset(scenario: str) -> None:
            async with control.post(base + "/__control/reset", json={"scenario": scenario}) as response:
                assert response.status == 200

        async def state() -> dict[str, Any]:
            async with control.get(base + "/__control/state") as response:
                result: dict[str, Any] = await response.json()
                return result

        setup = SetupClient(token="fixture-setup-secret", base_url=base, request_timeout=1)
        try:
            await reset("normal")
            command = await setup.create_command(
                spec, team_id="fixtureteam", url="https://callback.test/slash"
            )
            assert command.token is not None
            assert "token" not in command.model_dump()
            assert (await setup.get_command(command.id)).trigger == spec.trigger
            updated = await setup.update_command(
                command.id,
                CommandSpec("setupcheck", hint="changed"),
                team_id="fixtureteam",
                url="https://callback.test/new",
            )
            assert (
                updated.token is not None
                and updated.token.get_secret_value() == command.token.get_secret_value()
            )
            assert len(await setup.list_commands("fixtureteam")) == 2
            await setup.delete_command(command.id)
            normal = await state()
            assert not normal["commands"] and len(normal["calls"]) == 5
            assert all(not call["cookie"] and call["authorization"] for call in normal["calls"])
            checks["crud_and_list"] = (
                "5 HTTP calls; native field shapes, preserved issued token, built-in without ID accepted; deletion observed"
            )
            for scenario, error_type in (
                ("forbidden", PermissionDeniedError),
                ("rate-limit", RateLimitError),
                ("redirect", APIError),
            ):
                await reset(scenario)
                try:
                    await setup.create_command(spec, team_id="fixtureteam", url="https://callback.test/slash")
                except error_type as error:
                    if scenario == "rate-limit":
                        assert error.retry_after == "2"
                else:
                    raise AssertionError("Setup error was not surfaced")
                saved = await state()
                assert len(saved["calls"]) == 1 and not saved["commands"]
                checks[scenario] = "one call, no retry, no write/redirect"
            for method in ("POST", "PUT", "DELETE"):
                await reset("normal")
                if method != "POST":
                    command = await setup.create_command(
                        spec, team_id="fixtureteam", url="https://callback.test/slash"
                    )
                # Changing only the fault preserves the write-before-timeout seed.
                async with control.post(
                    base + "/__control/scenario", json={"scenario": "write-then-delay"}
                ) as response:
                    assert response.status == 200
                before = len((await state())["calls"])
                try:
                    if method == "POST":
                        await setup.create_command(
                            spec, team_id="fixtureteam", url="https://callback.test/slash"
                        )
                    elif method == "PUT":
                        await setup.update_command(
                            command.id,
                            CommandSpec("setupcheck", hint="committed"),
                            team_id="fixtureteam",
                            url="https://callback.test/slash",
                        )
                    else:
                        await setup.delete_command(command.id)
                except RequestOutcomeUnknown:
                    pass
                else:
                    raise AssertionError("Lost setup write outcome was not classified unknown")
                saved = await state()
                assert len(saved["calls"]) == before + 1
                if method == "DELETE":
                    assert not saved["commands"]
                else:
                    assert len(saved["commands"]) == 1
                    if method == "PUT":
                        assert saved["commands"][0]["auto_complete_hint"] == "committed"
                checks[method + "_write_then_timeout"] = (
                    "state committed, RequestOutcomeUnknown, exactly one write attempt"
                )
        finally:
            await setup.close()
    write_json(
        ROOT / "build/reports/setup-simulation.json",
        {
            "result": "passed",
            "scope": "Real SetupClient against local authored Postman stand-in; not provider fault injection",
            "checks": checks,
        },
    )
    print("Local Postman setup CRUD/fault/state verification passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    asyncio.run(verify(parser.parse_args().base_url))
