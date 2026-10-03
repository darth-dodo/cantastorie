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
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_args

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from pydantic import BaseModel

from src.observability import family_hash
from src.pipeline.models import Language, Story, Theme

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from mypy_boto3_s3 import S3Client
    from mypy_boto3_s3.type_defs import ObjectIdentifierTypeDef

    from src.config import Settings
    from src.pipeline.steps.assemble import AssembledStory

logger = logging.getLogger(__name__)

PUBLISHED_PREFIX = "published"
FAMILIES_SEGMENT = "families"
PENDING_PREFIX = "pending"
STAGED_PREFIX = f"{PENDING_PREFIX}/staged"
STORY_FILE = "story.json"

# The tenancy boundary in R2-key form. A family_token becomes a path prefix, so
# it must be exactly the canonical 32-hex mint (secrets.token_hex(16)) — no
# casing variants, no path separators, no empty string. See src/workshop/scope.py
# and src/api/routes/parent.py (FAMILY_TOKEN_PATTERN).
_FAMILY_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


def _publish_root(family_token: str | None) -> str:
    """Resolve the publish root for a scope.

    ``None`` → the shared shelf root (``published``), byte-identical to today.
    A family token → its private overlay root (``published/families/{token}``).
    A non-canonical token raises rather than smuggling into a bucket key.
    """
    if family_token is None:
        return PUBLISHED_PREFIX
    if not _FAMILY_TOKEN_RE.fullmatch(family_token):
        raise ValueError(f"invalid family token for overlay publish: {family_token!r}")
    return f"{PUBLISHED_PREFIX}/{FAMILIES_SEGMENT}/{family_token}"


def _lane_fields(family_token: str | None) -> dict[str, str]:
    """Log fields naming a publish lane — the token itself only ever as its hash."""
    if family_token is None:
        return {"lane": "shared"}
    return {"lane": "family", "family": family_hash(family_token)}


CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".webp": "image/webp",
    ".json": "application/json",
}

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

# Utterance file-name stems (narrate.py: shelf_greeting/story_start/end_prompt/
# audio_retry/offline) → the manifest's prompt keys the player reads (the dev
# fixture's greeting/story_start/end/audio_retry/offline). The player plays a
# published manifest unchanged.
# NOTE: the "offline" key is published for a uniform utterance set, but the
# player deliberately does NOT read it from the manifest — the offline screen
# fires when the manifest itself couldn't load, so its prompt is fetched
# same-origin (see main.js), never via ASSET_BASE/R2. Do not "wire up" this
# key on the player: R2 is exactly what's unreachable when the screen shows.
MANIFEST_PROMPT_KEYS = {
    "shelf_greeting": "greeting",
    "story_start": "story_start",
    "end_prompt": "end",
    "audio_retry": "audio_retry",
    "offline": "offline",
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


# Parallel R2 reads (AI-465): a page that needs N manifests or run records
# pays about one round trip of wall time, not N. The client's pool must be at
# least this wide or the extra threads just queue for a connection.
READ_WORKERS = 16
CLIENT_CONFIG = Config(max_pool_connections=READ_WORKERS * 2)

# One pooled client per Settings instance. Production has exactly one Settings
# (get_settings is cached), so every request reuses warm R2 connections instead
# of paying a fresh TLS handshake. Keying on the instance (and holding it, so
# its id is never reused) keeps each test's Settings on its own client.
_CLIENTS: dict[int, tuple[Settings, S3Client]] = {}
_CLIENTS_LOCK = threading.Lock()


def _build_client(settings: Settings) -> S3Client:
    with _CLIENTS_LOCK:
        cached = _CLIENTS.get(id(settings))
        if cached is None or cached[0] is not settings:
            client = boto3.client(
                "s3",
                endpoint_url=settings.r2_endpoint_url or None,
                aws_access_key_id=settings.r2_access_key_id.get_secret_value() or None,
                aws_secret_access_key=settings.r2_secret_access_key.get_secret_value() or None,
                region_name="auto",
                config=CLIENT_CONFIG,
            )
            cached = (settings, client)
            _CLIENTS[id(settings)] = cached
        return cached[1]


def parallel_map[T, R](fn: Callable[[T], R], items: Iterable[T]) -> list[R]:
    """``[fn(x) for x in items]`` on up to READ_WORKERS threads, in order.

    boto3 clients are thread-safe, so independent R2 reads overlap.
    """
    work = list(items)
    if len(work) <= 1:
        return [fn(item) for item in work]
    with ThreadPoolExecutor(max_workers=min(READ_WORKERS, len(work))) as pool:
        return list(pool.map(fn, work))


def child_prefixes(client: S3Client, bucket: str, prefix: str) -> list[str]:
    """The immediate "subdirectory" names under ``prefix`` (which ends in "/").

    A delimiter listing returns one entry per child rather than every object
    below it — one call instead of paging through every audio file and image.
    """
    names: list[str] = []
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=prefix, Delimiter="/"
    ):
        for common in page.get("CommonPrefixes", []):
            names.append(common["Prefix"].removeprefix(prefix).rstrip("/"))
    return names


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


