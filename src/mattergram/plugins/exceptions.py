"""Errors raised while composing and running Mattergram application plugins."""

from __future__ import annotations


class PluginError(RuntimeError):
    """Base error for the application-plugin layer."""


class PluginValidationError(PluginError, ValueError):
    """The declared plugin graph or its contributions are invalid."""


class MissingPluginCapabilityError(PluginValidationError):
    """A plugin requires a Mattermost capability not declared by the application."""


class PluginLifecycleError(PluginError):
    """One or more plugin shutdown operations failed."""


__all__ = [
    "MissingPluginCapabilityError",
    "PluginError",
    "PluginLifecycleError",
    "PluginValidationError",
]
