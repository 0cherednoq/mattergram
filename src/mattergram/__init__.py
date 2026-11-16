"""Native Mattermost bot client. Routing and WebSocket runtime are being ported."""

from importlib.metadata import version

from .bot import Bot
from .dispatcher.dispatcher import Dispatcher
from .dispatcher.flags import flags
from .dispatcher.router import Router
from .native import NativeAPI
from .ui.composer import ComposerButton, ComposerKeyboard, ComposerKeyboardState
from .utils.magic_filter import MagicFilter

__version__ = version("mattergram")

F = MagicFilter()

__all__ = [
    "Bot",
    "ComposerButton",
    "ComposerKeyboard",
    "ComposerKeyboardState",
    "Dispatcher",
    "F",
    "NativeAPI",
    "Router",
    "__version__",
    "flags",
]
