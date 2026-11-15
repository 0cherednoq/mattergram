"""Explicit application-plugin composition for Mattergram bots."""

from .api import MattergramPlugin, PluginManifest
from .builder import HealthCheck, PluginBuilder
from .context import PluginContext, ProviderKey
from .exceptions import (
    MissingPluginCapabilityError,
    PluginError,
    PluginLifecycleError,
    PluginValidationError,
)
from .manager import PluginManager

__all__ = [
    "HealthCheck",
    "MattergramPlugin",
    "MissingPluginCapabilityError",
    "PluginBuilder",
    "PluginContext",
    "PluginError",
    "PluginLifecycleError",
    "PluginManager",
    "PluginManifest",
    "PluginValidationError",
    "ProviderKey",
]