def _copy_if_new(
    client: S3Client,
    *,
    source_bucket: str,
    source_key: str,
    source_etag: str,
    dest_bucket: str,
    dest_key: str,
    content_type: str,
    cache_control: str,
) -> bool:
    """Copy pending → published server-side, skipping when the destination
    already holds identical bytes.

    Assets are content-hashed and staged with single-part PUTs, so their ETags
    are plain MD5s that compare directly against the source ETag from the object
    listing — no body is pulled through the process (the download+reupload this
    replaces made the approve request block on every asset). MetadataDirective
    REPLACE re-stamps content-type and the immutable Cache-Control, which the
    pending object does not carry.
    """
    if source_etag.strip('"') and _dest_matches(client, dest_bucket, dest_key, source_etag):
        return False
    client.copy_object(
        Bucket=dest_bucket,
        Key=dest_key,
        CopySource={"Bucket": source_bucket, "Key": source_key},
        ContentType=content_type,
        CacheControl=cache_control,
        MetadataDirective="REPLACE",
    )
    return True


def _dest_matches(client: S3Client, bucket: str, key: str, source_etag: str) -> bool:
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if not _missing(error):
            raise
        return False
    return head["ETag"].strip('"') == source_etag.strip('"')


def _load_manifest(
    client: S3Client, bucket: str, language: str, root: str = PUBLISHED_PREFIX
) -> tuple[dict[str, Any], str | None]:
    key = f"{root}/{language}/manifest.json"
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
        "cover": (
            f"{public_base}/stories/{story.id}/{story.cover}"
            if story.cover is not None
            else f"{public_base}/stories/{story.id}/{story.pages[0].image}"
        ),
    }
    stories: list[dict[str, Any]] = manifest.setdefault("stories", [])
    for index, existing in enumerate(stories):
        if existing.get("id") == story.id:
            stories[index] = entry
            return
    stories.append(entry)


