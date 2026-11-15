"""Create one real post with MM_URL and MM_BOT_TOKEN in the environment."""

from __future__ import annotations

import argparse
import asyncio
import os

from mattergram import Bot


async def create(channel_id: str, text: str, root_id: str | None) -> None:
    token = os.environ.get("MM_BOT_TOKEN")
    base = os.environ.get("MM_URL")
    if not token or not base:
        raise SystemExit("Set MM_URL and MM_BOT_TOKEN before running this example")
    async with Bot(token=token, base_url=base) as bot:
        message = await bot.send_message(channel_id, text, root_id=root_id)
        print(message.post_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("channel_id")
    parser.add_argument("text")
    parser.add_argument("--root-id")
    args = parser.parse_args()
    asyncio.run(create(args.channel_id, args.text, args.root_id))


if __name__ == "__main__":
    main()
