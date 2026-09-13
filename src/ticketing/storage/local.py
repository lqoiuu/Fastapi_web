import asyncio
import re
from collections.abc import AsyncIterable, AsyncIterator
from pathlib import Path
from typing import BinaryIO, Protocol

_OBJECT_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$")


class InvalidObjectKeyError(ValueError):
    pass


class AttachmentStorage(Protocol):
    async def write(self, object_key: str, chunks: AsyncIterable[bytes]) -> int: ...

    async def exists(self, object_key: str) -> bool: ...

    def read(self, object_key: str) -> AsyncIterator[bytes]: ...

    async def delete(self, object_key: str) -> None: ...


class LocalAttachmentStorage:
    """Store attachment bytes under a confined root without overwriting existing objects."""

    def __init__(self, root: Path, *, read_chunk_size: int = 64 * 1024) -> None:
        self._root = root.resolve()
        self._read_chunk_size = read_chunk_size

    async def write(self, object_key: str, chunks: AsyncIterable[bytes]) -> int:
        target = self._resolve(object_key)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        output: BinaryIO | None = None
        created = False
        size = 0
        try:
            output = await asyncio.to_thread(target.open, "xb")
            created = True
            async for chunk in chunks:
                size += await asyncio.to_thread(output.write, chunk)
            await asyncio.to_thread(output.flush)
            await asyncio.to_thread(output.close)
            output = None
            return size
        except BaseException:
            if output is not None:
                await asyncio.to_thread(output.close)
            if created:
                await asyncio.to_thread(target.unlink, missing_ok=True)
            raise

    async def exists(self, object_key: str) -> bool:
        return await asyncio.to_thread(self._resolve(object_key).is_file)

    async def read(self, object_key: str) -> AsyncIterator[bytes]:
        source = self._resolve(object_key)
        stream = await asyncio.to_thread(source.open, "rb")
        try:
            while chunk := await asyncio.to_thread(stream.read, self._read_chunk_size):
                yield chunk
        finally:
            await asyncio.to_thread(stream.close)

    async def delete(self, object_key: str) -> None:
        await asyncio.to_thread(self._resolve(object_key).unlink, missing_ok=True)

    def _resolve(self, object_key: str) -> Path:
        if not _OBJECT_KEY_PATTERN.fullmatch(object_key):
            raise InvalidObjectKeyError("Object key contains unsafe path components")
        target = (self._root / Path(*object_key.split("/"))).resolve()
        if not target.is_relative_to(self._root):
            raise InvalidObjectKeyError("Object key escapes the storage root")
        return target
