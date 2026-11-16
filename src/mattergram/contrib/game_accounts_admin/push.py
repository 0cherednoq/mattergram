"""Mattermost delivery adapter for Game Admin push rules."""

from __future__ import annotations

import re
from collections.abc import Sequence

from mattergram import Bot
from mattergram.types import Message, MessagePriority

from .models import PushEvent, PushRule

_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class MattermostPushDispatcher:
    """Deliver enabled Game Admin events as ordinary non-silent Mattermost posts."""

    def __init__(
        self,
        bot: Bot,
        *,
        mentions: Sequence[str] = (),
        priority: MessagePriority | None = None,
    ) -> None:
        if not isinstance(bot, Bot):
            raise TypeError("bot must be a mattergram Bot")
        if isinstance(mentions, (str, bytes)) or not isinstance(mentions, Sequence):
            raise TypeError("mentions must be a sequence of Mattermost usernames")
        if any(
            not isinstance(username, str) or _USERNAME.fullmatch(username) is None for username in mentions
        ):
            raise ValueError("mentions must contain usernames without the @ prefix")
        if len(set(mentions)) != len(mentions):
            raise ValueError("mentions must not contain duplicates")
        if priority is not None and not isinstance(priority, MessagePriority):
            raise TypeError("priority must be a MessagePriority or None")
        self.bot = bot
        self.mentions = tuple(mentions)
        self.priority = priority

    async def send(self, rule: PushRule, event: PushEvent, text: str) -> None:
        await self.deliver(rule, event, text)

    async def deliver(self, rule: PushRule, event: PushEvent, text: str) -> Message:
        """Deliver one event and return its confirmed post for operational tooling."""

        if not isinstance(rule, PushRule):
            raise TypeError("rule must be a PushRule")
        if not isinstance(text, str) or not text:
            raise ValueError("text must be a nonempty string")
        destination = rule.destination_id
        mention_prefix = ""
        if rule.destination_kind == "direct":
            destination = (await self.bot.create_direct_channel(destination)).id
        elif self.mentions:
            mention_prefix = " ".join(f"@{username}" for username in self.mentions) + "\n"
        return await self.bot.send_message(
            destination,
            f"{mention_prefix}[{event}] {text}",
            priority=self.priority,
            silent=False,
        )


__all__ = ["MattermostPushDispatcher"]
