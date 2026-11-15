"""Optional application lifecycle for plugin-backed composer keyboards."""

from .plugin import MENU_CAPABILITY, ComposerKeyboardPlugin

__all__ = ["ComposerKeyboardPlugin", "MENU_CAPABILITY"]
