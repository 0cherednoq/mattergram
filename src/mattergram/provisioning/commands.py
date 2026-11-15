"""Deterministic native command declarations collected from attached routers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from mattergram.dispatcher.event.slash import REGISTRATION_FLAG
from mattergram.filters.command import Command
from mattergram.types.command_metadata import CommandRegistration, command_text, command_trigger

if TYPE_CHECKING:
    from mattergram.dispatcher.router import Router


@dataclass(frozen=True)
class CommandSpec:
    trigger: str
    description: str = ""
    hint: str = ""
    display_name: str = ""
    method: Literal["POST", "GET"] = "POST"
    auto_complete: bool = True

    def __post_init__(self) -> None:
        command_trigger(self.trigger)
        command_text(self.description, "description", 128)
        command_text(self.hint, "hint")
        command_text(self.display_name, "display_name", 64)
        if self.method not in ("POST", "GET"):
            raise ValueError("Command method must be POST or GET")
        if type(self.auto_complete) is not bool:
            raise TypeError("auto_complete must be a boolean")

    def to_payload(self, *, team_id: str, url: str) -> dict[str, str | bool]:
        if not isinstance(team_id, str) or not team_id:
            raise ValueError("Resolve the team to its actual native ID before creating a command")
        if (
            not isinstance(url, str)
            or len(url.encode("utf-8")) > 1024
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url)
        ):
            raise ValueError("Invalid command callback URL")
        parts = urlsplit(url)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
        ):
            raise ValueError("Command callback URL must use HTTP(S) without credentials or fragment")
        try:
            parts.port
        except ValueError:
            raise ValueError("Invalid command callback URL port") from None
        return {
            "team_id": team_id,
            "trigger": self.trigger,
            "method": "P" if self.method == "POST" else "G",
            "url": url,
            "description": self.description,
            "display_name": self.display_name,
            "auto_complete": self.auto_complete,
            "auto_complete_desc": self.description,
            "auto_complete_hint": self.hint,
        }


@dataclass(frozen=True)
class CommandManifest:
    commands: tuple[CommandSpec, ...]
    skipped_handlers: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.commands, tuple) or not all(isinstance(c, CommandSpec) for c in self.commands):
            raise TypeError("Manifest commands must be a tuple of CommandSpec")
        if len({c.trigger for c in self.commands}) != len(self.commands):
            raise ValueError("Manifest commands must have unique triggers")
        if type(self.skipped_handlers) is not int or self.skipped_handlers < 0:
            raise ValueError("Invalid skipped handler count")

    @property
    def fingerprint(self) -> str:
        # Application metadata only: no server IDs, tokens, callback URLs or callable repr.
        entries = [
            {
                "trigger": c.trigger,
                "description": c.description,
                "hint": c.hint,
                "display_name": c.display_name,
                "method": c.method,
                "auto_complete": c.auto_complete,
            }
            for c in sorted(self.commands, key=lambda item: item.trigger)
        ]
        return hashlib.sha256(json.dumps(entries, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def collect_commands(router: Router) -> CommandManifest:
    """Only attached native slash handlers; never inspect posted message commands."""
    from mattergram.dispatcher.router import Router

    if not isinstance(router, Router):
        raise TypeError("collect_commands requires a Router or Dispatcher")
    commands: dict[str, CommandSpec] = {}
    skipped = 0
    for node in router.chain_tail:
        observer = node.slash_command
        for handler in observer.handlers:
            metadata = handler.flags.get(REGISTRATION_FLAG, CommandRegistration())
            if not isinstance(metadata, CommandRegistration):
                raise ValueError("Invalid slash registration metadata")
            if not metadata.provision:
                skipped += 1
                continue
            if metadata.triggers is not None:
                triggers = metadata.triggers
            else:
                if any(parent.slash_command._handler.filters for parent in node.chain_head):
                    raise ValueError("Root-filtered slash handlers require explicit triggers")
                filters = handler.filters or []
                if len(filters) != 1 or type(filters[0].callback) is not Command:
                    raise ValueError("Slash handler requires one literal Command filter or explicit triggers")
                command = filters[0].callback
                if command.magic is not None or "/" not in command.prefix:
                    raise ValueError(
                        "Command filter cannot be exported automatically; declare explicit triggers"
                    )
                if not all(isinstance(value, str) for value in command.commands):
                    raise ValueError("Regex command filters require explicit triggers")
                triggers = tuple(value for value in command.commands if isinstance(value, str))
            for trigger in triggers:
                spec = CommandSpec(trigger=trigger, description=metadata.description, hint=metadata.hint)
                previous = commands.get(trigger)
                if previous is not None and previous != spec:
                    raise ValueError("Conflicting registration metadata for a command trigger")
                commands[trigger] = spec
    return CommandManifest(tuple(commands[key] for key in sorted(commands)), skipped)
