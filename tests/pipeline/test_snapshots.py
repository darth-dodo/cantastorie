"""Behavior specs for manifest snapshots and restore (M13, Issue #185).

R2 has no object versioning, so every manifest write the app makes leaves a
copy of its bytes in the private pending bucket under ``snapshots/manifests/``,
and an operator restore puts one back without ever listing a story that is not
already published in the same lane. All S3 traffic is served by moto.
"""

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws
from mypy_boto3_s3 import S3Client

from src.config import Settings
from src.observability import family_hash
from src.pipeline import snapshots
from src.pipeline.publish import (
    audit_published_bucket,
    publish_story,
    stage_story,
    unpublish_story,
)
from src.pipeline.snapshots import (
    HISTORY_PREFIX,
    LATEST_PREFIX,
    PREVIOUS_PREFIX,
    ManifestConflictError,
    history_key,
    latest_key,
    list_snapshots,
    restore_manifest,
    seed_snapshots,
)
from tests.pipeline.test_publish import _assembled

BUCKET = "cantastorie-published"
PENDING_BUCKET = "cantastorie-pending"
PUBLIC_BASE = "https://cdn.example.test/published"
TOKEN = "0123456789abcdef0123456789abcdef"
OTHER_TOKEN = "fedcba9876543210fedcba9876543210"
SHARED_KEY = "published/it/manifest.json"
FAMILY_KEY = f"published/families/{TOKEN}/it/manifest.json"


@pytest.fixture
def s3() -> Iterator[S3Client]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.create_bucket(Bucket=PENDING_BUCKET)
        yield client


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        staging_dir=tmp_path / "staging",
        r2_endpoint_url="https://r2.example.test",
        r2_access_key_id="test-access-key",
        r2_secret_access_key="test-secret-key",
        r2_bucket=BUCKET,
        r2_pending_bucket=PENDING_BUCKET,
        r2_public_base=PUBLIC_BASE,
    )


def _keys(client: S3Client, prefix: str = "", bucket: str = BUCKET) -> list[str]:
    keys: list[str] = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(item["Key"] for item in page.get("Contents", []))
    return keys


def _body(client: S3Client, key: str, bucket: str = BUCKET) -> bytes:
    return client.get_object(Bucket=bucket, Key=key)["Body"].read()


def _history(client: S3Client, manifest_key: str) -> list[str]:
    return _keys(client, f"{HISTORY_PREFIX}/{manifest_key}/", PENDING_BUCKET)


def _publish(tmp_path: Path, s3: S3Client, story_id: str, family_token: str | None = None) -> None:
    settings = _settings(tmp_path)
    stage_story(_assembled(tmp_path, story_id=story_id), settings, client=s3)
    publish_story(story_id, settings, client=s3, family_token=family_token)


def _ids(client: S3Client, key: str) -> list[str]:
    return [entry["id"] for entry in json.loads(_body(client, key))["stories"]]


# 1. Snapshot on every manifest write


def test_shared_and_family_manifests_map_to_distinct_snapshot_keys() -> None:
    body = b'{"stories": []}'
    md5 = hashlib.md5(body, usedforsecurity=False).hexdigest()

    shared_latest = latest_key(SHARED_KEY)
    family_latest = latest_key(FAMILY_KEY)
    shared_history = history_key(SHARED_KEY, body)

    assert shared_latest == f"{LATEST_PREFIX}/{SHARED_KEY}"
    assert family_latest == f"{LATEST_PREFIX}/{FAMILY_KEY}"
    assert shared_latest != family_latest
    assert shared_history.startswith(f"{HISTORY_PREFIX}/{SHARED_KEY}/")
    assert shared_history.endswith(f"Z-{md5}.json")
    assert history_key(FAMILY_KEY, body).startswith(f"{HISTORY_PREFIX}/{FAMILY_KEY}/")
    assert LATEST_PREFIX.startswith("snapshots/manifests/")
    assert not LATEST_PREFIX.startswith("pending/")


def test_publishing_leaves_a_latest_and_a_history_copy_of_the_manifest(
    tmp_path: Path, s3: S3Client
) -> None:
    _publish(tmp_path, s3, "first-story")

    live = _body(s3, SHARED_KEY)
    history = _history(s3, SHARED_KEY)
    assert len(history) == 1
    assert _body(s3, history[0], PENDING_BUCKET) == live
    assert _body(s3, latest_key(SHARED_KEY), PENDING_BUCKET) == live
    assert _keys(s3, "snapshots/") == []


