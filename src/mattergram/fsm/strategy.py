from enum import Enum, auto


class FSMStrategy(Enum):
    """
    FSM strategy for storage key generation.
    """

    USER_IN_CHANNEL = auto()
    """State will be stored for each user in channel."""
    CHANNEL = auto()
    """State will be stored for each channel globally without separating by users."""
    GLOBAL_USER = auto()
    """State will be stored globally for each user globally."""
    USER_IN_THREAD = auto()
    """State will be stored for each user in channel and thread."""
    CHANNEL_THREAD = auto()
    """State will be stored for each channel and thread, but not separated by users."""


def apply_strategy(
    strategy: FSMStrategy,
    channel_id: str,
    user_id: str,
    thread_id: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    if strategy == FSMStrategy.CHANNEL:
        return channel_id, None, None
    if strategy == FSMStrategy.GLOBAL_USER:
        return None, user_id, None
    if strategy == FSMStrategy.USER_IN_THREAD:
        return channel_id, user_id, thread_id
    if strategy == FSMStrategy.CHANNEL_THREAD:
        return channel_id, None, thread_id

    return channel_id, user_id, None
