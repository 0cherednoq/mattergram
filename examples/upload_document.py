"""Upload one local file and attach it to a native Mattermost post."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from mattergram import Bot
from mattergram.types import FSInputFile


async def upload(channel_id: str, path: Path, text: str | None) -> None:
    async with Bot(token=os.environ["MM_BOT_TOKEN"], base_url=os.environ["MM_URL"]) as bot:
        message = await bot.send_document(channel_id, FSInputFile(path), text=text)
        print(message.post_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("channel_id")
    parser.add_argument("path", type=Path)
    parser.add_argument("--text")
    args = parser.parse_args()
    asyncio.run(upload(args.channel_id, args.path, args.text))


if __name__ == "__main__":
    main()
