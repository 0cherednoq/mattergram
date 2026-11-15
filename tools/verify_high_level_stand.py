"""Exercise the E7-02 high-level facade against the owned Mattermost stand."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime
from typing import Any

import aiohttp

from mattergram import Bot
from mattergram.types import BufferedInputFile
from tools._common import ROOT, read_json, sha256, write_json


def owned_endpoint() -> tuple[str, str | None]:
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
    return "http://" + port, identifier


async def verify() -> dict[str, Any]:
    base_url, _ = owned_endpoint()
    state: dict[str, Any] = read_json(ROOT / "build/native-stand-state.json")
    required = {"admin_id", "bot_id", "bot_token", "channel_id"}
    if required - state.keys() or not all(isinstance(state[name], str) and state[name] for name in required):
        raise RuntimeError("Run tools.verify_native_stand to prepare ordinary bot fixtures")

    payload = b"mattergram E7-02 streaming upload\x00\xff"
    executed: list[str] = []
    remaining_posts: dict[str, str] = {}
    server_version: str | None = None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as control:
        async with control.get(base_url + "/api/v4/system/ping") as response:
            if response.status != 200:
                raise RuntimeError("Owned Mattermost stand is not healthy")
            server_version = response.headers.get("X-Version-Id")

    async with Bot(token=state["bot_token"], base_url=base_url) as bot:
        try:
            user = await bot.get_user(state["bot_id"])
            executed.append("Bot.get_user")
            if user.id != state["bot_id"]:
                raise RuntimeError("High-level user lookup returned another identity")

            channel = await bot.get_channel(state["channel_id"])
            executed.append("Bot.get_channel")
            if channel.id != state["channel_id"]:
                raise RuntimeError("High-level channel lookup returned another channel")

            direct = await bot.create_direct_channel(state["admin_id"])
            executed.append("Bot.create_direct_channel")
            if direct.type != "D":
                raise RuntimeError("High-level direct-channel creation returned another type")

            direct_message = await bot.send_message(direct.id, "mattergram E7-02 facade")
            remaining_posts[direct_message.post_id] = direct.id
            edited = await bot.edit_message_text(
                direct.id, direct_message.post_id, "mattergram E7-02 facade edited"
            )
            executed.append("Bot.edit_message_text")
            if edited.text != "mattergram E7-02 facade edited":
                raise RuntimeError("High-level edit was not observed")
            await bot.delete_message(direct.id, direct_message.post_id)
            executed.append("Bot.delete_message")
            remaining_posts.pop(direct_message.post_id)

            shortcut_message = await bot.send_message(state["channel_id"], "mattergram E7-02 shortcut")
            remaining_posts[shortcut_message.post_id] = state["channel_id"]
            shortcut_edited = await shortcut_message.edit_text("mattergram E7-02 shortcut edited")
            executed.append("Message.edit_text")
            if shortcut_edited.text != "mattergram E7-02 shortcut edited":
                raise RuntimeError("Message edit shortcut was not observed")
            await shortcut_edited.delete()
            executed.append("Message.delete")
            remaining_posts.pop(shortcut_message.post_id)

            document_message = await bot.send_document(
                state["channel_id"],
                BufferedInputFile(payload, "e7-02.bin", chunk_size=7),
                text="mattergram E7-02 document",
            )
            executed.append("Bot.send_document")
            remaining_posts[document_message.post_id] = state["channel_id"]
            file_ids = document_message.raw.get("file_ids")
            if not isinstance(file_ids, list) or len(file_ids) != 1 or not isinstance(file_ids[0], str):
                raise RuntimeError("Document post omitted its native file ID")
            async with aiohttp.ClientSession(
                headers={"Authorization": "Bearer " + state["bot_token"]},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as control:
                async with control.get(base_url + "/api/v4/files/" + file_ids[0]) as response:
                    downloaded = await response.read()
                    if response.status != 200 or downloaded != payload:
                        raise RuntimeError("Uploaded native file did not round trip byte-for-byte")
            await document_message.delete()
            remaining_posts.pop(document_message.post_id)
        finally:
            for post_id, channel_id in sorted(remaining_posts.items()):
                try:
                    await bot.delete_message(channel_id, post_id)
                except Exception:
                    pass

    return {
        "result": "passed",
        "checked_at": datetime.now(UTC).isoformat(),
        "server_version": server_version,
        "credential": "ordinary bot token; value omitted",
        "executed_facade_methods": executed,
        "streaming_upload_bytes": len(payload),
        "streaming_upload_chunk_size": 7,
        "download_verification": "ordinary bot credential; exact bytes matched",
        "cleanup": "created posts soft-deleted; direct channel creation is idempotent",
        "source_hashes": {
            "bot": sha256((ROOT / "src/mattergram/bot.py").read_bytes()),
            "http_client": sha256((ROOT / "src/mattergram/_http_client.py").read_bytes()),
            "input_file": sha256((ROOT / "src/mattergram/types/input_file.py").read_bytes()),
            "message": sha256((ROOT / "src/mattergram/types/base.py").read_bytes()),
        },
    }


def main() -> None:
    report = asyncio.run(verify())
    path = ROOT / "build/reports/high-level-api-stand.json"
    write_json(path, report)
    print(f"high-level facade verified: {len(report['executed_facade_methods'])} methods")


if __name__ == "__main__":
    main()