def _write_manifest(
    client: S3Client,
    bucket: str,
    key: str,
    *,
    load: Callable[[], tuple[dict[str, Any], str | None]],
    mutate: Callable[[dict[str, Any]], None],
) -> tuple[dict[str, Any], bool]:
    """Load-mutate-write a manifest under optimistic concurrency.

    The one path every manifest write goes through — publish, unpublish, and
    the repair script alike. ``load`` is called fresh on every attempt (so a
    losing race always retries against the latest state, never a stale copy)
    and returns the current manifest plus its ETag (``None`` when the manifest
    doesn't exist yet). ``mutate`` edits that manifest in place.

    The write carries ``IfMatch`` on the ETag (omitted for a not-yet-existing
    manifest, an unconditional create) and ``Cache-Control: public,
    max-age=60`` — the manifest is "the one volatile file" (see module
    docstring), so every writer keeps its cache lifetime short, unpublish and
    repair included. A ``PreconditionFailed`` — another writer won the race —
    retries up to ``_MANIFEST_WRITE_ATTEMPTS`` times; any other error
    propagates.

    Returns the manifest as written and whether a PUT actually happened
    (``False`` when ``mutate`` produced bytes identical to what's already
    stored, so the write was skipped).
    """
    for attempt in range(_MANIFEST_WRITE_ATTEMPTS):
        manifest, etag = load()
        mutate(manifest)
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode()
        if (
            etag is not None
            and etag.strip('"') == hashlib.md5(manifest_bytes, usedforsecurity=False).hexdigest()
        ):
            return manifest, False
        try:
            if etag is None:
                client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=manifest_bytes,
                    ContentType="application/json",
                    CacheControl="public, max-age=60",
                )
            else:
                client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=manifest_bytes,
                    ContentType="application/json",
                    CacheControl="public, max-age=60",
                    IfMatch=etag,
                )
        except ClientError as error:
            if _precondition_failed(error) and attempt < _MANIFEST_WRITE_ATTEMPTS - 1:
                continue
            raise
        return manifest, True
    raise RuntimeError("manifest write retry loop exhausted")


