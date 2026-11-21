"""Stream an R2 object to the client instead of reading it whole.

boto3's ``StreamingBody`` is synchronous. Starlette runs a sync iterator in its
threadpool (``iterate_in_threadpool``), so iterating it never blocks the event
loop, and memory per request is one chunk rather than the whole object.
"""

from collections.abc import Iterator, Mapping
from typing import Any

from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

CHUNK_SIZE = 64 * 1024


def iter_body(body: Any) -> Iterator[bytes]:
    try:
        yield from body.iter_chunks(CHUNK_SIZE)
    finally:
        body.close()


def stream_object(
    response: Mapping[str, Any], *, media_type: str, headers: Mapping[str, str]
) -> StreamingResponse:
    """A StreamingResponse over a get_object ``response``'s body.

    Content-Length is passed through from R2 when it reports one.
    """
    out = dict(headers)
    length = response.get("ContentLength")
    if length is not None:
        out["Content-Length"] = str(length)
    body = response["Body"]
    # The background close covers a client that disconnects before the first
    # chunk, when the generator never starts and its finally never runs.
    return StreamingResponse(
        iter_body(body),
        media_type=media_type,
        headers=out,
        background=BackgroundTask(body.close),
    )
