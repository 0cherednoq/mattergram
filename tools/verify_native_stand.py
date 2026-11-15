"""Bootstrap fixtures and verify the client on the explicitly owned local Docker stand.

Uses administrative credentials only in this development harness. Runtime Bot
uses a separately issued bot token. State/secrets stay in ignored build/ files.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from mattergram import Bot, Dispatcher, F, Router
from mattergram.exceptions import AuthenticationError, PermissionDeniedError
from mattergram.fsm.context import FSMContext
from mattergram.fsm.storage.base import StorageKey
from mattergram.fsm.storage.redis import RedisStorage
from mattergram.normalizers import normalize_posted
from mattergram.types import ErrorEvent, Message, RawEvent
from tools._common import ROOT, read_json, write_json


async def verify() -> dict[str, Any]:
    compose = [
        "docker",
        "compose",
        "--env-file",
        str(ROOT / "build/native-stand.env"),
        "-p",
        "mattergram-native-stand",
        "-f",
        str(ROOT / "infra/native-stand.compose.yml"),
    ]
    port = subprocess.run(
        [*compose, "port", "mattermost", "8065"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if not port.startswith("127.0.0.1:") or not port.removeprefix("127.0.0.1:").isdigit():
        raise ValueError("Stand must expose its API on loopback only")
    base = "http://" + port
    state_path = ROOT / "build/native-stand-state.json"
    state: dict[str, Any] = (
        read_json(state_path)
        if state_path.exists()
        else {"admin_username": "mattergram-stand-admin", "admin_password": "MG!" + secrets.token_urlsafe(24)}
    )
    write_json(state_path, state)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as control:
        server_version = None
        for attempt in range(40):
            try:
                async with control.get(base + "/api/v4/system/ping") as response:
                    if response.status == 200:
                        server_version = response.headers.get("X-Version-Id")
                        break
            except (aiohttp.ClientError, TimeoutError):
                pass
            await asyncio.sleep(0.5)
        else:
            raise RuntimeError("Owned Mattermost stand did not become ready")

        async def request(
            method: str,
            path: str,
            body: dict[str, Any] | None = None,
            token: str | None = None,
            expected: tuple[int, ...] = (200, 201),
        ) -> tuple[dict[str, Any], str | None]:
            async with control.request(
                method,
                base + "/api/v4/" + path,
                json=body,
                headers={"Authorization": "Bearer " + token} if token else {},
            ) as response:
                if response.status not in expected:
                    raise RuntimeError(
                        f"Owned stand fixture request {method} {path} returned HTTP {response.status}"
                    )
                data = await response.json()
                if not isinstance(data, dict):
                    raise RuntimeError("Owned stand returned an invalid fixture object")
                return data, response.headers.get("Token")

        if not state.get("admin_id"):
            user, _ = await request(
                "POST",
                "users",
                {
                    "username": state["admin_username"],
                    "email": "mattergram-stand-admin@example.test",
                    "password": state["admin_password"],
                },
            )
            state["admin_id"] = user["id"]
            write_json(state_path, state)
        admin, admin_token = await request(
            "POST", "users/login", {"login_id": state["admin_username"], "password": state["admin_password"]}
        )
        if not admin_token or "system_admin" not in admin.get("roles", "").split():
            raise RuntimeError("Owned stand bootstrap identity is not its system administrator")
        if not state.get("team_id"):
            team, _ = await request(
                "POST",
                "teams",
                {"name": "mattergram-native-stand", "display_name": "Mattergram native stand", "type": "O"},
                admin_token,
            )
            state["team_id"] = team["id"]
            write_json(state_path, state)
        if not state.get("bot_id"):
            bot_record, _ = await request(
                "POST",
                "bots",
                {"username": "mattergram-native-bot", "display_name": "Mattergram native bot"},
                admin_token,
            )
            state["bot_id"] = bot_record["user_id"]
            write_json(state_path, state)
            await request(
                "POST",
                f"teams/{state['team_id']}/members",
                {"team_id": state["team_id"], "user_id": state["bot_id"]},
                admin_token,
            )
        if not state.get("bot_token"):
            credential, _ = await request(
                "POST",
                f"users/{state['bot_id']}/tokens",
                {"description": "mattergram native stand fixture"},
                admin_token,
            )
            state["bot_token"] = credential["token"]
            write_json(state_path, state)
        for key, name, kind in (
            ("channel_id", "native-client-test", "O"),
            ("private_channel_id", "native-forbidden-test", "P"),
        ):
            if not state.get(key):
                channel, _ = await request(
                    "POST",
                    "channels",
                    {"team_id": state["team_id"], "name": name, "display_name": name, "type": kind},
                    admin_token,
                )
                state[key] = channel["id"]
                write_json(state_path, state)
                if key == "channel_id":
                    await request(
                        "POST", f"channels/{channel['id']}/members", {"user_id": state["bot_id"]}, admin_token
                    )

        async with Bot(token=state["bot_token"], base_url=base) as bot:
            assert bot.user_id == state["bot_id"]
            async with control.ws_connect(base + "/api/v4/websocket", heartbeat=10) as websocket:
                await websocket.send_json(
                    {"seq": 1, "action": "authentication_challenge", "data": {"token": state["bot_token"]}}
                )
                for _ in range(30):
                    acknowledgement = await websocket.receive_json(timeout=10)
                    if acknowledgement.get("seq_reply") == 1:
                        assert acknowledgement.get("status") == "OK"
                        break
                else:
                    raise RuntimeError("Stand WebSocket authentication was not acknowledged")

                async def posted(post_id: str) -> dict[str, Any]:
                    for _ in range(100):
                        update = await websocket.receive_json(timeout=10)
                        if (
                            update.get("event") == "posted"
                            and json.loads(update["data"]["post"]).get("id") == post_id
                        ):
                            return dict(update)
                    raise RuntimeError("Stand did not deliver the matching posted event")

                original, _ = await request(
                    "POST",
                    "posts",
                    {
                        "channel_id": state["channel_id"],
                        "message": "mattergram native stand ping " + secrets.token_hex(4),
                    },
                    admin_token,
                )
                observed_update = await posted(original["id"])
                write_json(ROOT / "build/reports/native-posted-wire.json", observed_update)
                incoming = normalize_posted(observed_update, bot=bot, self_user_id=state["bot_id"])
                assert incoming is not None
                answer = await incoming.answer("mattergram native stand pong")
                read_back, _ = await request("GET", "posts/" + answer.post_id, token=admin_token)
                assert read_back["id"] == answer.post_id and read_back["message"] == answer.text
                assert not read_back["root_id"]
                assert (
                    normalize_posted(await posted(answer.post_id), bot=bot, self_user_id=state["bot_id"])
                    is None
                )
                reply = await incoming.reply("mattergram thread reply")
                nested = await reply.reply("mattergram nested reply")
                assert reply.root_id == original["id"] == nested.root_id
            try:
                await bot.send_message(state["private_channel_id"], "must not be allowed")
            except PermissionDeniedError as error:
                assert error.status == 403
            else:
                raise AssertionError("Unjoined bot wrote to a private channel")
        assert bot.is_closed
        invalid = Bot(token="invalid-local-test-token", base_url=base)
        try:
            async with invalid:
                raise AssertionError("Invalid token entered context")
        except AuthenticationError as error:
            assert error.status == 401 and invalid.is_closed

        redis_url = os.environ.get("MATTERGRAM_REDIS_URL")
        external_storage = None
        isolation = None
        if redis_url:
            parsed = urlsplit(redis_url)
            if (
                parsed.scheme != "redis"
                or parsed.hostname != "127.0.0.1"
                or parsed.username
                or parsed.password
            ):
                raise ValueError("Native fixture accepts only a loopback Redis stand")
            external_storage = RedisStorage.from_url(redis_url)
            isolation = external_storage.create_isolation()
        dispatcher = Dispatcher(storage=external_storage, events_isolation=isolation)
        router = dispatcher.include_router(Router(name="stand-native-routing"))
        routing_bot = Bot(token=state["bot_token"], base_url=base)
        ready, replied, denied = asyncio.Event(), asyncio.Event(), asyncio.Event()
        finished = asyncio.Event()
        expected_bot_id = state["bot_id"]
        marker = "mattergram routing " + secrets.token_hex(8)
        handled: list[str] = []
        replies: list[Message] = []

        @router.raw_event(F.event_type == "hello")
        async def hello(event: RawEvent) -> None:
            ready.set()

        @router.message(F.text == marker)
        async def routed(event: Message, bot: Bot, state: FSMContext) -> None:
            assert bot is routing_bot and event.context is not None
            assert event.context.bot_id == expected_bot_id
            await state.set_state("stand:waiting")
            await state.set_data({"marker": marker})
            handled.append(event.post_id)
            replies.append(await event.answer(marker + " pong"))
            replied.set()

        @router.message(F.text == marker + " finish")
        async def finish(event: Message, state: FSMContext, raw_state: str | None) -> None:
            assert raw_state == "stand:waiting"
            assert await state.get_data() == {"marker": marker}
            await state.clear()
            finished.set()

        @router.message(F.text == marker + " forbidden")
        async def forbidden(event: Message, bot: Bot) -> None:
            await bot.send_message(state["private_channel_id"], "must not be allowed")

        @router.error(lambda event: isinstance(event.exception, PermissionDeniedError))
        async def permission_error(event: ErrorEvent) -> None:
            assert isinstance(event.event, Message)
            assert event.event.text == marker + " forbidden"
            assert isinstance(event.exception, PermissionDeniedError)
            assert event.exception.status == 403
            denied.set()

        task = asyncio.create_task(dispatcher.start_websocket(routing_bot))
        try:
            await asyncio.wait_for(ready.wait(), 15)
            routed_post, _ = await request(
                "POST", "posts", {"channel_id": state["channel_id"], "message": marker}, admin_token
            )
            await asyncio.wait_for(replied.wait(), 15)
            verified_reply, _ = await request("GET", "posts/" + replies[0].post_id, token=admin_token)
            assert verified_reply["message"] == marker + " pong"
            assert verified_reply["user_id"] == state["bot_id"]
            assert handled == [routed_post["id"]]

            async def self_seen() -> None:
                while dispatcher.ignored_self_events < 1:
                    if task.done():
                        await task
                        raise AssertionError("Dispatcher stopped before receiving its own event")
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(self_seen(), 15)
            if external_storage is not None and redis_url is not None:
                observer = RedisStorage.from_url(redis_url)
                try:
                    state_key = StorageKey(
                        routing_bot.instance_id, expected_bot_id, state["channel_id"], state["admin_id"]
                    )
                    assert await observer.get_state(state_key) == "stand:waiting"
                    assert await observer.get_data(state_key) == {"marker": marker}
                finally:
                    await observer.close()
            await request(
                "POST",
                "posts",
                {"channel_id": state["channel_id"], "message": marker + " finish"},
                admin_token,
            )
            await asyncio.wait_for(finished.wait(), 15)
            await request(
                "POST",
                "posts",
                {"channel_id": state["channel_id"], "message": marker + " forbidden"},
                admin_token,
            )
            await asyncio.wait_for(denied.wait(), 15)
        finally:
            try:
                await dispatcher.stop()
                await asyncio.wait_for(task, 15)
            finally:
                if isolation is not None:
                    await isolation.close()
                if external_storage is not None:
                    await external_storage.close()
        assert routing_bot.is_closed and dispatcher._closed
        assert not dispatcher._running

        invalid_dispatcher = Dispatcher()
        invalid_bot = Bot(token="invalid-local-test-token", base_url=base)
        try:
            await invalid_dispatcher.start_websocket(invalid_bot)
        except AuthenticationError as error:
            assert error.status == 401 and invalid_bot.is_closed and invalid_dispatcher._closed
        else:
            raise AssertionError("Dispatcher accepted an invalid token")
    return {
        "result": "passed",
        "checked_at": datetime.now(UTC).isoformat(),
        "scope": "Real Mattermost REST, SDK-owned WebSocket, Router/F/DI/handler/answer, typed errors and lifecycle cleanup.",
        "server_version_header": server_version,
        "server_image": "mattermost/mattermost-team-edition:11.1.0@sha256:4862f28825a7d5d4624d48493061587344605a14f060d9b0cf0b189d896efe9e",
        "post_id": answer.post_id,
        "routing": {
            "human_post_id": routed_post["id"],
            "answer_post_id": replies[0].post_id,
            "handler_calls": len(handled),
            "ignored_self_events": dispatcher.ignored_self_events,
            "permission_error_observed": denied.is_set(),
            "bot_closed": routing_bot.is_closed,
            "dispatcher_closed": dispatcher._closed,
            "fsm_profile": "redis" if redis_url else "memory",
            "state_transition_and_clear": finished.is_set(),
        },
        "checks": [
            "bot identity via issued bot token",
            "human posted event captured from real WebSocket",
            "answer created and read back",
            "own outgoing posted event ignored by normalizer",
            "reply and nested reply use original root",
            "private channel denied with 403",
            "invalid token rejected with 401 and startup cleanup",
            "owned client closed",
            "SDK-owned authenticated WebSocket delivered posted through Router/F/DI to answer",
            "routed answer read back and its own WebSocket event suppressed before handlers",
            "handler permission failure delivered to native error observer",
            "Dispatcher invalid-token startup and normal-stop cleanup",
            "two real human WS events transition and clear FSM state/data",
        ],
        "credentials": "not included; fixture state only in ignored build/native-stand-state.json",
    }


def main() -> None:
    report = asyncio.run(verify())
    path = ROOT / (
        "build/reports/native-stand-redis.json"
        if os.environ.get("MATTERGRAM_REDIS_URL")
        else "build/reports/native-stand.json"
    )
    write_json(path, report)
    print(f"real native Mattermost stand passed; evidence: {path}")


if __name__ == "__main__":
    main()