def _publish_manifest(
    client: S3Client,
    bucket: str,
    language: str,
    story: Story,
    public_base: str,
    prompt_urls: dict[str, str],
    root: str = PUBLISHED_PREFIX,
) -> tuple[list[str], list[str], list[str]]:
    manifest_key = f"{root}/{language}/manifest.json"

    def mutate(manifest: dict[str, Any]) -> None:
        if prompt_urls:
            manifest["prompts"] = {**manifest.get("prompts", {}), **prompt_urls}
        _upsert_story(manifest, story, public_base)

    manifest, wrote = _write_manifest(
        client,
        bucket,
        manifest_key,
        load=lambda: _load_manifest(client, bucket, language, root),
        mutate=mutate,
    )
    listed_ids = [entry["id"] for entry in manifest["stories"]]
    if wrote:
        return [manifest_key], [], listed_ids
    return [], [manifest_key], listed_ids


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
    family_token: str | None = None,
) -> PublishResult:
    """Publish a staged story to R2 and update its language manifest.

    Reads pending/staged/{story-id}/ from the pending bucket (never
    regenerates), uploads story.json, every hashed asset, and the language's
    prompts, then rewrites the manifest last. Every upload is a
    skip-if-unchanged, so a repeat publish is a no-op.

    ``family_token`` selects the lane: ``None`` writes the shared shelf
    (operator, global), a canonical token writes that family's private overlay
    under ``published/families/{token}/…``. The two lanes never cross.
    """
    root = _publish_root(family_token)
    client = client or _build_client(settings)
    bucket = settings.r2_bucket
    pending_bucket = settings.pending_bucket
    if not settings.r2_public_base:
        raise ValueError(
            "R2_PUBLIC_BASE must be set before publishing — manifest URLs would be relative"
        )
    public_base = settings.r2_public_base.rstrip("/")
    if family_token is not None:
        public_base = f"{public_base}/{FAMILIES_SEGMENT}/{family_token}"
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

    def copy(source_key: str, source_etag: str, dest_key: str, content_type: str) -> None:
        # Assets and prompts are content-hashed, hence always immutable — never
        # the manifest, so the immutable Cache-Control is unconditional here.
        target = (
            uploaded
            if _copy_if_new(
                client,
                source_bucket=pending_bucket,
                source_key=source_key,
                source_etag=source_etag,
                dest_bucket=bucket,
                dest_key=dest_key,
                content_type=content_type,
                cache_control="public, max-age=31536000, immutable",
            )
            else skipped
        )
        target.append(dest_key)

    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=pending_bucket, Prefix=f"{staged_prefix}/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            name = key.removeprefix(f"{staged_prefix}/")
            if name == STORY_FILE:
                continue
            copy(key, obj.get("ETag", ""), f"{root}/stories/{story_id}/{name}", _content_type(name))
    send(f"{root}/stories/{story_id}/{STORY_FILE}", story_bytes, "application/json")

    prompt_prefix = f"{STAGED_PREFIX}/prompts/{language}"
    prompt_urls: dict[str, str] = {}
    for page in paginator.paginate(Bucket=pending_bucket, Prefix=f"{prompt_prefix}/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            name = key.removeprefix(f"{prompt_prefix}/")
            copy(key, obj.get("ETag", ""), f"{root}/prompts/{language}/{name}", _content_type(name))
            manifest_key = MANIFEST_PROMPT_KEYS.get(name.split(".")[0])
            if manifest_key is not None:
                prompt_urls[manifest_key] = f"{public_base}/prompts/{language}/{name}"

    manifest_uploaded, manifest_skipped, manifest_story_ids = _publish_manifest(
        client, bucket, language, story, public_base, prompt_urls, root
    )
    uploaded.extend(manifest_uploaded)
    skipped.extend(manifest_skipped)

    logger.info(
        "story_published",
        extra={
            "event": "story_published",
            **_lane_fields(family_token),
            "story_id": story_id,
            "language": language,
            "uploaded": len(uploaded),
            "skipped": len(skipped),
        },
    )
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
    family_token: str | None = None,
) -> None:
    """Remove a published story: its manifest entry and its asset directory.

    ``family_token`` scopes the search to a lane. ``None`` searches only the
    shared shelf (``published/{lang}/manifest.json`` + ``published/stories/…``);
    a canonical token searches only that family's overlay root. Scoping keeps a
    family confined to its own partition and lets the operator target a
    specific family's private story by passing its token.
    """
    root = _publish_root(family_token)
    client = client or _build_client(settings)
    bucket = settings.r2_bucket
    language: str | None = None
    manifest_prefixes = _manifest_prefixes_under(client, bucket, root)
    for candidate in manifest_prefixes:
        loaded, _ = _load_manifest(client, bucket, candidate, root)
        if any(entry.get("id") == story_id for entry in loaded.get("stories", [])):
            language = candidate
            break
    if language is not None:
        lang = language
        manifest_key = f"{root}/{lang}/manifest.json"

        def mutate(manifest: dict[str, Any]) -> None:
            manifest["stories"] = [
                entry for entry in manifest.get("stories", []) if entry.get("id") != story_id
            ]

        _write_manifest(
            client,
            bucket,
            manifest_key,
            load=lambda: _load_manifest(client, bucket, lang, root),
            mutate=mutate,
        )
    keys: list[ObjectIdentifierTypeDef] = []
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=f"{root}/stories/{story_id}/"
    ):
        keys.extend({"Key": item["Key"]} for item in page.get("Contents", []))
    for start in range(0, len(keys), 1000):
        client.delete_objects(Bucket=bucket, Delete={"Objects": keys[start : start + 1000]})
    logger.info(
        "story_unpublished",
        extra={
            "event": "story_unpublished",
            **_lane_fields(family_token),
            "story_id": story_id,
            "language": language,
            "deleted": len(keys),
        },
    )


def _manifest_prefixes_under(client: S3Client, bucket: str, root: str) -> list[str]:
    """Language prefixes owning a manifest.json directly under ``root``.

    Only the manifests one level below ``root`` — never a family overlay's when
    ``root`` is the shared shelf, and never the shared shelf's when ``root`` is a
    family overlay. Returns e.g. ``["it", "es"]``.
    """
    prefixes: list[str] = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=f"{root}/"):
        for item in page.get("Contents", []):
            key = item["Key"]
            if not key.endswith("/manifest.json"):
                continue
            candidate = key.removeprefix(f"{root}/").removesuffix("/manifest.json")
            if "/" in candidate:
                continue  # a deeper (family) manifest — not owned by this root
            prefixes.append(candidate)
    return prefixes


class AuditResult(BaseModel):
    violations: list[str]
    manifests_checked: int


