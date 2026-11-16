"""Create an owned local alert channel through the bot and publish one mentioned alert."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mattergram import Bot
from mattergram.contrib.game_accounts_admin import MattermostPushDispatcher, PushRule
from mattergram.provisioning import SetupClient
from mattergram.types import MessagePriority

ROOT = Path(__file__).resolve().parents[1]


def _state(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Invalid local state: {path}")
    return value


async def run(args: argparse.Namespace) -> None:
    runtime = _state(args.runtime_state)
    stand = _state(args.stand_state)
    base_url = runtime["base_url"]
    bot_token = stand["bot_token"]
    team_id = stand["team_id"]
    admin_id = stand["admin_id"]
    admin_username = stand["admin_username"]
    suffix = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    channel_name = f"game-alerts-{suffix}"

    bot = Bot(token=bot_token, base_url=base_url)
    setup = SetupClient(token=bot_token, base_url=base_url)
    try:
        identity = await bot.get_me()
        if identity.id != stand["bot_id"]:
            raise RuntimeError("Local runtime token belongs to another bot")
        channel = await bot.create_channel(
            team_id=team_id,
            name=channel_name,
            display_name="Game Account Alerts",
            purpose="Local delivery test for Game Admin push events",
            header="Created by mattergram-ui-bot for an alert delivery test.",
        )
        await setup.add_channel_member(channel.id, admin_id)
        priority = MessagePriority(args.priority) if args.priority is not None else None
        dispatcher = MattermostPushDispatcher(
            bot,
            mentions=(admin_username,),
            priority=priority,
        )
        post = await dispatcher.deliver(
            PushRule(
                id="local-alert-test",
                destination_kind="channel",
                destination_id=channel.id,
                events=("account.invalid",),
            ),
            "account.invalid",
            "Тест алерта: игровой аккаунт требует проверки.",
        )
        print(
            json.dumps(
                {
                    "status": "passed",
                    "channel_id": channel.id,
                    "channel_name": channel_name,
                    "post_id": post.post_id,
                    "post_url": f"{base_url}/pl/{post.post_id}",
                    "mentioned": admin_username,
                    "silent": False,
                    "priority": args.priority,
                    "sound_note": "Client notification sound depends on the mentioned user's settings.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        await setup.close()
        await bot.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime-state",
        type=Path,
        default=ROOT / "build/game-accounts-admin-demo-state.json",
    )
    parser.add_argument(
        "--stand-state",
        type=Path,
        default=ROOT / "build/ui-stand-state.json",
    )
    parser.add_argument("--priority", choices=("important", "urgent"))
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
