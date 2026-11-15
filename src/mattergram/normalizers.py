"""Normalize a posted envelope synchronously, without hidden HTTP enrichment."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from mattergram.exceptions import ProtocolError
from mattergram.types import Channel, Message

if TYPE_CHECKING:
    from mattergram.bot import Bot


def normalize_posted(update: Mapping[str, Any], *, bot: Bot, self_user_id: str) -> Message | None:
    """Ignore non-posted/self events; reject malformed posted data instead of inventing IDs."""
    if not isinstance(self_user_id, str) or not self_user_id:
        raise ValueError("self_user_id must be a nonempty real user ID")
    if not isinstance(update, Mapping):
        raise ProtocolError("WebSocket event must be an object")
    event = update.get("event")
    if not isinstance(event, str) or not event:
        raise ProtocolError("WebSocket event name is missing or invalid")
    if event != "posted":
        return None
    data = update.get("data")
    if not isinstance(data, Mapping) or not isinstance(data.get("post"), str):
        raise ProtocolError("posted data.post must contain a JSON string")
    try:
        post = json.loads(data["post"])
    except (ValueError, TypeError):
        raise ProtocolError("posted data.post contains invalid JSON") from None
    if not isinstance(post, dict):
        raise ProtocolError("posted post must decode to an object")
    message = Message.from_wire(post, bot=bot)
    if message.user_id == self_user_id:
        return None
    channel = {"id": message.channel_id}
    if "channel_type" in data:
        channel["type"] = data["channel_type"]
    if "team_id" in data:
        channel["team_id"] = data["team_id"]
    message._channel = Channel.from_wire(channel)
    message._event_raw = deepcopy(dict(update))
    return message