def test_republishing_an_unchanged_story_adds_no_history_copy(tmp_path: Path, s3: S3Client) -> None:
    _publish(tmp_path, s3, "first-story")
    publish_story("first-story", _settings(tmp_path), client=s3)

    assert len(_history(s3, SHARED_KEY)) == 1


def test_a_family_publish_snapshots_under_the_family_manifest_path(
    tmp_path: Path, s3: S3Client
) -> None:
    _publish(tmp_path, s3, "family-story", family_token=TOKEN)

    assert len(_history(s3, FAMILY_KEY)) == 1
    assert _history(s3, SHARED_KEY) == []
    assert _body(s3, latest_key(FAMILY_KEY), PENDING_BUCKET) == _body(s3, FAMILY_KEY)


def test_unpublish_snapshots_the_manifest_it_wrote(tmp_path: Path, s3: S3Client) -> None:
    _publish(tmp_path, s3, "first-story")
    _publish(tmp_path, s3, "second-story")

    unpublish_story("first-story", _settings(tmp_path), client=s3)

    assert len(_history(s3, SHARED_KEY)) == 3
    assert _body(s3, latest_key(SHARED_KEY), PENDING_BUCKET) == _body(s3, SHARED_KEY)
    assert _ids(s3, SHARED_KEY) == ["second-story"]


