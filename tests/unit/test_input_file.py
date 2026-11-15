from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from mattergram import Bot
from mattergram.types import BufferedInputFile, FSInputFile, Message


async def read_all(source: BufferedInputFile | FSInputFile) -> tuple[list[bytes], bytes]:
    chunks = [chunk async for chunk in source.read()]
    return chunks, b"".join(chunks)


class InputFileTests(unittest.IsolatedAsyncioTestCase):
    def test_e7_02_public_signatures_match_the_design_contract(self) -> None:
        expected = {
            Bot.get_user: ("self", "user_id"),
            Bot.get_users_in_team: ("self", "team_id", "page", "per_page", "active"),
            Bot.get_channel: ("self", "channel_id"),
            Bot.create_direct_channel: ("self", "user_id"),
            Bot.edit_message_text: ("self", "chat_id", "message_id", "text", "props"),
            Bot.delete_message: ("self", "chat_id", "message_id"),
            Bot.send_document: ("self", "chat_id", "document", "text", "root_id"),
            Message.edit_text: ("self", "text"),
            Message.delete: ("self",),
        }
        for method, parameters in expected.items():
            with self.subTest(method=method.__qualname__):
                self.assertEqual(tuple(inspect.signature(method).parameters), parameters)
        signature = inspect.signature(Bot.send_document)
        self.assertEqual(signature.parameters["text"].default, None)
        self.assertEqual(signature.parameters["root_id"].default, None)

    async def test_buffered_source_is_reusable_and_chunked(self) -> None:
        source = BufferedInputFile(b"abcdef", "report.txt", chunk_size=2)
        first, first_data = await read_all(source)
        second, second_data = await read_all(source)
        self.assertEqual(first, [b"ab", b"cd", b"ef"])
        self.assertEqual(second, first)
        self.assertEqual(first_data, second_data)
        self.assertEqual(source.content_type, "text/plain")

    async def test_filesystem_source_opens_and_closes_one_handle_per_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "payload.bin"
            path.write_bytes(b"012345")
            source = FSInputFile(path, chunk_size=4)
            first, data = await read_all(source)
            self.assertEqual(first, [b"0123", b"45"])
            self.assertEqual(data, b"012345")
            path.write_bytes(b"changed")
            _, changed = await read_all(source)
            self.assertEqual(changed, b"changed")
            path.rename(path.with_suffix(".moved"))

    def test_sources_reject_ambiguous_names_content_types_and_chunks(self) -> None:
        for name in ("", "../secret", "dir/file", "bad\nname", "x" * 256):
            with self.subTest(name=name), self.assertRaises(ValueError):
                BufferedInputFile(b"data", name)
        for chunk_size in (True, 0, 1024 * 1024 + 1):
            with self.subTest(chunk_size=chunk_size), self.assertRaises(ValueError):
                BufferedInputFile(b"data", "data.bin", chunk_size=chunk_size)
        with self.assertRaises(ValueError):
            BufferedInputFile(b"data", "data.bin", content_type="not a mime")
        with self.assertRaises(TypeError):
            BufferedInputFile(bytearray(b"mutable"), "data.bin")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