def _check_manifest_url(sid: str, url: str, lane_asset_prefix: str) -> str | None:
    """Verify a manifest entry's URL stays inside its own lane's asset root.

    ``lane_asset_prefix`` is ``{root}/stories/`` for the lane owning this
    manifest. A URL pointing anywhere else under published/ (the shared shelf
    from a family manifest, another family, or a family asset from the shared
    manifest) is a cross-tenant leak.
    """
    if not url:
        return f"{sid}: manifest entry has an empty URL"
    if "pending/" in url:
        return f"{sid}: manifest URL points into pending/ — {url}"
    if f"/{PUBLISHED_PREFIX}/" not in url and not url.startswith(PUBLISHED_PREFIX):
        return f"{sid}: manifest URL outside published/ — {url}"
    if f"/{lane_asset_prefix}" not in url and not url.startswith(lane_asset_prefix):
        return f"{sid}: cross-tenant manifest URL outside its lane — {url}"
    return None


def _check_story_assets(
    client: S3Client, bucket: str, sid: str, root: str
) -> tuple[list[str], Story | None]:
    violations: list[str] = []
    story_key = f"{root}/stories/{sid}/{STORY_FILE}"
    try:
        body = client.get_object(Bucket=bucket, Key=story_key)["Body"].read()
    except ClientError as error:
        if _missing(error):
            violations.append(f"{sid}: story.json missing at {story_key}")
            return violations, None
        raise
    story = Story.model_validate_json(body)
    for page_obj in story.pages:
        if page_obj.audio:
            akey = f"{root}/stories/{sid}/{page_obj.audio.file}"
            if not _object_exists(client, bucket, akey):
                violations.append(f"{sid}: missing audio {page_obj.audio.file}")
        if page_obj.image:
            ikey = f"{root}/stories/{sid}/{page_obj.image}"
            if not _object_exists(client, bucket, ikey):
                violations.append(f"{sid}: missing image {page_obj.image}")
    return violations, story


def _object_exists(client: S3Client, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as error:
        if _missing(error):
            return False
        raise


def _find_orphan_story_dirs(
    client: S3Client, bucket: str, root: str, listed_story_ids: set[str]
) -> list[str]:
    """Story directories directly under ``{root}/stories/`` no manifest lists.

    Scoped to one lane: it walks only ``{root}/stories/`` so a shared audit
    never mistakes a family's story dir for a shared orphan, and vice versa.
    """
    orphans: list[str] = []
    story_dirs: list[str] = []
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=f"{root}/stories/"
    ):
        for item in page.get("Contents", []):
            parts = item["Key"].removeprefix(f"{root}/stories/").split("/", 1)
            if len(parts) == 2 and parts[0] not in story_dirs:
                story_dirs.append(parts[0])
    for sdir in story_dirs:
        if sdir not in listed_story_ids:
            orphans.append(f"{sdir}: story directory exists but no manifest lists it")
    return orphans


