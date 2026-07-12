"""stage and publish (AI-361): the assembled story onto disk, then into R2.

Two moves sit between assembly and a child hearing the story:

**stage** copies an assembled story — story.json, audio, and images together —
into a local review folder (``staging/{story-id}/``) the operator can open. It
touches nothing but the local disk; the S3 bucket is never reachable from here.

**publish** is the only writer to ``published/`` (docs/architecture.md "Privacy
Architecture": only the publish step writes there). It uploads the staged story
under ``published/stories/{story-id}/`` with the content-hashed immutable names
assembly minted, uploads the language's spoken prompts under
``published/prompts/{lang}/``, and rewrites ``published/{lang}/manifest.json`` —
the one volatile file (short TTL). R2 is S3-compatible, reached with boto3.

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

# Utterance file-name stems (narrate.py: shelf_greeting/story_start/end_prompt)
# → the manifest's prompt keys the player reads (the dev fixture's
# greeting/story_start/end). The player plays a published manifest unchanged.
MANIFEST_PROMPT_KEYS = {
    "shelf_greeting": "greeting",
    "story_start": "story_start",
    "end_prompt": "end",
}

# Missing-object error codes across S3 dialects (head returns bare "404").
_MISSING_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


class PublishResult(BaseModel):
    """What one publish did: which object keys it wrote, which it left untouched,
    and the story ids the manifest now lists — the evidence idempotency needs."""

    story_id: str
    uploaded: list[str]
    skipped: list[str]
    manifest_story_ids: list[str]


def _story_json_bytes(story: Story) -> bytes:
    return story.model_dump_json(indent=2).encode("utf-8")


def stage_story(assembled: AssembledStory, settings: Settings) -> Path:
    """Copy an assembled story into staging/{story-id}/ for operator review.

    Writes story.json beside every hashed audio and image asset — text, audio,
    and images together in one folder the operator can open. Local disk only.
    """
    story_dir = settings.staging_dir / assembled.story.id
    story_dir.mkdir(parents=True, exist_ok=True)
    (story_dir / STORY_FILE).write_bytes(_story_json_bytes(assembled.story))
    for name, source in assembled.assets.items():
        (story_dir / name).write_bytes(source.read_bytes())
    return story_dir


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


def _upload_if_new(client: S3Client, bucket: str, key: str, body: bytes, content_type: str) -> bool:
    """PUT the object unless the bucket already holds these exact bytes.

    The immutable content-hashed names make this the whole idempotency story:
    identical content → identical key and identical MD5 → a pure skip.
    """
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if not _missing(error):
            raise
    else:
        if head["ETag"].strip('"') == hashlib.md5(body, usedforsecurity=False).hexdigest():
            return False
    client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)
    return True


def _load_manifest(client: S3Client, bucket: str, language: str) -> dict[str, Any]:
    key = f"{PUBLISHED_PREFIX}/{language}/manifest.json"
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if _missing(error):
            return {"language": language, "prompts": {}, "stories": []}
        raise
    loaded: dict[str, Any] = json.loads(obj["Body"].read())
    return loaded


def _upsert_story(manifest: dict[str, Any], story: Story, public_base: str) -> None:
    """Add or replace this story's manifest entry, keyed by id — a second story
    appends, a re-publish of the same id replaces, neither clobbers the rest."""
    entry = {
        "id": story.id,
        "title": story.title,
        "wash": THEME_WASH[story.theme],
        "story": f"{public_base}/stories/{story.id}/{STORY_FILE}",
    }
    stories: list[dict[str, Any]] = manifest.setdefault("stories", [])
    for index, existing in enumerate(stories):
        if existing.get("id") == story.id:
            stories[index] = entry
            return
    stories.append(entry)


def publish_story(
    story_id: str,
    settings: Settings,
    *,
    client: S3Client | None = None,
) -> PublishResult:
    """Publish a staged story to R2 and update its language manifest.

    Reads staging/{story-id}/ (never regenerates), uploads story.json, every
    hashed asset, and the language's prompts, then rewrites the manifest last —
    after all the content it points at is already in place. Every upload is a
    skip-if-unchanged, so a repeat publish is a no-op.
    """
    client = client or _build_client(settings)
    bucket = settings.r2_bucket
    public_base = settings.r2_public_base.rstrip("/")

    story_dir = settings.staging_dir / story_id
    story_bytes = (story_dir / STORY_FILE).read_bytes()
    story = Story.model_validate_json(story_bytes)
    language = story.language

    uploaded: list[str] = []
    skipped: list[str] = []

    def send(key: str, body: bytes, content_type: str) -> None:
        target = uploaded if _upload_if_new(client, bucket, key, body, content_type) else skipped
        target.append(key)

    for asset in sorted(story_dir.iterdir()):
        if asset.name == STORY_FILE:
            continue
        send(
            f"{PUBLISHED_PREFIX}/stories/{story_id}/{asset.name}",
            asset.read_bytes(),
            _content_type(asset.name),
        )
    send(f"{PUBLISHED_PREFIX}/stories/{story_id}/{STORY_FILE}", story_bytes, "application/json")

    prompt_dir = settings.staging_dir / "prompts" / language
    prompt_urls: dict[str, str] = {}
    if prompt_dir.is_dir():
        for prompt in sorted(prompt_dir.iterdir()):
            send(
                f"{PUBLISHED_PREFIX}/prompts/{language}/{prompt.name}",
                prompt.read_bytes(),
                _content_type(prompt.name),
            )
            manifest_key = MANIFEST_PROMPT_KEYS.get(prompt.name.split(".")[0])
            if manifest_key is not None:
                prompt_urls[manifest_key] = f"{public_base}/prompts/{language}/{prompt.name}"

    manifest = _load_manifest(client, bucket, language)
    if prompt_urls:
        manifest["prompts"] = {**manifest.get("prompts", {}), **prompt_urls}
    _upsert_story(manifest, story, public_base)
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode()
    send(f"{PUBLISHED_PREFIX}/{language}/manifest.json", manifest_bytes, "application/json")

    return PublishResult(
        story_id=story_id,
        uploaded=uploaded,
        skipped=skipped,
        manifest_story_ids=[entry["id"] for entry in manifest["stories"]],
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
            loaded = _load_manifest(client, bucket, candidate)
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
