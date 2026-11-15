"""Immutable registration intent, separate from runtime command routing."""

from __future__ import annotations

from dataclasses import dataclass


def command_trigger(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value.encode("utf-8")) <= 128
        or value.startswith("/")
        or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValueError(
            "Command trigger must be 1-128 UTF-8 bytes without leading slash, whitespace or controls"
        )
    return value


def command_text(value: str, name: str, maximum: int | None = None) -> str:
    if not isinstance(value, str) or (maximum is not None and len(value.encode("utf-8")) > maximum):
        raise ValueError(f"Invalid command {name}")
    return value


@dataclass(frozen=True)
class CommandRegistration:
    """SDK metadata; triggers=None means inference from one plain Command filter."""

    description: str = ""
    hint: str = ""
    triggers: tuple[str, ...] | None = None
    provision: bool = True

    def __post_init__(self) -> None:
        command_text(self.description, "description", 128)
        command_text(self.hint, "hint")
        if type(self.provision) is not bool:
            raise TypeError("provision must be a boolean")
        if self.triggers is not None:
            if not isinstance(self.triggers, tuple) or not self.triggers:
                raise ValueError("Explicit triggers must be a nonempty tuple")
            for trigger in self.triggers:
                command_trigger(trigger)
            if len(self.triggers) != len(set(self.triggers)):
                raise ValueError("Explicit triggers must be unique")
        if not self.provision and (self.description or self.hint or self.triggers is not None):
            raise ValueError("provision=False cannot also declare registration metadata")