def audit_published_bucket(
    settings: Settings,
    *,
    client: S3Client | None = None,
) -> AuditResult:
    """Verify every reachable asset is approved and confined to its lane.

    Walks the shared shelf and every family overlay. For each lane, checks
    that every manifest entry resolves to real objects **inside that lane's own
    asset root**, that no URL points into ``pending/`` / outside ``published/``
    / into another lane (a cross-tenant leak), that every story.json's audio and
    image files exist, that no orphan story directory lurks unlisted, and that
    the public bucket holds nothing under ``pending/`` (that content belongs in
    the private pending bucket). Zero child-reachable unapproved or
    cross-tenant asset is the invariant.
    """
    client = client or _build_client(settings)
    bucket = settings.r2_bucket

    violations: list[str] = []
    manifests_checked = 0

    manifest_keys: list[str] = []
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=f"{PUBLISHED_PREFIX}/"
    ):
        for item in page.get("Contents", []):
            if item["Key"].endswith("/manifest.json"):
                manifest_keys.append(item["Key"])

    # Group manifests by lane root, tracking which story ids each lane lists so
    # orphan detection stays lane-local.
    listed_by_root: dict[str, set[str]] = {}
    for mkey in manifest_keys:
        lane = _manifest_lane(mkey)
        if lane is None:
            continue
        manifests_checked += 1
        language, family_token = lane
        root = _publish_root(family_token)
        lane_asset_prefix = f"{root}/stories/"
        listed = listed_by_root.setdefault(root, set())
        manifest, _ = _load_manifest(client, bucket, language, root)
        for entry in manifest.get("stories", []):
            sid = entry.get("id", "?")
            story_url = entry.get("story", "")
            cover_url = entry.get("cover", "")

            for url in (story_url, cover_url):
                v = _check_manifest_url(sid, url, lane_asset_prefix)
                if v:
                    violations.append(v)

            # Only walk assets for an entry that stays in its own lane; a
            # cross-tenant URL is already a violation above.
            if story_url and lane_asset_prefix in f"/{story_url}":
                listed.add(sid)
                story_violations, _ = _check_story_assets(client, bucket, sid, root)
                violations.extend(story_violations)

    # pending/ belongs in the private pending bucket. The public bucket serves
    # every key under its URL, so any pending/ object here is exposed (B1).
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=f"{PENDING_PREFIX}/"
    ):
        violations.extend(
            f"{item['Key']}: pending/ object in the public bucket — belongs in R2_PENDING_BUCKET"
            for item in page.get("Contents", [])
        )

    # Orphans: check every lane root that has a stories/ area, not only those
    # with a manifest (a family whose only manifest was deleted still leaves a
    # story dir behind).
    for root in _all_lane_roots(client, bucket):
        violations.extend(
            _find_orphan_story_dirs(client, bucket, root, listed_by_root.get(root, set()))
        )

    return AuditResult(violations=violations, manifests_checked=manifests_checked)


def _all_lane_roots(client: S3Client, bucket: str) -> list[str]:
    """Every lane root that owns a ``stories/`` area: the shared shelf and each
    family overlay present on the bucket."""
    roots: set[str] = set()
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=f"{PUBLISHED_PREFIX}/stories/"
    ):
        if page.get("Contents"):
            roots.add(PUBLISHED_PREFIX)
            break
    prefix = f"{PUBLISHED_PREFIX}/{FAMILIES_SEGMENT}/"
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            rest = item["Key"].removeprefix(prefix).split("/", 1)
            token = rest[0]
            if (
                len(rest) == 2
                and rest[1].startswith("stories/")
                and _FAMILY_TOKEN_RE.fullmatch(token)
            ):
                roots.add(f"{prefix}{token}")
    return sorted(roots)


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


class PublishedStory(BaseModel):
    id: str
    title: str
    language: str
    cover: str
    # None for the shared shelf (operator, global); the owning family for a
    # private overlay story. The operator library reads this to tag and to
    # scope a delete back to the right lane.
    family_token: str | None = None


def _manifest_lane(key: str) -> tuple[str, str | None] | None:
    """Map a manifest.json key to (language, family_token) or None if it is not
    a lane manifest.

    ``published/it/manifest.json`` → ``("it", None)`` (shared shelf).
    ``published/families/{token}/it/manifest.json`` → ``("it", token)``.
    Anything else (a deeper or malformed key) is ignored.
    """
    inner = key.removeprefix(f"{PUBLISHED_PREFIX}/").removesuffix("/manifest.json")
    parts = inner.split("/")
    if len(parts) == 1:
        return parts[0], None
    if len(parts) == 3 and parts[0] == FAMILIES_SEGMENT and _FAMILY_TOKEN_RE.fullmatch(parts[1]):
        return parts[2], parts[1]
    return None


# A lane is (language, family_token); a family_token of None is the shared shelf.
Lane = tuple[str, "str | None"]
# Top-level published/ children that are not shared-shelf language lanes.
_NON_LANE_DIRS = frozenset({FAMILIES_SEGMENT, "stories", "prompts"})


