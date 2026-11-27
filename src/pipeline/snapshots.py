"""Manifest snapshots and restore (M13, Issue #185).

R2 has no object versioning (``PutBucketVersioning`` is unsupported on its S3
API), so the app keeps its own: every manifest write in ``publish._write_manifest``
also writes the same bytes into the private pending bucket, as a dated history
copy and a "latest" copy, both keyed by the manifest's own path::

    snapshots/manifests/history/{manifest key}/{UTC timestamp}Z-{md5}.json
    snapshots/manifests/latest/{manifest key}
    snapshots/manifests/previous/{manifest key}

Before ``latest/`` is overwritten its old bytes are copied to ``previous/``, so
a bad write after a long quiet spell still leaves the last good state. An R2
lifecycle rule expires ``history/`` after 30 days; ``latest/`` and
``previous/`` never expire. ``snapshots/`` sits outside ``pending/`` so a cleanup of staged work
cannot reach it by prefix.

A restore writes only the manifest, through the same conditional write as
publish, and keeps an entry only when that story's ``story.json`` is already
published in the same lane and its URLs stay in that lane. Only publish writes
story directories, and publish runs only after parent approval, so a restore
can never list a story a parent has not approved.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, cast

import sentry_sdk
from botocore.exceptions import ClientError
from pydantic import BaseModel

from src.observability import redact_family_tokens
from src.pipeline import publish

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

    from src.config import Settings

logger = logging.getLogger(__name__)

SNAPSHOT_PREFIX = "snapshots/manifests"
LATEST_PREFIX = f"{SNAPSHOT_PREFIX}/latest"
HISTORY_PREFIX = f"{SNAPSHOT_PREFIX}/history"
PREVIOUS_PREFIX = f"{SNAPSHOT_PREFIX}/previous"
_STAMP_FORMAT = "%Y%m%dT%H%M%S%f"


class ManifestSnapshotError(Exception):
    """A manifest was written but its snapshot was not (reported, never raised)."""


class ManifestConflictError(ValueError):
    """The live manifest changed between a restore's plan and its write."""


def _now() -> datetime:
    return datetime.now(UTC)


def latest_key(manifest_key: str) -> str:
    return f"{LATEST_PREFIX}/{manifest_key}"


def previous_key(manifest_key: str) -> str:
    return f"{PREVIOUS_PREFIX}/{manifest_key}"


def history_key(manifest_key: str, body: bytes) -> str:
    md5 = hashlib.md5(body, usedforsecurity=False).hexdigest()
    return f"{HISTORY_PREFIX}/{manifest_key}/{_now().strftime(_STAMP_FORMAT)}Z-{md5}.json"


def _history_time(name: str) -> datetime | None:
    try:
        return datetime.strptime(name.split("Z-", 1)[0], _STAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def put_snapshot(
    client: S3Client, bucket: str, manifest_key: str, body: bytes, *, latest: bool = True
) -> str:
    """Write a history copy (and, by default, the latest copy) of ``body``.

    The latest copy it replaces is first copied server-side to ``previous/``.

    Raises on any failure. Returns the history copy's name, which is what
    ``restore-manifest --from`` takes.
    """
    key = history_key(manifest_key, body)
    client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
    if latest:
        try:
            client.copy_object(
                Bucket=bucket,
                Key=previous_key(manifest_key),
                CopySource={"Bucket": bucket, "Key": latest_key(manifest_key)},
            )
        except ClientError as error:
            if not publish._missing(error):
                raise
        client.put_object(
            Bucket=bucket,
            Key=latest_key(manifest_key),
            Body=body,
            ContentType="application/json",
        )
    return key.rsplit("/", 1)[-1]


def write_snapshot(client: S3Client, bucket: str, manifest_key: str, body: bytes) -> None:
    """Best-effort snapshot after a manifest write that already succeeded.

    The manifest is live by now, so a failure here must not fail the publish.
    It is logged and sent to Sentry as one event naming the manifest key with
    any family token hashed. The event is built fresh, with no chained cause,
    so nothing from the underlying error (which may carry the raw key) leaks.
    """
    try:
        put_snapshot(client, bucket, manifest_key, body)
    except Exception as error:
        safe_key = redact_family_tokens(manifest_key)
        logger.warning(
            "manifest_snapshot_failed",
            extra={
                "event": "manifest_snapshot_failed",
                "manifest_key": safe_key,
                "error_type": type(error).__name__,
            },
        )
        sentry_sdk.capture_exception(
            ManifestSnapshotError(
                f"manifest snapshot failed for {safe_key}: {type(error).__name__}"
            )
        )


def _manifest_keys(client: S3Client, bucket: str) -> list[str]:
    keys: list[str] = []
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=f"{publish.PUBLISHED_PREFIX}/"
    ):
        for item in page.get("Contents", []):
            key = item["Key"]
            if key.endswith("/manifest.json") and publish._manifest_lane(key) is not None:
                keys.append(key)
    return keys


