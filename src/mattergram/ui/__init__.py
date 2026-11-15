"""Native Mattermost UI declarations and explicit capability profiles."""

from .adaptive import from_adaptive_cards
from .capabilities import Capability, UIProfile
from .composer import ComposerButton, ComposerKeyboard, ComposerKeyboardState
from .forms import Form, Option
from .input_base import UIConversionError
from .slack import from_slack_blocks

__all__ = [
    "Capability",
    "ComposerButton",
    "ComposerKeyboard",
    "ComposerKeyboardState",
    "Form",
    "Option",
    "UIConversionError",
    "UIProfile",
    "from_adaptive_cards",
    "from_slack_blocks",
]
