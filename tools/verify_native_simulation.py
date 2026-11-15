"""Exercise the installed SDK path against a running local Postman simulation."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

import aiohttp

from mattergram import Bot
from mattergram.exceptions import (
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
    RequestOutcomeUnknown,
)
from mattergram.ui.actions import Actions, Button, External
from tools._common import ROOT, write_json


async def verify(base_url: str) -> dict[str, Any]:
    checks: dict[str, str] = {}
    async with aiohttp.ClientSession() as control:

        async def reset(scenario: str = "normal") -> None:
            async with control.post(base_url + "/__control/reset", json={"scenario": scenario}) as response:
                assert response.status == 200

        async def state() -> dict[str, Any]:
            async with control.get(base_url + "/__control/state") as response:
                assert response.status == 200
                result: dict[str, Any] = await response.json()
                return result

        await reset()
        async with Bot(token="fixture-secret", base_url=base_url) as bot:
            assert bot.user_id == "fixture-bot"
            message = await bot.send_message("fixture-channel", "simulation post")
            reply = await message.reply("simulation reply")
            assert reply.root_id == message.post_id
        result = await state()
        assert len(result["posts"]) == 2 and len(result["calls"]) == 3
        checks["identity_create_reply"] = "passed, two recorded posts and three SDK HTTP calls"
        await reset()
        async with Bot(token="fixture-secret", base_url=base_url) as bot:
            await bot.send_message(
                "fixture-channel",
                "UI message",
                actions=Actions(
                    items=(
                        Button(
                            name="Run",
                            action=External(
                                url="https://integration.test/callback", context={"fixture": True}
                            ),
                        ),
                    )
                ),
            )
        result = await state()
        assert len(result["posts"]) == 1 and len(result["calls"]) == 2
        action = result["posts"][0]["props"]["attachments"][0]["actions"][0]
        assert action["name"] == "Run" and action["integration"]["context"] == {"fixture": True}
        checks["native_ui_serialization"] = (
            "passed, actual SDK HTTP POST and preserved compiled action in mock state"
        )
        for scenario, exception in (
            ("forbidden", PermissionDeniedError),
            ("rate-limit", RateLimitError),
            ("write-then-delay", RequestOutcomeUnknown),
        ):
            await reset(scenario)
            async with Bot(token="fixture-secret", base_url=base_url, request_timeout=0.1) as bot:
                try:
                    await bot.send_message("fixture-channel", "failure case")
                except exception:
                    pass
                else:
                    raise AssertionError(f"Expected {exception.__name__}")
            result = await state()
            assert sum(call["method"] == "POST" for call in result["calls"]) == 1
            if scenario == "write-then-delay":
                assert len(result["posts"]) == 1
            checks[scenario] = "passed, one write attempt; no automatic retry"
        await reset()
        try:
            async with Bot(token="invalid-fixture-token", base_url=base_url):
                raise AssertionError("Invalid token entered context")
        except AuthenticationError:
            checks["unauthorized"] = "passed, 401 with startup cleanup"
    return {
        "scope": "Native SDK against authored local Postman HTTP stand-in, not a Mattermost server",
        "cli": "1.82.0",
        "result": "passed",
        "checks": checks,
        "owner_mock_search": "skipped: CLI guest session; pinned protocol contract used",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Simulation URL including its /mattermost routeKey")
    args = parser.parse_args()
    report = asyncio.run(verify(args.base_url.rstrip("/")))
    path = ROOT / "build/reports/native-simulation.json"
    write_json(path, report)
    print(f"native simulation passed; evidence: {path}")


if __name__ == "__main__":
    main()
