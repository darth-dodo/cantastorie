"""stage and publish (AI-361): the assembled story into R2, then into published/.

Two moves sit between assembly and a child hearing the story:

**stage** uploads an assembled story — story.json, audio, and images together —
into the pending bucket under ``pending/staged/{story-id}/`` for operator review
from anywhere. It touches only R2; local disk is not needed.

**publish** is the only writer to ``published/`` (docs/architecture.md "Privacy
Architecture": only the publish step writes there). It reads the staged story
from the pending bucket, uploads it under ``published/stories/{story-id}/`` with
the content-hashed immutable names assembly minted, uploads the language's
spoken prompts under ``published/prompts/{lang}/``, and rewrites
``published/{lang}/manifest.json`` — the one volatile file (short TTL). R2 is
S3-compatible, reached with boto3.

Publish is idempotent by construction. Asset names embed a content hash, so an
unchanged asset keeps its key; every upload is a HEAD-then-PUT that skips when
the object already carries the same bytes (S3 ETag == body MD5). Re-publishing
an unchanged story therefore uploads nothing at all, manifest included.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import boto3
from botocore.exceptions import ClientError
from pydantic import BaseModel

from src.pipeline.models import Story, Theme

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
    from mypy_boto3_s3.type_defs import ObjectIdentifierTypeDef

    from src.config import Settings
    from src.pipeline.steps.assemble import AssembledStory

PUBLISHED_PREFIX = "published"
STAGED_PREFIX = "pending/staged"
STORY_FILE = "story.json"

CONTENT_TYPES = {".mp3": "audio/mpeg", ".webp": "image/webp", ".json": "application/json"}

THEME_WASH: dict[Theme, str] = {
    "animals_helping_each_other": "wash-bosco",
    "tiny_garden_adventure": "wash-bosco",
    "the_sleepy_sea": "wash-barchetta",
    "rain_and_puddles": "wash-barchetta",
    "bakery_morning": "wash-panetteria",
    "grandparent_visit": "wash-bosco",
    "the_lost_mitten": "wash-guanto",
    "gentle_forest_friends": "wash-bosco",
    "the_moon_says_goodnight": "wash-guanto",
    "picnic_surprise": "wash-panetteria",
    "the_little_boat": "wash-barchetta",
    "first_snow": "wash-guanto",
}

MANIFEST_PROMPT_KEYS = {
    "shelf_greeting": "greeting",
    "story_start": "story_start",
    "end_prompt": "end",
}

_MISSING_CODES = frozenset({"404", "NoSuchKey", "NotFound"})
_MANIFEST_WRITE_ATTEMPTS = 3


class PublishResult(BaseModel):
    story_id: str
    uploaded: list[str]
    skipped: list[str]
    manifest_story_ids: list[str]


def _story_json_bytes(story: Story) -> bytes:
    return story.model_dump_json(indent=2).encode("utf-8")


def _content_type(name: str) -> str:
    return CONTENT_TYPES.get(Path(name).suffix, "application/octet-stream")


def _build_client(settings: Settings) -> S3Client:
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url or None,
        aws_access_key_id=settings.r2_access_key_id.get_secret_value() or None,
        aws_secret_access_key=settings.r2_secret_access_key.get_secret_value() or None,
        region_name="auto",
    )


def _missing(error: ClientError) -> bool:
    return str(error.response.get("Error", {}).get("Code")) in _MISSING_CODES


def _precondition_failed(error: ClientError) -> bool:
    return str(error.response.get("Error", {}).get("Code")) == "PreconditionFailed"


def _upload_if_new(
    client: S3Client,
    bucket: str,
    key: str,
    body: bytes,
    content_type: str,
    cache_control: str | None = None,
) -> bool:
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if not _missing(error):
            raise
    else:
        if head["ETag"].strip('"') == hashlib.md5(body, usedforsecurity=False).hexdigest():
            return False
    if cache_control is None:
        client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)
    else:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            CacheControl=cache_control,
        )
    return True


def _load_manifest(
    client: S3Client, bucket: str, language: str
) -> tuple[dict[str, Any], str | None]:
    key = f"{PUBLISHED_PREFIX}/{language}/manifest.json"
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if _missing(error):
            return {"language": language, "prompts": {}, "stories": []}, None
        raise
    loaded: dict[str, Any] = json.loads(obj["Body"].read())
    return loaded, obj["ETag"]


def _upsert_story(manifest: dict[str, Any], story: Story, public_base: str) -> None:
    entry = {
        "id": story.id,
        "title": story.title,
        "wash": THEME_WASH[story.theme],
        "story": f"{public_base}/stories/{story.id}/{STORY_FILE}",
        "cover": f"{public_base}/stories/{story.id}/{story.pages[0].image}",
    }
    stories: list[dict[str, Any]] = manifest.setdefault("stories", [])
    for index, existing in enumerate(stories):
        if existing.get("id") == story.id:
            stories[index] = entry
            return
    stories.append(entry)


def _publish_manifest(
    client: S3Client,
    bucket: str,
    language: str,
    story: Story,
    public_base: str,
    prompt_urls: dict[str, str],
) -> tuple[list[str], list[str], list[str]]:
    manifest_key = f"{PUBLISHED_PREFIX}/{language}/manifest.json"
    for attempt in range(_MANIFEST_WRITE_ATTEMPTS):
        manifest, etag = _load_manifest(client, bucket, language)
        if prompt_urls:
            manifest["prompts"] = {**manifest.get("prompts", {}), **prompt_urls}
        _upsert_story(manifest, story, public_base)
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode()
        story_ids = [entry["id"] for entry in manifest["stories"]]
        if (
            etag is not None
            and etag.strip('"') == hashlib.md5(manifest_bytes, usedforsecurity=False).hexdigest()
        ):
            return [], [manifest_key], story_ids
        try:
            if etag is None:
                client.put_object(
                    Bucket=bucket,
                    Key=manifest_key,
                    Body=manifest_bytes,
                    ContentType="application/json",
                    CacheControl="public, max-age=60",
                )
            else:
                client.put_object(
                    Bucket=bucket,
                    Key=manifest_key,
                    Body=manifest_bytes,
                    ContentType="application/json",
                    CacheControl="public, max-age=60",
                    IfMatch=etag,
                )
        except ClientError as error:
            if _precondition_failed(error) and attempt < _MANIFEST_WRITE_ATTEMPTS - 1:
                continue
            raise
        return [manifest_key], [], story_ids
    raise RuntimeError("manifest publish retry loop exhausted")


def stage_story(
    assembled: AssembledStory,
    settings: Settings,
    *,
    client: S3Client | None = None,
) -> str:
    """Stage an assembled story to R2 under pending/staged/{story-id}/ for review.

    Writes story.json beside every hashed audio and image asset to the pending
    bucket so the workshop can review from anywhere. Returns the R2 key prefix.
    """
    client = client or _build_client(settings)
    bucket = settings.pending_bucket
    prefix = f"{STAGED_PREFIX}/{assembled.story.id}"
    delete_staged_story(assembled.story.id, settings, client=client)
    client.put_object(
        Bucket=bucket,
        Key=f"{prefix}/{STORY_FILE}",
        Body=_story_json_bytes(assembled.story),
        ContentType="application/json",
    )
    for name, source in assembled.assets.items():
        client.put_object(
            Bucket=bucket,
            Key=f"{prefix}/{name}",
            Body=source.read_bytes(),
            ContentType=_content_type(name),
        )
    return prefix


def publish_story(
    story_id: str,
    settings: Settings,
    *,
    client: S3Client | None = None,
) -> PublishResult:
    """Publish a staged story to R2 and update its language manifest.

    Reads pending/staged/{story-id}/ from the pending bucket (never
    regenerates), uploads story.json, every hashed asset, and the language's
    prompts, then rewrites the manifest last. Every upload is a
    skip-if-unchanged, so a repeat publish is a no-op.
    """
    client = client or _build_client(settings)
    bucket = settings.r2_bucket
    pending_bucket = settings.pending_bucket
    if not settings.r2_public_base:
        raise ValueError(
            "R2_PUBLIC_BASE must be set before publishing — manifest URLs would be relative"
        )
    public_base = settings.r2_public_base.rstrip("/")
    staged_prefix = f"{STAGED_PREFIX}/{story_id}"

    story_bytes = client.get_object(Bucket=pending_bucket, Key=f"{staged_prefix}/{STORY_FILE}")[
        "Body"
    ].read()
    story = Story.model_validate_json(story_bytes)
    if story.id != story_id:
        raise ValueError(f"Staged story id {story.id!r} does not match requested id {story_id!r}")
    language = story.language

    uploaded: list[str] = []
    skipped: list[str] = []

    def send(key: str, body: bytes, content_type: str) -> None:
        cache_control = (
            "public, max-age=60"
            if key.endswith("/manifest.json")
            else "public, max-age=31536000, immutable"
        )
        target = (
            uploaded
            if _upload_if_new(client, bucket, key, body, content_type, cache_control)
            else skipped
        )
        target.append(key)

    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=pending_bucket, Prefix=f"{staged_prefix}/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            name = key.removeprefix(f"{staged_prefix}/")
            if name == STORY_FILE:
                continue
            body = client.get_object(Bucket=pending_bucket, Key=key)["Body"].read()
            send(
                f"{PUBLISHED_PREFIX}/stories/{story_id}/{name}",
                body,
                _content_type(name),
            )
    send(f"{PUBLISHED_PREFIX}/stories/{story_id}/{STORY_FILE}", story_bytes, "application/json")

    prompt_prefix = f"{STAGED_PREFIX}/prompts/{language}"
    prompt_urls: dict[str, str] = {}
    for page in paginator.paginate(Bucket=pending_bucket, Prefix=f"{prompt_prefix}/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            name = key.removeprefix(f"{prompt_prefix}/")
            body = client.get_object(Bucket=pending_bucket, Key=key)["Body"].read()
            send(
                f"{PUBLISHED_PREFIX}/prompts/{language}/{name}",
                body,
                _content_type(name),
            )
            manifest_key = MANIFEST_PROMPT_KEYS.get(name.split(".")[0])
            if manifest_key is not None:
                prompt_urls[manifest_key] = f"{public_base}/prompts/{language}/{name}"

    manifest_uploaded, manifest_skipped, manifest_story_ids = _publish_manifest(
        client, bucket, language, story, public_base, prompt_urls
    )
    uploaded.extend(manifest_uploaded)
    skipped.extend(manifest_skipped)

    return PublishResult(
        story_id=story_id,
        uploaded=uploaded,
        skipped=skipped,
        manifest_story_ids=manifest_story_ids,
    )


def unpublish_story(
    story_id: str,
    settings: Settings,
    *,
    client: S3Client | None = None,
) -> None:
    client = client or _build_client(settings)
    bucket = settings.r2_bucket
    language: str | None = None
    manifest: dict[str, Any] | None = None
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=f"{PUBLISHED_PREFIX}/"
    ):
        for item in page.get("Contents", []):
            key = item["Key"]
            if not key.endswith("/manifest.json"):
                continue
            candidate = key.removeprefix(f"{PUBLISHED_PREFIX}/").removesuffix("/manifest.json")
            loaded, _ = _load_manifest(client, bucket, candidate)
            if any(entry.get("id") == story_id for entry in loaded.get("stories", [])):
                language = candidate
                manifest = loaded
                break
        if manifest is not None:
            break
    if language is not None and manifest is not None:
        manifest["stories"] = [
            entry for entry in manifest.get("stories", []) if entry.get("id") != story_id
        ]
        client.put_object(
            Bucket=bucket,
            Key=f"{PUBLISHED_PREFIX}/{language}/manifest.json",
            Body=json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode(),
            ContentType="application/json",
        )
    keys: list[ObjectIdentifierTypeDef] = []
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=f"{PUBLISHED_PREFIX}/stories/{story_id}/"
    ):
        keys.extend({"Key": item["Key"]} for item in page.get("Contents", []))
    for start in range(0, len(keys), 1000):
        client.delete_objects(Bucket=bucket, Delete={"Objects": keys[start : start + 1000]})


def delete_staged_story(
    story_id: str,
    settings: Settings,
    *,
    client: S3Client | None = None,
) -> None:
    """Remove a staged story from the pending bucket. Idempotent."""
    client = client or _build_client(settings)
    bucket = settings.pending_bucket
    prefix = f"{STAGED_PREFIX}/{story_id}"
    keys: list[ObjectIdentifierTypeDef] = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        keys.extend({"Key": item["Key"]} for item in page.get("Contents", []))
    for start in range(0, len(keys), 1000):
        client.delete_objects(Bucket=bucket, Delete={"Objects": keys[start : start + 1000]})
