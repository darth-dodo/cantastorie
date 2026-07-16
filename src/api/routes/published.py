"""Proxy published content from R2 so dev and prod read from the same bucket.

In dev (no R2 public URL) and in prod (bucket-direct via the app), this route
serves everything under /published/ — manifests, story.json, audio, images.
The player's ASSET_BASE is set to "/published" so it fetches through here.
"""

from typing import Annotated, Any

import boto3
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from src.config import Settings, get_settings

router = APIRouter()

CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".webp": "image/webp",
    ".json": "application/json",
}


def _s3_client(settings: Settings) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.r2_secret_access_key.get_secret_value(),
        region_name="auto",
    )


@router.get("/published/{path:path}")
async def published_asset(
    path: str, settings: Annotated[Settings, Depends(get_settings)]
) -> Response:
    if not settings.r2_bucket:
        raise HTTPException(status_code=404, detail="R2 not configured")

    key = f"published/{path}"
    client: Any = _s3_client(settings)
    try:
        response = client.get_object(Bucket=settings.r2_bucket, Key=key)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    body = response["Body"].read()
    suffix = "." + path.rsplit(".", 1)[-1] if "." in path else ""
    media_type = CONTENT_TYPES.get(suffix, "application/octet-stream")
    return Response(content=body, media_type=media_type)
