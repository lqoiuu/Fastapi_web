import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from ticketing.storage.local import InvalidObjectKeyError, LocalAttachmentStorage


def test_local_storage_round_trip_and_confines_object_keys(tmp_path: Path) -> None:
    async def exercise() -> bytes:
        storage = LocalAttachmentStorage(tmp_path)

        async def chunks() -> AsyncIterator[bytes]:
            yield b"safe"
            yield b" content"

        size = await storage.write("tenant/ticket/random-key", chunks())
        assert size == 12
        assert await storage.exists("tenant/ticket/random-key")
        return b"".join([chunk async for chunk in storage.read("tenant/ticket/random-key")])

    assert asyncio.run(exercise()) == b"safe content"


def test_local_storage_rejects_path_traversal(tmp_path: Path) -> None:
    storage = LocalAttachmentStorage(tmp_path)

    with pytest.raises(InvalidObjectKeyError):
        asyncio.run(storage.exists("../outside"))


def test_local_storage_removes_partial_file_when_stream_fails(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        storage = LocalAttachmentStorage(tmp_path)

        async def failing_chunks() -> AsyncIterator[bytes]:
            yield b"partial"
            raise RuntimeError("stream interrupted")

        with pytest.raises(RuntimeError, match="stream interrupted"):
            await storage.write("tenant/ticket/random-key", failing_chunks())

    asyncio.run(exercise())
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


def test_local_storage_never_overwrites_an_existing_object(tmp_path: Path) -> None:
    async def exercise() -> None:
        storage = LocalAttachmentStorage(tmp_path)

        async def original() -> AsyncIterator[bytes]:
            yield b"original"

        async def replacement() -> AsyncIterator[bytes]:
            yield b"replacement"

        await storage.write("tenant/ticket/random-key", original())
        with pytest.raises(FileExistsError):
            await storage.write("tenant/ticket/random-key", replacement())
        content = b"".join([chunk async for chunk in storage.read("tenant/ticket/random-key")])
        assert content == b"original"

    asyncio.run(exercise())