def seed_snapshots(
    settings: Settings, *, client: S3Client | None = None, dry_run: bool = False
) -> list[str]:
    """Copy every live shared and family manifest into the snapshot area.

    Seeds snapshots for manifests that predate this feature, and serves as a
    manual backup before risky bucket work. ``dry_run`` lists only.
    """
    client = client or publish._build_client(settings)
    keys = _manifest_keys(client, settings.r2_bucket)
    if not dry_run:
        for key in keys:
            body = client.get_object(Bucket=settings.r2_bucket, Key=key)["Body"].read()
            put_snapshot(client, settings.pending_bucket, key, body)
    return keys


class SnapshotInfo(BaseModel):
    kind: Literal["latest", "previous", "history"]
    name: str
    written_at: datetime
    story_count: int


def _story_count(body: bytes) -> int:
    stories = json.loads(body).get("stories", [])
    return len(stories) if isinstance(stories, list) else 0


def list_snapshots(
    settings: Settings, manifest_key: str, *, client: S3Client | None = None
) -> list[SnapshotInfo]:
    """The latest and previous copies, then every history copy newest first."""
    _lane_of(manifest_key)
    client = client or publish._build_client(settings)
    bucket = settings.pending_bucket
    found: list[SnapshotInfo] = []
    for kind, key in (
        ("latest", latest_key(manifest_key)),
        ("previous", previous_key(manifest_key)),
    ):
        try:
            obj = client.get_object(Bucket=bucket, Key=key)
        except ClientError as error:
            if not publish._missing(error):
                raise
            continue
        found.append(
            SnapshotInfo(
                kind=cast("Literal['latest', 'previous']", kind),
                name=kind,
                written_at=obj["LastModified"],
                story_count=_story_count(obj["Body"].read()),
            )
        )
    prefix = f"{HISTORY_PREFIX}/{manifest_key}/"
    history: list[SnapshotInfo] = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            name = item["Key"].removeprefix(prefix)
            body = client.get_object(Bucket=bucket, Key=item["Key"])["Body"].read()
            history.append(
                SnapshotInfo(
                    kind="history",
                    name=name,
                    written_at=_history_time(name) or item["LastModified"],
                    story_count=_story_count(body),
                )
            )
    history.sort(key=lambda info: (info.written_at, info.name), reverse=True)
    return found + history


class DroppedEntry(BaseModel):
    story_id: str
    reason: str


class RestorePlan(BaseModel):
    manifest_key: str
    source: str
    added: list[str]
    removed: list[str]
    dropped: list[DroppedEntry]
    dropped_prompts: list[str]
    live_exists: bool
    written: bool
    backup: str | None


def _lane_of(manifest_key: str) -> tuple[str, str | None]:
    lane = publish._manifest_lane(manifest_key) if manifest_key.endswith("/manifest.json") else None
    if lane is None or not manifest_key.startswith(f"{publish.PUBLISHED_PREFIX}/"):
        raise ValueError(f"{manifest_key!r} is not a shelf manifest key")
    return lane


