"""Run actual SDK response_url requests against an owned Postman simulation."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

import aiohttp
from pydantic import SecretStr

from mattergram import Bot
from mattergram.exceptions import (
    APIError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
    RequestOutcomeUnknown,
    ResponseStateError,
)
from mattergram.http.delayed import DelayedResponder
from tools._common import ROOT, write_json


async def verify(base_url: str) -> dict[str, Any]:
    checks: dict[str, str] = {}
    bot = Bot(token="fixture-secret", base_url=base_url, request_timeout=1)
    try:
        async with aiohttp.ClientSession() as control:

            async def reset(scenario: str = "normal") -> None:
                async with control.post(
                    base_url + "/__control/reset", json={"scenario": scenario}
                ) as response:
                    assert response.status == 200

            async def state() -> dict[str, Any]:
                async with control.get(base_url + "/__control/state") as response:
                    assert response.status == 200
                    result: dict[str, Any] = await response.json()
                    return result

            url = SecretStr(base_url + "/hooks/commands/fixture-response")
            await reset()
            responder = DelayedResponder(bot, url)
            for i in range(5):
                await responder({"response_type": "in_channel", "text": f"followup {i}"})
            try:
                await responder({"text": "sixth"})
            except ResponseStateError:
                pass
            else:
                raise AssertionError("SDK accepted a sixth attempt")
            result = await state()
            assert len(result["delayed"]) == len(result["calls"]) == 5
            assert all(not call["authorization"] and not call["cookie"] for call in result["calls"])
            checks["five_bounded_attempts"] = (
                "passed: five real HTTP calls, sixth blocked locally, no Bearer or cookies"
            )
            for scenario, exception in (
                ("unauthorized", AuthenticationError),
                ("forbidden", PermissionDeniedError),
                ("rate-limit", RateLimitError),
                ("redirect", APIError),
                ("write-then-delay", RequestOutcomeUnknown),
            ):
                await reset(scenario)
                try:
                    await DelayedResponder(bot, url)({"text": "fault probe"})
                except exception as error:
                    assert "fixture-response" not in str(error) and "fixture-secret" not in str(error)
                    if isinstance(error, RateLimitError):
                        assert error.retry_after == "2"
                    if scenario == "redirect":
                        assert isinstance(error, APIError) and error.status == 302
                else:
                    raise AssertionError(f"Expected {exception.__name__}")
                result = await state()
                assert len(result["calls"]) == 1
                assert len(result["delayed"]) == (1 if scenario == "write-then-delay" else 0)
                checks[scenario] = (
                    "passed: one actual HTTP attempt, no retry or redirect; delayed state inspected"
                )
    finally:
        await bot.close()
    return {
        "result": "passed",
        "scope": "Real SDK against authored local Postman stand-in; does not establish provider fault behavior or server quota",
        "checks": checks,
        "cli": "1.82.0",
        "owner_discovery": "guest session; owner search unavailable; Orbit search returned 403; pinned primary sources used",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    path = ROOT / "build/reports/http-simulation.json"
    write_json(path, asyncio.run(verify(args.base_url.rstrip("/"))))
    print(f"HTTP simulation passed; evidence: {path}")


if __name__ == "__main__":
    main()
