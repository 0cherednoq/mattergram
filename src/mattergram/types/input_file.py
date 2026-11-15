"""Reusable streaming upload sources adapted from aiogram's InputFile algorithm."""

from __future__ import annotations

import asyncio
import io
import mimetypes
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from pathlib import Path

DEFAULT_CHUNK_SIZE = 64 * 1024
MAX_CHUNK_SIZE = 1024 * 1024


def _filename(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 255
        or any(character in "/\\" or ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("filename must be a 1-255 byte basename without control characters")
    return value


class InputFile(ABC):
    """One reusable upload source; each read owns and closes its own local handle."""

    def __init__(
        self,
        filename: str,
        *,
        content_type: str | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> None:
        if (
            isinstance(chunk_size, bool)
            or not isinstance(chunk_size, int)
            or not 1 <= chunk_size <= MAX_CHUNK_SIZE
        ):
            raise ValueError("chunk_size must be an integer between 1 and 1048576 bytes")
        if content_type is not None and (
            not isinstance(content_type, str)
            or "/" not in content_type
            or any(ord(character) < 33 or ord(character) == 127 for character in content_type)
        ):
            raise ValueError("content_type must be a nonempty MIME type without whitespace")
        self.filename = _filename(filename)
        self.content_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self.chunk_size = chunk_size

    @abstractmethod
    async def read(self) -> AsyncGenerator[bytes, None]:
        """Yield a fresh byte stream for one upload attempt."""
        yield b""


class BufferedInputFile(InputFile):
    def __init__(
        self,
        data: bytes,
        filename: str,
        *,
        content_type: str | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> None:
        if not isinstance(data, bytes):
            raise TypeError("BufferedInputFile data must be immutable bytes")
        super().__init__(filename, content_type=content_type, chunk_size=chunk_size)
        self.data = data

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        filename: str | None = None,
        *,
        content_type: str | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> BufferedInputFile:
        source = Path(path)
        return cls(
            source.read_bytes(),
            filename or source.name,
            content_type=content_type,
            chunk_size=chunk_size,
        )

    async def read(self) -> AsyncGenerator[bytes, None]:
        buffer = io.BytesIO(self.data)
        while chunk := buffer.read(self.chunk_size):
            yield chunk


class FSInputFile(InputFile):
    def __init__(
        self,
        path: str | Path,
        filename: str | None = None,
        *,
        content_type: str | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> None:
        self.path = Path(path)
        super().__init__(
            filename or self.path.name,
            content_type=content_type,
            chunk_size=chunk_size,
        )

    async def read(self) -> AsyncGenerator[bytes, None]:
        stream = await asyncio.to_thread(self.path.open, "rb")
        try:
            while chunk := await asyncio.to_thread(stream.read, self.chunk_size):
                yield chunk
        finally:
            stream.close()
