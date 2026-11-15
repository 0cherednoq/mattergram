"""E2 design contract, not installed stubs. Native client and routing slice are implemented."""

from collections.abc import AsyncGenerator, Mapping
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any

class User:
    id: str
    username: str | None
    raw: Mapping[str, Any]

class Channel:
    id: str
    team_id: str | None
    type: str | None
    raw: Mapping[str, Any]

class Message:
    post_id: str
    message_id: str
    channel_id: str
    root_id: str | None
    from_user: User
    chat: Channel
    text: str
    create_at: int
    date: datetime
    raw: Mapping[str, Any]
    event_raw: Mapping[str, Any] | None
    async def answer(self, text: str) -> Message: ...
    async def reply(self, text: str) -> Message: ...
    async def edit_text(self, text: str) -> Message: ...
    async def delete(self) -> None: ...

class InputFile:
    filename: str
    content_type: str
    chunk_size: int
    async def read(self) -> AsyncGenerator[bytes]: ...

class BufferedInputFile(InputFile):
    data: bytes
    def __init__(
        self,
        data: bytes,
        filename: str,
        *,
        content_type: str | None = None,
        chunk_size: int = 64 * 1024,
    ) -> None: ...
    @classmethod
    def from_file(
        cls,
        path: str | Path,
        filename: str | None = None,
        *,
        content_type: str | None = None,
        chunk_size: int = 64 * 1024,
    ) -> BufferedInputFile: ...

class FSInputFile(InputFile):
    path: Path
    def __init__(
        self,
        path: str | Path,
        filename: str | None = None,
        *,
        content_type: str | None = None,
        chunk_size: int = 64 * 1024,
    ) -> None: ...

class Bot:
    is_closed: bool
    user_id: str | None
    def __init__(self, *, token: str, base_url: str, request_timeout: float = 30.0) -> None: ...
    async def get_me(self) -> User: ...
    async def get_user(self, user_id: str) -> User: ...
    async def get_users_in_team(
        self,
        team_id: str,
        *,
        page: int = ...,
        per_page: int = ...,
        active: bool = ...,
    ) -> list[User]: ...
    async def get_channel(self, channel_id: str) -> Channel: ...
    async def create_direct_channel(self, user_id: str) -> Channel: ...
    async def send_message(self, chat_id: str, text: str, *, root_id: str | None = None) -> Message: ...
    async def edit_message_text(self, chat_id: str, message_id: str, text: str) -> Message: ...
    async def delete_message(self, chat_id: str, message_id: str) -> None: ...
    async def send_document(
        self,
        chat_id: str,
        document: InputFile,
        *,
        text: str | None = None,
        root_id: str | None = None,
    ) -> Message: ...
    async def close(self) -> None: ...
    async def __aenter__(self) -> Bot: ...
    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None: ...

class Router:
    name: str | None
    def __init__(self, *, name: str | None = None) -> None: ...
    def include_router(self, router: Router) -> Router: ...
    # message observer, F and handler types retain selected upstream semantics;
    # their precise typed interface is ported with the selected core in E1/E3.
    message: Any

class Dispatcher(Router):
    async def feed_event(self, bot: Bot, event: Message, **context: Any) -> Any: ...
    async def feed_update(self, bot: Bot, update: Mapping[str, Any], **context: Any) -> Any: ...
    async def start_websocket(self, bot: Bot) -> None: ...

class MattergramError(Exception): ...
class AuthenticationError(MattergramError): ...
class PermissionDeniedError(MattergramError): ...
class ProtocolError(MattergramError): ...
class TransportError(MattergramError): ...
class RequestOutcomeUnknown(TransportError): ...

# F is an instance of the ported magic-filter subclass (as_ included).
F: Any
