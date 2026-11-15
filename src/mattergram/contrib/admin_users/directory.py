"""Uncached Mattermost team directory adapter."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from mattergram.bot import Bot

from .models import TeamUser


class TeamUserDirectory(Protocol):
    async def list_users(self) -> Sequence[TeamUser]: ...


class MattermostTeamDirectory:
    """Read active team members with the ordinary runtime bot token."""

    def __init__(self, bot: Bot, *, team_id: str) -> None:
        if not team_id:
            raise ValueError("Mattermost team ID must be nonempty")
        self.bot = bot
        self.team_id = team_id

    async def list_users(self) -> tuple[TeamUser, ...]:
        page = 0
        per_page = 100
        result: list[TeamUser] = []
        seen: set[str] = set()
        while True:
            users = await self.bot.get_users_in_team(
                self.team_id,
                page=page,
                per_page=per_page,
                active=True,
            )
            for user in users:
                if user.id in seen:
                    raise RuntimeError("Mattermost team pagination returned a duplicate user")
                seen.add(user.id)
                if not user.username:
                    raise RuntimeError("Mattermost team user has no username")
                display_name = " ".join(
                    part.strip() for part in (user.first_name, user.last_name) if part and part.strip()
                )
                result.append(
                    TeamUser(
                        id=user.id,
                        username=user.username,
                        display_name=display_name or (user.nickname or ""),
                        is_bot=user.is_bot,
                    )
                )
            if len(users) < per_page:
                return tuple(result)
            page += 1


__all__ = ["MattermostTeamDirectory", "TeamUserDirectory"]