def _url_problem(entry: dict[str, Any], sid: str, root: str, public_base: str) -> str | None:
    story_url = entry.get("story")
    cover_url = entry.get("cover")
    if not isinstance(story_url, str) or not isinstance(cover_url, str):
        return "missing story or cover URL"
    story_dir = f"{public_base}/stories/{sid}/"
    problems = [
        publish._check_manifest_url(sid, url, f"{root}/stories/") for url in (story_url, cover_url)
    ]
    if any(".." in url.split("/") for url in (story_url, cover_url)):
        problems.append("URL has a '..' segment")
    if story_url != f"{story_dir}{publish.STORY_FILE}":
        problems.append(f"story URL is not this story's own story.json in its lane: {story_url}")
    if not cover_url.startswith(story_dir):
        problems.append(f"cover URL is outside this story's directory: {cover_url}")
    return next((problem for problem in problems if problem is not None), None)


def _story_published(client: S3Client, bucket: str, sid: str, root: str) -> bool:
    return publish._object_exists(client, bucket, f"{root}/stories/{sid}/{publish.STORY_FILE}")


def _entry_problem(
    client: S3Client, bucket: str, entry: Any, root: str, public_base: str
) -> str | None:
    """Why a snapshot entry may not be restored, or None when it may."""
    sid = entry.get("id") if isinstance(entry, dict) else None
    if not isinstance(sid, str) or not sid or "/" in sid or sid in {".", ".."}:
        return "invalid story id"
    problem = _url_problem(entry, sid, root, public_base)
    if problem is not None:
        return problem
    if not _story_published(client, bucket, sid, root):
        return f"story.json is not published at {root}/stories/{sid}/{publish.STORY_FILE}"
    return None


def _prompt_survives(client: S3Client, bucket: str, url: Any, public_base: str, root: str) -> bool:
    if not isinstance(url, str) or not url.startswith(f"{public_base}/"):
        return False
    key = f"{publish.PUBLISHED_PREFIX}/{url.removeprefix(f'{public_base}/')}"
    if not key.startswith(f"{root}/prompts/") or ".." in key.split("/"):
        return False
    return publish._object_exists(client, bucket, key)


def _canonical(manifest: dict[str, Any]) -> bytes:
    """The bytes ``_write_manifest`` would write for ``manifest``."""
    return json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode()


def _ids(manifest: dict[str, Any]) -> list[str]:
    return [
        str(entry.get("id")) for entry in manifest.get("stories", []) if isinstance(entry, dict)
    ]


def _read_snapshot(client: S3Client, bucket: str, manifest_key: str, source: str | None) -> Any:
    if source is not None and ("/" in source or source in {".", ".."}):
        raise ValueError(f"invalid snapshot name {source!r}")
    if source is None or source == "latest":
        key = latest_key(manifest_key)
    elif source == "previous":
        key = previous_key(manifest_key)
    else:
        key = f"{HISTORY_PREFIX}/{manifest_key}/{source}"
    try:
        return json.loads(client.get_object(Bucket=bucket, Key=key)["Body"].read())
    except ClientError as error:
        if publish._missing(error):
            raise ValueError(f"no snapshot {source or 'latest'!r} for {manifest_key}") from None
        raise


def _read_live(
    client: S3Client, bucket: str, manifest_key: str, language: str
) -> tuple[bytes | None, dict[str, Any]]:
    try:
        live = client.get_object(Bucket=bucket, Key=manifest_key)
    except ClientError as error:
        if not publish._missing(error):
            raise
        return None, {"language": language, "prompts": {}, "stories": []}
    body = live["Body"].read()
    loaded: dict[str, Any] = json.loads(body)
    return body, loaded