def test_a_failed_snapshot_never_fails_the_publish_and_alerts_once(
    tmp_path: Path, s3: S3Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[BaseException] = []
    monkeypatch.setattr(snapshots.sentry_sdk, "capture_exception", events.append)
    put_object = s3.put_object

    def pending_bucket_down(**kwargs: Any) -> Any:
        if kwargs["Bucket"] == PENDING_BUCKET and kwargs["Key"].startswith("snapshots/"):
            raise RuntimeError(f"bucket unavailable for {kwargs['Key']}")
        return put_object(**kwargs)

    settings = _settings(tmp_path)
    stage_story(_assembled(tmp_path, story_id="family-story"), settings, client=s3)
    monkeypatch.setattr(s3, "put_object", pending_bucket_down)

    result = publish_story("family-story", settings, client=s3, family_token=TOKEN)

    assert result.manifest_story_ids == ["family-story"]
    assert _ids(s3, FAMILY_KEY) == ["family-story"]
    assert len(events) == 1
    message = str(events[0])
    assert f"published/families/{family_hash(TOKEN)}/it/manifest.json" in message
    assert TOKEN not in message
    assert events[0].__cause__ is None
    assert events[0].__context__ is None


# 2. Seed and list


def _put_manifest(s3: S3Client, key: str, manifest: dict[str, Any]) -> bytes:
    body = json.dumps(manifest).encode()
    s3.put_object(Bucket=BUCKET, Key=key, Body=body, ContentType="application/json")
    return body


def test_seeding_copies_every_live_manifest_and_a_dry_run_writes_nothing(
    tmp_path: Path, s3: S3Client
) -> None:
    shared = _put_manifest(s3, SHARED_KEY, {"language": "it", "stories": []})
    family = _put_manifest(s3, FAMILY_KEY, {"language": "it", "stories": []})
    s3.put_object(Bucket=BUCKET, Key="published/stories/x/story.json", Body=b"{}")
    settings = _settings(tmp_path)

    planned = seed_snapshots(settings, client=s3, dry_run=True)

    assert sorted(planned) == sorted([SHARED_KEY, FAMILY_KEY])
    assert _keys(s3, "snapshots/", PENDING_BUCKET) == []

    seeded = seed_snapshots(settings, client=s3)

    assert sorted(seeded) == sorted([SHARED_KEY, FAMILY_KEY])
    assert _body(s3, latest_key(SHARED_KEY), PENDING_BUCKET) == shared
    assert _body(s3, latest_key(FAMILY_KEY), PENDING_BUCKET) == family
    assert len(_history(s3, SHARED_KEY)) == 1
    assert len(_history(s3, FAMILY_KEY)) == 1


def test_listing_shows_the_latest_and_history_copies_newest_first_with_story_counts(
    tmp_path: Path, s3: S3Client
) -> None:
    _publish(tmp_path, s3, "first-story")
    _publish(tmp_path, s3, "second-story")

    listed = list_snapshots(_settings(tmp_path), SHARED_KEY, client=s3)

    assert [item.kind for item in listed] == ["latest", "previous", "history", "history"]
    assert [item.story_count for item in listed] == [2, 1, 2, 1]
    history_times = [item.written_at for item in listed if item.kind == "history"]
    assert history_times == sorted(history_times, reverse=True)
    assert listed[2].name == _history(s3, SHARED_KEY)[-1].rsplit("/", 1)[-1]


# 3. Restore with the review gate intact


def _oldest_history_name(s3: S3Client, manifest_key: str) -> str:
    return sorted(_history(s3, manifest_key))[0].rsplit("/", 1)[-1]


def test_a_restore_without_yes_reports_the_change_and_writes_nothing(
    tmp_path: Path, s3: S3Client
) -> None:
    _publish(tmp_path, s3, "first-story")
    first_only = _oldest_history_name(s3, SHARED_KEY)
    _publish(tmp_path, s3, "second-story")
    before = _body(s3, SHARED_KEY)
    pending_before = _keys(s3, "", PENDING_BUCKET)

    plan = restore_manifest(_settings(tmp_path), SHARED_KEY, source=first_only, client=s3)

    assert plan.written is False
    assert plan.added == []
    assert plan.removed == ["second-story"]
    assert plan.dropped == []
    assert _body(s3, SHARED_KEY) == before
    assert _keys(s3, "", PENDING_BUCKET) == pending_before


def test_a_restore_drops_a_story_unpublished_since_the_snapshot(
    tmp_path: Path, s3: S3Client
) -> None:
    _publish(tmp_path, s3, "first-story")
    _publish(tmp_path, s3, "second-story")
    both = sorted(_history(s3, SHARED_KEY))[-1].rsplit("/", 1)[-1]
    unpublish_story("second-story", _settings(tmp_path), client=s3)

    plan = restore_manifest(_settings(tmp_path), SHARED_KEY, source=both, yes=True, client=s3)

    assert [item.story_id for item in plan.dropped] == ["second-story"]
    assert plan.added == []
    assert _ids(s3, SHARED_KEY) == ["first-story"]


def test_a_restore_drops_cross_lane_entries_from_a_family_snapshot(
    tmp_path: Path, s3: S3Client
) -> None:
    _publish(tmp_path, s3, "family-story", family_token=TOKEN)
    _publish(tmp_path, s3, "shared-story")
    snapshot = json.loads(_body(s3, FAMILY_KEY))
    snapshot["stories"] += [
        {
            "id": "shared-story",
            "story": f"{PUBLIC_BASE}/stories/shared-story/story.json",
            "cover": f"{PUBLIC_BASE}/stories/shared-story/cover.webp",
        },
        {
            "id": "other-story",
            "story": f"{PUBLIC_BASE}/families/{OTHER_TOKEN}/stories/other-story/story.json",
            "cover": f"{PUBLIC_BASE}/families/{OTHER_TOKEN}/stories/other-story/cover.webp",
        },
    ]
    s3.put_object(
        Bucket=PENDING_BUCKET,
        Key=latest_key(FAMILY_KEY),
        Body=json.dumps(snapshot).encode(),
    )

    plan = restore_manifest(_settings(tmp_path), FAMILY_KEY, yes=True, client=s3)

    assert sorted(item.story_id for item in plan.dropped) == ["other-story", "shared-story"]
    assert _ids(s3, FAMILY_KEY) == ["family-story"]
    assert audit_published_bucket(_settings(tmp_path), client=s3).violations == []


def test_a_confirmed_restore_writes_only_the_manifest_in_the_public_bucket(
    tmp_path: Path, s3: S3Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(tmp_path, s3, "first-story")
    first_only = _oldest_history_name(s3, SHARED_KEY)
    _publish(tmp_path, s3, "second-story")
    public_writes: list[str] = []
    for name in ("put_object", "copy_object", "delete_object", "delete_objects"):
        original = getattr(s3, name)

        def record(original: Any = original, name: str = name, **kwargs: Any) -> Any:
            if kwargs["Bucket"] == BUCKET:
                public_writes.append(kwargs.get("Key", name))
            return original(**kwargs)

        monkeypatch.setattr(s3, name, record)

    restore_manifest(_settings(tmp_path), SHARED_KEY, source=first_only, yes=True, client=s3)

    assert public_writes == [SHARED_KEY]
    assert _ids(s3, SHARED_KEY) == ["first-story"]


def test_a_restore_can_be_undone_to_the_exact_prior_bytes(tmp_path: Path, s3: S3Client) -> None:
    _publish(tmp_path, s3, "first-story")
    first_only = _oldest_history_name(s3, SHARED_KEY)
    _publish(tmp_path, s3, "second-story")
    before = _body(s3, SHARED_KEY)
    settings = _settings(tmp_path)

    plan = restore_manifest(settings, SHARED_KEY, source=first_only, yes=True, client=s3)

    assert plan.backup is not None
    backup_key = f"{HISTORY_PREFIX}/{SHARED_KEY}/{plan.backup}"
    assert _body(s3, backup_key, PENDING_BUCKET) == before
    assert _body(s3, latest_key(SHARED_KEY), PENDING_BUCKET) == _body(s3, SHARED_KEY)

    restore_manifest(settings, SHARED_KEY, source=plan.backup, yes=True, client=s3)

    assert _body(s3, SHARED_KEY) == before


def test_a_restore_aborts_when_the_backup_copy_fails(
    tmp_path: Path, s3: S3Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(tmp_path, s3, "first-story")
    first_only = _oldest_history_name(s3, SHARED_KEY)
    _publish(tmp_path, s3, "second-story")
    before = _body(s3, SHARED_KEY)
    put_object = s3.put_object

    def pending_bucket_down(**kwargs: Any) -> Any:
        if kwargs["Bucket"] == PENDING_BUCKET:
            raise RuntimeError("pending bucket unavailable")
        return put_object(**kwargs)

    monkeypatch.setattr(s3, "put_object", pending_bucket_down)

    with pytest.raises(RuntimeError):
        restore_manifest(_settings(tmp_path), SHARED_KEY, source=first_only, yes=True, client=s3)

    assert _body(s3, SHARED_KEY) == before


def test_a_restore_that_loses_a_race_fails_closed_and_keeps_the_concurrent_publish(
    tmp_path: Path, s3: S3Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(tmp_path, s3, "first-story")
    first_only = _oldest_history_name(s3, SHARED_KEY)
    _publish(tmp_path, s3, "second-story")
    put_object = s3.put_object
    raced = False

    def race_a_concurrent_publish(**kwargs: Any) -> Any:
        nonlocal raced
        if kwargs["Bucket"] == BUCKET and kwargs["Key"] == SHARED_KEY and not raced:
            raced = True
            current = json.loads(_body(s3, SHARED_KEY))
            current["stories"].append({"id": "concurrent-story"})
            put_object(
                Bucket=BUCKET,
                Key=SHARED_KEY,
                Body=json.dumps(current).encode(),
                ContentType="application/json",
            )
        return put_object(**kwargs)

    monkeypatch.setattr(s3, "put_object", race_a_concurrent_publish)

    with pytest.raises(ManifestConflictError, match="manifest changed since planned"):
        restore_manifest(_settings(tmp_path), SHARED_KEY, source=first_only, yes=True, client=s3)

    assert raced
    assert sorted(_ids(s3, SHARED_KEY)) == ["concurrent-story", "first-story", "second-story"]


def test_a_restore_drops_an_entry_whose_story_vanishes_before_the_write(
    tmp_path: Path, s3: S3Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(tmp_path, s3, "first-story")
    _publish(tmp_path, s3, "second-story")
    both = sorted(_history(s3, SHARED_KEY))[-1].rsplit("/", 1)[-1]
    _publish(tmp_path, s3, "third-story")
    put_snapshot = snapshots.put_snapshot

    def unpublish_assets_meanwhile(*args: Any, **kwargs: Any) -> str:
        s3.delete_object(Bucket=BUCKET, Key="published/stories/second-story/story.json")
        return put_snapshot(*args, **kwargs)

    monkeypatch.setattr(snapshots, "put_snapshot", unpublish_assets_meanwhile)

    plan = restore_manifest(_settings(tmp_path), SHARED_KEY, source=both, yes=True, client=s3)

    assert _ids(s3, SHARED_KEY) == ["first-story"]
    assert "second-story" in [item.story_id for item in plan.dropped]


def _restore_family_entry(tmp_path: Path, s3: S3Client, entry: dict[str, Any]) -> list[str]:
    _publish(tmp_path, s3, "family-story", family_token=TOKEN)
    _publish(tmp_path, s3, "shared-story")
    snapshot = json.loads(_body(s3, FAMILY_KEY))
    snapshot["stories"] = [entry]
    s3.put_object(
        Bucket=PENDING_BUCKET, Key=latest_key(FAMILY_KEY), Body=json.dumps(snapshot).encode()
    )
    plan = restore_manifest(_settings(tmp_path), FAMILY_KEY, client=s3)
    return [item.story_id for item in plan.dropped]


def test_a_restore_drops_a_cover_that_climbs_out_of_its_lane(tmp_path: Path, s3: S3Client) -> None:
    lane = f"{PUBLIC_BASE}/families/{TOKEN}/stories/family-story"
    dropped = _restore_family_entry(
        tmp_path,
        s3,
        {
            "id": "family-story",
            "story": f"{lane}/story.json",
            "cover": f"{lane}/../../../../stories/shared-story/cover.webp",
        },
    )

    assert dropped == ["family-story"]


def test_a_restore_drops_an_entry_on_a_foreign_host(tmp_path: Path, s3: S3Client) -> None:
    lane = f"https://evil.example.test/published/families/{TOKEN}/stories/family-story"
    dropped = _restore_family_entry(
        tmp_path,
        s3,
        {"id": "family-story", "story": f"{lane}/story.json", "cover": f"{lane}/cover.webp"},
    )

    assert dropped == ["family-story"]


def test_each_write_keeps_the_replaced_latest_as_a_non_expiring_previous_copy(
    tmp_path: Path, s3: S3Client
) -> None:
    _publish(tmp_path, s3, "first-story")
    first_latest = _body(s3, latest_key(SHARED_KEY), PENDING_BUCKET)
    _publish(tmp_path, s3, "second-story")

    previous_key = f"{PREVIOUS_PREFIX}/{SHARED_KEY}"
    assert _body(s3, previous_key, PENDING_BUCKET) == first_latest
    assert not PREVIOUS_PREFIX.startswith(HISTORY_PREFIX)
    listed = list_snapshots(_settings(tmp_path), SHARED_KEY, client=s3)
    assert [item.kind for item in listed][:2] == ["latest", "previous"]
    assert listed[1].story_count == 1

    plan = restore_manifest(_settings(tmp_path), SHARED_KEY, source="previous", yes=True, client=s3)

    assert plan.removed == ["second-story"]
    assert _ids(s3, SHARED_KEY) == ["first-story"]


def test_a_restore_that_changes_nothing_takes_no_backup(tmp_path: Path, s3: S3Client) -> None:
    _publish(tmp_path, s3, "first-story")
    history_before = _history(s3, SHARED_KEY)

    plan = restore_manifest(_settings(tmp_path), SHARED_KEY, yes=True, client=s3)

    assert plan.written is False
    assert plan.backup is None
    assert _history(s3, SHARED_KEY) == history_before


def test_restoring_the_latest_copy_recreates_a_deleted_manifest(
    tmp_path: Path, s3: S3Client
) -> None:
    _publish(tmp_path, s3, "first-story")
    _publish(tmp_path, s3, "second-story")
    s3.delete_object(Bucket=BUCKET, Key=SHARED_KEY)
    s3.delete_object(Bucket=BUCKET, Key="published/stories/second-story/story.json")

    plan = restore_manifest(_settings(tmp_path), SHARED_KEY, yes=True, client=s3)

    assert plan.backup is None
    assert plan.added == ["first-story"]
    assert [item.story_id for item in plan.dropped] == ["second-story"]
    assert _ids(s3, SHARED_KEY) == ["first-story"]


def test_a_restore_refuses_a_key_that_is_not_a_lane_manifest(tmp_path: Path, s3: S3Client) -> None:
    with pytest.raises(ValueError, match="not a shelf manifest"):
        restore_manifest(_settings(tmp_path), "published/stories/x/story.json", client=s3)
