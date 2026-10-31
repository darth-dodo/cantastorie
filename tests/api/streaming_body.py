"""A boto3 body that refuses to be read whole, for proving a route streams."""

from __future__ import annotations

import io
from typing import Any

from botocore.response import StreamingBody


class ChunkOnlyBody(StreamingBody):
    """A real ``StreamingBody`` whose unbounded ``read()`` raises.

    ``iter_chunks`` reads in bounded amounts, so a streaming route works and a
    route that buffers the whole object with ``.read()`` fails loudly.
    """

    def __init__(self, data: bytes) -> None:
        super().__init__(io.BytesIO(data), len(data))
        self.was_closed = False

    def read(self, amt: int | None = None) -> bytes:
        if amt is None:
            raise AssertionError("whole-body read(): the route must stream in chunks")
        return super().read(amt)

    def close(self) -> None:
        self.was_closed = True
        super().close()


def chunk_only_object(data: bytes) -> dict[str, Any]:
    """A get_object response shaped like boto3's, with a ChunkOnlyBody."""
    return {"Body": ChunkOnlyBody(data), "ContentLength": len(data)}
