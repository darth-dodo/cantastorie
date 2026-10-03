"""Proxy published content from R2 so dev and prod read from the same bucket.

When the player's ASSET_BASE is "/published", it fetches manifests, story.json,
audio and images through this route instead of bucket-direct. The route is
unauthenticated, so it serves only the key shapes publish_story writes (H7):

    {lang}/manifest.json
    stories/{story-id}/{file}.{json|mp3|wav|webp}
    prompts/{lang}/{file}.{mp3|wav}

each optionally under families/{32-hex token}/ — a family's overlay lane, which
the public bucket already serves to anyone holding the token. Anything else,
including dot segments in any encoding, is a 404 before R2 is asked.
"""

import logging
import re
from collections.abc import Iterator
from typing import Annotated, Any

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from src.config import Settings, get_settings
from src.observability import family_hash
from src.pipeline.publish import CONTENT_TYPES, PUBLISHED_PREFIX, _build_client

logger = logging.getLogger(__name__)

router = APIRouter()

_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9_-]*"
_FILE = rf"{_SEGMENT}(?:\.[A-Za-z0-9_-]+)*"
_LANG = r"[a-z]{2}"
PUBLISHED_PATH = re.compile(
    rf"(?:families/[0-9a-f]{{32}}/)?"
    rf"(?:{_LANG}/manifest\.json"
    rf"|stories/{_SEGMENT}/{_FILE}\.(?:json|mp3|wav|webp)"
    rf"|prompts/{_LANG}/{_FILE}\.(?:mp3|wav))"
)
CHUNK_SIZE = 64 * 1024

_FAMILY_SEGMENT = re.compile(r"^families/([0-9a-f]{32})/")


def _log_fields(path: str, code: str) -> dict[str, str]:
    """The path with any family token segment stripped; the family as its hash."""
    fields = {"event": "published_proxy_error", "r2_code": code, "path": path}
    match = _FAMILY_SEGMENT.match(path)
    if match:
        fields["path"] = "families/<family>/" + path[match.end() :]
        fields["family"] = family_hash(match.group(1))
    return fields


def _stream(body: Any) -> Iterator[bytes]:
    try:
        yield from body.iter_chunks(CHUNK_SIZE)
    finally:
        body.close()


@router.get("/published/{path:path}")
def published_asset(
    path: str, settings: Annotated[Settings, Depends(get_settings)]
) -> StreamingResponse:
    if not settings.r2_bucket or not PUBLISHED_PATH.fullmatch(path):
        raise HTTPException(status_code=404)

    try:
        response = _build_client(settings).get_object(
            Bucket=settings.r2_bucket, Key=f"{PUBLISHED_PREFIX}/{path}"
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code not in {"NoSuchKey", "404"}:
            logger.warning("published_proxy_error", extra=_log_fields(path, code or "error"))
        raise HTTPException(status_code=404) from exc

    upstream = {
        "Cache-Control": response.get("CacheControl"),
        "ETag": response.get("ETag"),
        "Content-Length": response.get("ContentLength"),
    }
    headers = {name: str(value) for name, value in upstream.items() if value is not None}
    suffix = path[path.rfind(".") :]
    body = response["Body"]
    # The background close covers a client that disconnects before the first
    # chunk, when the generator never starts and its finally never runs.
    return StreamingResponse(
        _stream(body),
        media_type=CONTENT_TYPES[suffix],
        headers=headers,
        background=BackgroundTask(body.close),
    )