def _filter_snapshot(
    client: S3Client,
    bucket: str,
    snapshot: dict[str, Any],
    *,
    language: str,
    root: str,
    public_base: str,
) -> tuple[dict[str, Any], list[DroppedEntry], list[str]]:
    """The snapshot with every entry and prompt that may not be restored removed."""
    kept: list[dict[str, Any]] = []
    dropped: list[DroppedEntry] = []
    for entry in snapshot.get("stories", []):
        problem = _entry_problem(client, bucket, entry, root, public_base)
        if problem is None:
            kept.append(entry)
        else:
            sid = entry.get("id") if isinstance(entry, dict) else None
            dropped.append(DroppedEntry(story_id=str(sid), reason=problem))
    restored = {**snapshot, "language": snapshot.get("language", language), "stories": kept}
    dropped_prompts: list[str] = []
    if "prompts" in snapshot:
        prompts = snapshot["prompts"] if isinstance(snapshot["prompts"], dict) else {}
        restored["prompts"] = {}
        for name, url in prompts.items():
            if _prompt_survives(client, bucket, url, public_base, root):
                restored["prompts"][name] = url
            else:
                dropped_prompts.append(str(name))
    return restored, dropped, dropped_prompts


def restore_manifest(
    settings: Settings,
    manifest_key: str,
    *,
    source: str | None = None,
    yes: bool = False,
    client: S3Client | None = None,
) -> RestorePlan:
    """Plan, and with ``yes`` apply, a restore of one manifest from a snapshot.

    ``source`` names a history copy, or ``"previous"``; ``None`` or
    ``"latest"`` means the latest copy. Without ``yes`` nothing is written,
    and a restore that would change nothing writes nothing either. With
    ``yes`` the live manifest (if any) is first copied into history, and the
    restore aborts if that copy fails, since it is the undo. The write goes
    through ``_write_manifest`` and fails closed: if the live manifest changed
    since the plan, it raises ManifestConflictError and writes nothing. Each
    kept entry's story.json is checked again just before the write.
    """
    language, family_token = _lane_of(manifest_key)
    root = publish._publish_root(family_token)
    client = client or publish._build_client(settings)
    bucket = settings.r2_bucket
    pending = settings.pending_bucket
    public_base = settings.r2_public_base.rstrip("/")
    if family_token is not None:
        public_base = f"{public_base}/{publish.FAMILIES_SEGMENT}/{family_token}"

    snapshot = _read_snapshot(client, pending, manifest_key, source)
    restored, dropped, dropped_prompts = _filter_snapshot(
        client, bucket, snapshot, language=language, root=root, public_base=public_base
    )
    live_body, base = _read_live(client, bucket, manifest_key, language)
    live_ids = _ids(base)
    kept_ids = _ids(restored)
    plan = RestorePlan(
        manifest_key=manifest_key,
        source=source or "latest",
        added=[sid for sid in kept_ids if sid not in live_ids],
        removed=[sid for sid in live_ids if sid not in kept_ids],
        dropped=dropped,
        dropped_prompts=dropped_prompts,
        live_exists=live_body is not None,
        written=False,
        backup=None,
    )
    if not yes or _canonical(restored) == live_body:
        return plan

    backup = (
        put_snapshot(client, pending, manifest_key, live_body, latest=False)
        if live_body is not None
        else None
    )
    vanished: list[DroppedEntry] = []

    def mutate(manifest: dict[str, Any]) -> None:
        if manifest != base:
            raise ManifestConflictError(
                f"{redact_family_tokens(manifest_key)}: manifest changed since planned; "
                "rerun the dry run"
            )
        stories = []
        for entry in restored["stories"]:
            if _story_published(client, bucket, entry["id"], root):
                stories.append(entry)
            else:
                vanished.append(
                    DroppedEntry(
                        story_id=entry["id"], reason="story.json vanished before the write"
                    )
                )
        manifest.clear()
        manifest.update({**restored, "stories": stories})

    _, wrote = publish._write_manifest(
        client,
        bucket,
        manifest_key,
        load=lambda: publish._load_manifest(client, bucket, language, root),
        mutate=mutate,
        snapshot_bucket=pending,
    )
    logger.info(
        "manifest_restored",
        extra={
            "event": "manifest_restored",
            "manifest_key": redact_family_tokens(manifest_key),
            "source": plan.source,
            "stories": len(kept_ids),
            "dropped": len(dropped),
            "written": wrote,
        },
    )
    return plan.model_copy(
        update={
            "written": wrote,
            "backup": backup,
            "dropped": [*dropped, *vanished],
            "added": [sid for sid in plan.added if sid not in {d.story_id for d in vanished}],
        }
    )
