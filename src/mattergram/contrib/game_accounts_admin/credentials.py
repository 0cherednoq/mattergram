from __future__ import annotations

from dataclasses import dataclass

from mattergram import Bot

from .mattermost_ui.account_card import build_account_card_blocks


@dataclass(frozen=True, slots=True)
class DenyCredentialRevealPolicy:
    async def allows(self, actor_id: str, channel_id: str) -> bool:
        del actor_id, channel_id
        return False


@dataclass(frozen=True, slots=True)
class MattermostDirectMessageCredentialPolicy:
    bot: Bot

    async def allows(self, actor_id: str, channel_id: str) -> bool:
        del actor_id
        channel = await self.bot.get_channel(channel_id)
        return channel.type == "D"


__all__ = [
    "DenyCredentialRevealPolicy",
    "MattermostDirectMessageCredentialPolicy",
    "build_account_card_blocks",
]
