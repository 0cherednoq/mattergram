"""Exercise real SDK form-open writes and uncertain sessions against a local Postman simulation."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

import aiohttp
from pydantic import SecretStr

from mattergram import Bot
from mattergram.exceptions import APIError, PermissionDeniedError, RateLimitError, RequestOutcomeUnknown
from mattergram.http import DialogManager
from mattergram.types import SlashCommand
from mattergram.ui import UIProfile
from mattergram.ui.forms import Form, TextInput
from tools._common import ROOT, write_json


async def verify(base_url: str) -> dict[str, Any]:
    checks: dict[str, str] = {}
    async with aiohttp.ClientSession() as control:

        async def reset(scenario: str) -> None:
            async with control.post(base_url + "/__control/reset", json={"scenario": scenario}) as response:
                assert response.status == 200

        async def state() -> dict[str, Any]:
            async with control.get(base_url + "/__control/state") as response:
                result: dict[str, Any] = await response.json()
                return result

        for scenario, expected in (
            ("normal", None),
            ("forbidden", PermissionDeniedError),
            ("rate-limit", RateLimitError),
            ("redirect", APIError),
            ("write-then-delay", RequestOutcomeUnknown),
        ):
            await reset(scenario)
            async with Bot(token="fixture-secret", base_url=base_url, request_timeout=1) as bot:
                manager = DialogManager(
                    bot,
                    profile=UIProfile(
                        "11.1.0",
                        client_features=frozenset({"forms.basic"}),
                        client_evidence="authored simulation",
                    ),
                    callback_url="https://integration.test/dialog",
                )
                try:
                    event = SlashCommand(
                        user_id="fixture-user",
                        channel_id="fixture-channel",
                        command="/form",
                        token=SecretStr("fixture-command"),
                        trigger_id=SecretStr("fixture-trigger"),
                    )
                    form = Form(
                        callback_id="f", title="Form", elements=(TextInput(name="title", label="Title"),)
                    )
                    try:
                        handle = await manager.open(event, form)
                    except Exception as error:
                        if expected is None or not isinstance(error, expected):
                            raise
                        if scenario == "redirect":
                            assert isinstance(error, APIError) and error.status == 302
                    else:
                        assert expected is None and handle.session_id
                    result = await state()
                    writes = [call for call in result["calls"] if call["method"] == "POST"]
                    assert len(writes) == 1 and writes[0]["path"] == "/api/v4/actions/dialogs/open"
                    if scenario in ("normal", "write-then-delay"):
                        assert len(result["dialogs"]) == 1
                        wire = result["dialogs"][0]
                        assert wire["dialog"]["state"].startswith("mgd1.")
                        assert wire["dialog"]["elements"][0]["display_name"] == "Title"
                    if scenario == "write-then-delay":

                        async def must_not_run(event: Any, response: Any) -> Any:
                            raise AssertionError("Uncertain form open was dispatched")

                        reply = await manager.handle(
                            {
                                "type": "dialog_submission",
                                "user_id": "fixture-user",
                                "channel_id": "fixture-channel",
                                "callback_id": "f",
                                "state": wire["dialog"]["state"],
                                "submission": {"title": "value"},
                            },
                            deadline=asyncio.get_running_loop().time() + 2,
                            serve=must_not_run,
                        )
                        assert reply.status == 409
                        checks[scenario] = (
                            "passed: one recorded native open, unknown outcome consumes session, later submit blocked without business dispatch"
                        )
                    else:
                        checks[scenario] = (
                            "passed: one actual SDK write, no retry or redirect; native payload/mock state inspected"
                        )
                finally:
                    await manager.close()
    return {
        "result": "passed",
        "scope": "SDK against authored local Postman stand-in; models faults, does not establish real provider fault outcomes or UI rendering",
        "cli": "1.82.0",
        "checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    path = ROOT / "build/reports/form-simulation.json"
    write_json(path, asyncio.run(verify(args.base_url.rstrip("/"))))
    print(f"Form simulation passed; evidence: {path}")


if __name__ == "__main__":
    main()