def _all_lanes(client: S3Client, bucket: str) -> list[Lane]:
    """Every lane in the bucket: shared languages, then each family's languages.

    Delimiter listings only: a few calls however many assets are published.
    """
    shared = child_prefixes(client, bucket, f"{PUBLISHED_PREFIX}/")
    lanes: list[Lane] = [(name, None) for name in shared if name not in _NON_LANE_DIRS]
    tokens = [
        name
        for name in child_prefixes(client, bucket, f"{PUBLISHED_PREFIX}/{FAMILIES_SEGMENT}/")
        if _FAMILY_TOKEN_RE.fullmatch(name)
    ]
    family_languages = parallel_map(
        lambda token: child_prefixes(client, bucket, f"{_publish_root(token)}/"), tokens
    )
    for token, languages in zip(tokens, family_languages, strict=True):
        lanes.extend((language, token) for language in languages)
    return lanes


def read_manifests(
    client: S3Client, bucket: str, lanes: list[Lane]
) -> list[tuple[Lane, dict[str, Any]]]:
    """Each lane's manifest (an empty one where none exists), read in parallel."""
    manifests = parallel_map(
        lambda lane: _load_manifest(client, bucket, lane[0], _publish_root(lane[1]))[0], lanes
    )
    return list(zip(lanes, manifests, strict=True))


def published_manifests(
    settings: Settings, *, client: S3Client | None = None
) -> list[tuple[Lane, dict[str, Any]]]:
    """Every lane's manifest: the shared shelf and every family overlay."""
    client = client or _build_client(settings)
    return read_manifests(client, settings.r2_bucket, _all_lanes(client, settings.r2_bucket))


def stories_from_manifests(manifests: list[tuple[Lane, dict[str, Any]]]) -> list[PublishedStory]:
    """Manifest entries as PublishedStory rows, sorted for stable pages."""
    stories = [
        PublishedStory(
            id=str(entry.get("id", "")),
            title=str(entry.get("title", "")),
            language=language,
            cover=str(entry.get("cover", "")),
            family_token=family_token,
        )
        for (language, family_token), manifest in manifests
        for entry in manifest.get("stories", [])
    ]
    stories.sort(key=lambda story: (story.family_token or "", story.language, story.id))
    return stories


def list_published_stories(
    settings: Settings,
    *,
    client: S3Client | None = None,
) -> list[PublishedStory]:
    """Every manifest entry across every lane, sorted for stable pages.

    Enumerates the shared shelf (``published/{lang}/manifest.json``) and every
    family overlay (``published/families/{token}/{lang}/manifest.json``),
    tagging each row with its owning family (None for the shared shelf) so the
    operator library can show and moderate private stories.
    """
    return stories_from_manifests(published_manifests(settings, client=client))


def list_family_shelf(
    settings: Settings,
    family_token: str,
    *,
    client: S3Client | None = None,
) -> list[PublishedStory]:
    """What one family's parent pages show: the shared shelf and that family's
    own overlay — never another family's lane.

    Reads the known manifest key for every supported language directly and in
    parallel: no bucket listing, about one round trip of wall time.
    """
    client = client or _build_client(settings)
    languages = get_args(Language)
    lanes: list[Lane] = [(language, None) for language in languages]
    lanes += [(language, family_token) for language in languages]
    return stories_from_manifests(read_manifests(client, settings.r2_bucket, lanes))


def list_orphan_story_dirs(
    settings: Settings,
    *,
    client: S3Client | None = None,
    manifests: list[tuple[Lane, dict[str, Any]]] | None = None,
) -> list[str]:
    """Story directories under published/stories/ that no manifest lists.

    Pass ``manifests`` (from published_manifests) to reuse a read the caller
    already made instead of reading every manifest again.
    """
    client = client or _build_client(settings)
    if manifests is None:
        manifests = published_manifests(settings, client=client)
    listed = {
        str(entry["id"])
        for _lane, manifest in manifests
        for entry in manifest.get("stories", [])
        if entry.get("story")
    }
    dirs = child_prefixes(client, settings.r2_bucket, f"{PUBLISHED_PREFIX}/stories/")
    return [story_id for story_id in dirs if story_id not in listed]
