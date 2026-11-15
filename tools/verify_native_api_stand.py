"""Exercise every generated E7-01 operation against the owned Mattermost stand."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime
from typing import Any

import aiohttp

from mattergram import NativeAPI
from mattergram.native import OPERATIONS, OVERLAY_SHA256, SOURCE_COMMIT
from tools._common import ROOT, read_json, sha256, write_json


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
    identifier = subprocess.run(
        [*compose, "ps", "-q", "mattermost"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if not identifier or len(identifier.splitlines()) != 1:
        raise RuntimeError("Owned Mattermost stand is not running")
    labels = subprocess.run(
        ["docker", "inspect", "--format", "{{json .Config.Labels}}", identifier],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if '"com.docker.compose.project":"mattergram-native-stand"' not in labels:
        raise RuntimeError("Mattermost fixture ownership differs")
    port = subprocess.run(
        [*compose, "port", "mattermost", "8065"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if not port.startswith("127.0.0.1:") or not port.removeprefix("127.0.0.1:").isdigit():
        raise RuntimeError("Mattermost stand must expose one loopback endpoint")
    state: dict[str, Any] = read_json(ROOT / "build/native-stand-state.json")
    required = {"bot_id", "bot_token", "team_id", "channel_id"}
    if required - state.keys() or not all(isinstance(state[name], str) and state[name] for name in required):
        raise RuntimeError("Run tools.verify_native_stand to prepare ordinary bot fixtures")
    base_url = "http://" + port
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as control:
        async with control.get(base_url + "/api/v4/system/ping") as response:
            if response.status != 200:
                raise RuntimeError("Owned Mattermost stand is not healthy")
            server_version = response.headers.get("X-Version-Id")

    executed: list[str] = []
    deviations: list[str] = []
    post_id: str | None = None
    async with NativeAPI(token=state["bot_token"], base_url=base_url) as native:

        async def call(operation: str, **kwargs: Any) -> Any:
            result = await native.call(operation, **kwargs)
            executed.append(operation)
            return result

        me = await call("GetUser", path={"user_id": "me"})
        if not isinstance(me, dict) or me.get("id") != state["bot_id"]:
            raise RuntimeError("Generated GetUser returned another identity")
        username = me.get("username")
        if not isinstance(username, str) or not username:
            raise RuntimeError("Generated GetUser omitted username")
        by_name = await call("GetUserByUsername", path={"username": username})
        if not isinstance(by_name, dict) or by_name.get("id") != state["bot_id"]:
            raise RuntimeError("Generated username lookup returned another identity")
        team = await call("GetTeam", path={"team_id": state["team_id"]})
        team_name = team.get("name") if isinstance(team, dict) else None
        if not isinstance(team_name, str) or not team_name:
            raise RuntimeError("Generated GetTeam omitted its name")
        team_by_name = await call("GetTeamByName", path={"name": team_name})
        if not isinstance(team_by_name, dict) or team_by_name.get("id") != state["team_id"]:
            raise RuntimeError("Generated team lookup returned another team")
        channel = await call("GetChannel", path={"channel_id": state["channel_id"]})
        channel_name = channel.get("name") if isinstance(channel, dict) else None
        if not isinstance(channel_name, str) or not channel_name:
            raise RuntimeError("Generated GetChannel omitted its name")
        channel_by_name = await call(
            "GetChannelByName",
            path={"team_id": state["team_id"], "channel_name": channel_name},
        )
        if not isinstance(channel_by_name, dict) or channel_by_name.get("id") != state["channel_id"]:
            raise RuntimeError("Generated channel lookup returned another channel")
        await call("GetUserStatus", path={"user_id": state["bot_id"]})
        preferences = await call("GetPreferences", path={"user_id": state["bot_id"]})
        if preferences is None:
            deviations.append(
                "Mattermost 11.1.0 returned JSON null for an account with no preferences; pinned OpenAPI says array"
            )
        elif not isinstance(preferences, list):
            raise RuntimeError(f"Generated GetPreferences returned unexpected {type(preferences).__name__}")
        await call(
            "GetPostsForChannel",
            path={"channel_id": state["channel_id"]},
            query={"page": 0, "per_page": 1},
        )
        created = await call(
            "CreatePost",
            body={"channel_id": state["channel_id"], "message": "mattergram generated E7-01"},
        )
        post_id = created.get("id") if isinstance(created, dict) else None
        if not isinstance(post_id, str) or not post_id:
            raise RuntimeError("Generated CreatePost omitted native ID")
        try:
            fetched = await call("GetPost", path={"post_id": post_id})
            if not isinstance(fetched, dict) or fetched.get("id") != post_id:
                raise RuntimeError("Generated GetPost returned another post")
            patched = await call(
                "PatchPost",
                path={"post_id": post_id},
                body={"id": post_id, "message": "mattergram generated E7-01 updated"},
            )
            if (
                not isinstance(patched, dict)
                or patched.get("message") != "mattergram generated E7-01 updated"
            ):
                raise RuntimeError("Generated PatchPost did not update message")
            await call("GetPostThread", path={"post_id": post_id})
            reaction = {"user_id": state["bot_id"], "post_id": post_id, "emoji_name": "+1"}
            await call("SaveReaction", body=reaction)
            reactions = await call("GetReactions", path={"post_id": post_id})
            if not isinstance(reactions, list) or not any(
                isinstance(item, dict) and item.get("emoji_name") == "+1" for item in reactions
            ):
                raise RuntimeError("Generated reaction round trip was not observed")
            await call(
                "DeleteReaction",
                path={"user_id": state["bot_id"], "post_id": post_id, "emoji_name": "+1"},
            )
        finally:
            await call("DeletePost", path={"post_id": post_id})

    if set(executed) != set(OPERATIONS) or len(executed) != len(OPERATIONS):
        raise RuntimeError("Native stand did not execute every generated operation exactly once")
    return {
        "result": "passed",
        "checked_at": datetime.now(UTC).isoformat(),
        "server_version": server_version,
        "source_commit": SOURCE_COMMIT,
        "overlay_sha256": OVERLAY_SHA256,
        "generated_operations": len(OPERATIONS),
        "executed_operation_ids": sorted(executed),
        "credential": "ordinary bot token; value omitted",
        "cleanup": "generated post soft-deleted; reaction removed before deletion",
        "provider_openapi_deviations": deviations,
        "source_hashes": {
            "client": sha256((ROOT / "src/mattergram/native/client.py").read_bytes()),
            "operation": sha256((ROOT / "src/mattergram/native/operation.py").read_bytes()),
            "generated": sha256((ROOT / "src/mattergram/native/_operations.py").read_bytes()),
            "overlay": sha256((ROOT / "manifests/native-api-overlays.json").read_bytes()),
        },
    }


def main() -> None:
    report = asyncio.run(verify())
    path = ROOT / "build/reports/native-api-stand.json"
    write_json(path, report)
    print(f"native API stand verified: {report['generated_operations']} ordinary-bot operations")


if __name__ == "__main__":
    main()
