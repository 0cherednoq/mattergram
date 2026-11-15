"""Read identity and recent channel posts through reviewed low-level operations."""

from __future__ import annotations

import asyncio
import os

from mattergram import NativeAPI


async def main() -> None:
    async with NativeAPI(
        base_url=os.environ["MM_URL"],
        token=os.environ["MM_BOT_TOKEN"],
    ) as native:
        identity = await native.call("GetUser", path={"user_id": "me"})
        posts = await native.call(
            "GetPostsForChannel",
            path={"channel_id": os.environ["MM_CHANNEL_ID"]},
            query={"page": 0, "per_page": 20},
        )
        print(identity["username"], len(posts.get("order", [])))


if __name__ == "__main__":
    asyncio.run(main())
