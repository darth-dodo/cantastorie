"""Health endpoints for Render and the post-deploy smoke check.

/health is liveness: it never calls out, so a slow dependency can't fail it.
/health/version names the deployed commit, for the factory dashboard's deploy lag.
/health/ready is readiness (M1, GitHub #183): it proves R2 answers for both
buckets, and Render's health check gates on it. Its body is built only from
fixed check names and the values ok / failed / skipped, so no credential,
bucket name, endpoint or error text can reach it. Failures are logged with the
exception class only.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Annotated

import boto3
from botocore.config import Config
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from src.config import Settings, get_settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

logger = logging.getLogger(__name__)

router = APIRouter()

# Render polls the health check often; one round of R2 calls per window per
# instance keeps that cheap and stops one slow R2 answer stalling every poll.
CACHE_S = 10.0
# Hard bound on one check, on top of boto3's own timeouts: a hung R2 call is
# reported failed instead of holding the response.
CHECK_TIMEOUT_S = 3.0
_R2_CONFIG = Config(connect_timeout=2, read_timeout=2, retries={"max_attempts": 1})

# Its own small pool, not the event loop's default executor: a hung R2 call
# then holds only one of these threads, and nothing waits for it on shutdown.
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="readiness")

_cache: dict[int, tuple[float, int, dict[str, object]]] = {}


def clear_cache() -> None:
    _cache.clear()


def r2_client(settings: Settings) -> S3Client:
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url or None,
        aws_access_key_id=settings.r2_access_key_id.get_secret_value() or None,
        aws_secret_access_key=settings.r2_secret_access_key.get_secret_value() or None,
        region_name="auto",
        config=_R2_CONFIG,
    )


async def _bucket_check(client: S3Client, bucket: str, name: str) -> str:
    try:
        loop = asyncio.get_running_loop()
        call = loop.run_in_executor(_EXECUTOR, lambda: client.head_bucket(Bucket=bucket))
        await asyncio.wait_for(call, CHECK_TIMEOUT_S)
    except Exception as error:  # any failure means not ready; the detail stays in the log
        logger.warning("readiness check %s failed: %s", name, type(error).__name__)
        return "failed"
    return "ok"


async def _readiness(settings: Settings) -> tuple[int, dict[str, object]]:
    checks: dict[str, str] = {"settings": "ok"}
    if not settings.r2_endpoint_url:
        checks["r2_published"] = "skipped"
        checks["r2_pending"] = "skipped"
    else:
        client = r2_client(settings)
        published, pending = await asyncio.gather(
            _bucket_check(client, settings.r2_bucket, "r2_published"),
            _bucket_check(client, settings.pending_bucket, "r2_pending"),
        )
        checks["r2_published"] = published
        checks["r2_pending"] = pending
    ready = all(value != "failed" for value in checks.values())
    return (200 if ready else 503), {"status": "ready" if ready else "not_ready", "checks": checks}


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/version")
async def health_version() -> dict[str, str]:
    # The deployed commit, so the factory dashboard can show deploy lag. Render sets it.
    return {"commit": os.environ.get("RENDER_GIT_COMMIT", "")}


@router.get("/health/ready")
async def health_ready(settings: Annotated[Settings, Depends(get_settings)]) -> JSONResponse:
    now = time.monotonic()
    cached = _cache.get(id(settings))
    if cached is not None and now - cached[0] < CACHE_S:
        return JSONResponse(cached[2], status_code=cached[1])
    status_code, body = await _readiness(settings)
    _cache.clear()  # one live Settings in production; never let old instances pile up
    _cache[id(settings)] = (now, status_code, body)
    return JSONResponse(body, status_code=status_code)
