"""Behavior specs for workshop run records (AI-387, ADR-005).

A run record is the durable trace of one story request: queued → running →
staged → approved | rejected, with a retryable failed. Records persist to R2
under pending/{family-token}/runs/ because Render's disk is ephemeral — moto
serves all S3 traffic here, zero network.
"""

import json
from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws
from mypy_boto3_s3 import S3Client
from pydantic import ValidationError

from src.config import Settings
from src.pipeline.models import PREMISE_MAX_LENGTH
from src.workshop import records
from src.workshop.records import (
    InvalidTransition,
    RunStore,
    StoryRequest,
    new_run,
    story_request_error_message,
)

BUCKET = "cantastorie-published"
PENDING_BUCKET = "cantastorie-pending"

REQUEST = StoryRequest(theme="the_sleepy_sea", language="it")


@pytest.fixture
def s3() -> Iterator[S3Client]:
    """A moto-backed S3 client with the bucket already created."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.create_bucket(Bucket=PENDING_BUCKET)
        yield client


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        r2_endpoint_url="http://localhost",
        r2_access_key_id="test",
        r2_secret_access_key="test",
        r2_bucket=BUCKET,
        r2_pending_bucket=PENDING_BUCKET,
        r2_public_base="http://localhost",
    )


def test_records_live_in_the_private_pending_bucket_when_configured(s3: S3Client) -> None:
    """The published bucket is public by design (setup.md); pending content
    must never share it in production. R2_PENDING_BUCKET points the store at
    a private bucket; the public one stays untouched."""
    settings = _settings()
    store = RunStore(settings, client=s3)
    record = new_run("family-abc", REQUEST)

    store.save(record)

    pending = s3.list_objects_v2(Bucket=PENDING_BUCKET)
    assert [obj["Key"] for obj in pending["Contents"]] == [
        f"pending/family-abc/runs/{record.id}.json"
    ]
    assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET)  # public bucket untouched
    assert store.load("family-abc", record.id) == record


def test_new_run_starts_queued_with_identity_and_timestamps() -> None:
    record = new_run("family-abc", REQUEST)

    assert record.state == "queued"
    assert record.family_token == "family-abc"
    assert record.request == REQUEST
    assert record.id
    assert record.story_ids == []
    assert record.error is None
    assert record.updated_at >= record.created_at


def test_two_runs_get_distinct_ids() -> None:
    assert new_run("family-abc", REQUEST).id != new_run("family-abc", REQUEST).id


def test_advance_moves_queued_to_running_and_touches_updated_at() -> None:
    record = new_run("family-abc", REQUEST)

    running = record.advance("running")

    assert running.state == "running"
    assert running.updated_at >= record.updated_at
    assert record.state == "queued"  # advance returns a copy; records are values


def test_advance_rejects_a_transition_the_lifecycle_does_not_allow() -> None:
    record = new_run("family-abc", REQUEST)

    with pytest.raises(InvalidTransition):
        record.advance("approved")  # queued → approved skips the whole pipeline


def test_a_queued_run_interrupted_before_starting_can_be_failed() -> None:
    # If the process dies while a run is still queued, the reaper (AI-417) must
    # retire it: queued → failed is a legitimate edge, not only running → failed.
    record = new_run("family-abc", REQUEST)

    failed = record.advance("failed", error="interrupted")

    assert failed.state == "failed"
    assert failed.error == "interrupted"


def test_failed_is_retryable_back_to_queued() -> None:
    record = new_run("family-abc", REQUEST).advance("running").advance("failed", error="boom")

    retried = record.advance("queued")

    assert retried.state == "queued"
    assert retried.error is None  # a retry starts clean


def test_staged_resolves_to_approved_or_rejected_only() -> None:
    staged = new_run("family-abc", REQUEST).advance("running").advance("staged")

    assert staged.advance("approved").state == "approved"
    with pytest.raises(InvalidTransition):
        staged.advance("running")


def test_a_story_request_needs_no_count() -> None:
    """AI-480: one run is one story, so a request has no count at all."""
    request = StoryRequest(theme="the_sleepy_sea", language="it")

    assert "count" not in request.model_dump()


def test_a_legacy_request_with_a_count_loads_and_drops_it() -> None:
    """Records persisted before AI-480 carry `count`; they still load, and the
    next save writes no count."""
    request = StoryRequest.model_validate({"theme": "the_sleepy_sea", "language": "it", "count": 3})

    assert request == StoryRequest(theme="the_sleepy_sea", language="it")
    assert "count" not in request.model_dump()


def test_store_round_trips_a_record_under_the_pending_prefix(s3: S3Client) -> None:
    store = RunStore(_settings(), client=s3)
    record = new_run("family-abc", REQUEST)

    store.save(record)
    loaded = store.load("family-abc", record.id)

    assert loaded == record
    keys = [obj["Key"] for obj in s3.list_objects_v2(Bucket=PENDING_BUCKET)["Contents"]]
    assert keys == [f"pending/family-abc/runs/{record.id}.json"]


def test_store_load_of_an_unknown_run_returns_none(s3: S3Client) -> None:
    store = RunStore(_settings(), client=s3)

    assert store.load("family-abc", "no-such-run") is None


def test_store_delete_removes_a_record_from_the_pending_bucket(s3: S3Client) -> None:
    store = RunStore(_settings(), client=s3)
    record = new_run("family-abc", REQUEST)
    store.save(record)

    store.delete(record.family_token, record.id)

    assert store.load(record.family_token, record.id) is None


def test_store_lists_runs_filtered_by_state_across_families(s3: S3Client) -> None:
    store = RunStore(_settings(), client=s3)
    queued = new_run("family-abc", REQUEST)
    running = new_run("family-xyz", REQUEST).advance("running")
    store.save(queued)
    store.save(running)

    assert {r.id for r in store.list_runs()} == {queued.id, running.id}
    assert [r.id for r in store.list_runs(state="running")] == [running.id]
    assert [r.id for r in store.list_runs(family_token="family-abc")] == [queued.id]


def test_saving_an_advanced_record_overwrites_in_place(s3: S3Client) -> None:
    store = RunStore(_settings(), client=s3)
    record = new_run("family-abc", REQUEST)
    store.save(record)

    store.save(record.advance("running"))

    loaded = store.load("family-abc", record.id)
    assert loaded is not None
    assert loaded.state == "running"
    contents = s3.list_objects_v2(Bucket=PENDING_BUCKET)["Contents"]
    assert len(contents) == 1  # same key, new bytes — not a second object


def test_store_rejects_saving_a_stale_loaded_record(s3: S3Client) -> None:
    store = RunStore(_settings(), client=s3)
    record = new_run("family-abc", REQUEST)
    store.save(record)
    first = store.load(record.family_token, record.id)
    second = store.load(record.family_token, record.id)
    assert first is not None
    assert second is not None

    store.save(first.advance("running"))

    with pytest.raises(records.ConcurrentModificationError):
        store.save(second.advance("running"))


def test_store_list_skips_malformed_run_records(
    s3: S3Client, caplog: pytest.LogCaptureFixture
) -> None:
    store = RunStore(_settings(), client=s3)
    record = new_run("family-abc", REQUEST)
    store.save(record)
    malformed_key = "pending/family-abc/runs/malformed.json"
    s3.put_object(Bucket=PENDING_BUCKET, Key=malformed_key, Body=b"not valid json")
    caplog.set_level("WARNING")

    records = store.list_runs()

    assert records == [record]
    assert any(
        message.startswith(f"Skipping malformed run record at {malformed_key}")
        for message in caplog.messages
    )


def test_operator_listing_reads_every_family_but_never_pages_staged_artifacts(
    s3: S3Client,
) -> None:
    """The all-families read lists each owner's runs/ folder (AI-465): staged
    artifacts share pending/ and must not be paged through to find records."""
    store = RunStore(_settings(), client=s3)
    request = StoryRequest(theme="the_sleepy_sea", language="it")
    runs = [new_run(token, request) for token in ("a" * 32, "b" * 32, "operator")]
    for run in runs:
        store.save(run)
    for i in range(5):
        s3.put_object(Bucket=PENDING_BUCKET, Key=f"pending/staged/story-x/p{i}.webp", Body=b"x")

    listed_prefixes: list[str] = []
    paginator = s3.get_paginator

    def spying_paginator(name: str):  # type: ignore[no-untyped-def]
        pager = paginator(name)
        paginate = pager.paginate

        def spy(**kwargs):  # type: ignore[no-untyped-def]
            listed_prefixes.append(kwargs["Prefix"])
            return paginate(**kwargs)

        pager.paginate = spy  # type: ignore[method-assign]
        return pager

    s3.get_paginator = spying_paginator  # type: ignore[method-assign]

    assert {r.id for r in store.list_runs()} == {r.id for r in runs}
    assert not any(p.startswith("pending/staged") for p in listed_prefixes)
    assert "pending/" not in listed_prefixes[1:]  # only the one delimiter listing


def test_premise_over_the_bound_is_rejected() -> None:
    """AI-470: the field bound, not just the form's maxlength, blocks a
    direct POST that skips the DOM entirely."""
    with pytest.raises(ValidationError):
        StoryRequest(theme="the_sleepy_sea", language="it", premise="x" * (PREMISE_MAX_LENGTH + 1))


def test_premise_at_the_bound_is_accepted() -> None:
    request = StoryRequest(theme="the_sleepy_sea", language="it", premise="x" * PREMISE_MAX_LENGTH)
    assert request.premise == "x" * PREMISE_MAX_LENGTH


def test_story_request_error_message_is_friendly_for_a_too_long_premise() -> None:
    try:
        StoryRequest(theme="the_sleepy_sea", language="it", premise="x" * 301)
    except ValidationError as error:
        message = story_request_error_message(error)
    else:
        pytest.fail("expected a ValidationError")
    assert "loc" not in message
    assert "300" in message


# ── Parent review (B2, AI-475): every staged story is seen before approve ─────


def _staged(story_ids: list[str]) -> records.RunRecord:
    return new_run("a" * 32, REQUEST).advance("running").advance("staged", story_ids=story_ids)


def test_a_staged_run_starts_unreviewed_and_marks_stories_one_at_a_time() -> None:
    record = _staged(["s1", "s2"])
    assert record.reviewed_story_ids == []
    assert not record.fully_reviewed
    record = record.mark_reviewed("s1")
    assert record.unreviewed_story_ids == ["s2"]
    assert not record.fully_reviewed
    record = record.mark_reviewed("s2").mark_reviewed("s2")
    assert record.reviewed_story_ids == ["s1", "s2"]
    assert record.fully_reviewed


def test_a_run_with_no_stories_is_never_fully_reviewed() -> None:
    assert not _staged([]).fully_reviewed


def test_restaging_a_run_clears_its_review() -> None:
    reviewed = _staged(["s1"]).mark_reviewed("s1")
    assert reviewed.advance("approved").reviewed_story_ids == ["s1"]
    rerun = reviewed.model_copy(update={"state": "running"})
    assert rerun.advance("staged", story_ids=["s9"]).reviewed_story_ids == []


def test_a_record_without_a_review_field_loads_unreviewed(s3: S3Client) -> None:
    store = RunStore(_settings(), client=s3)
    record = _staged(["s1"])
    legacy = json.loads(record.model_dump_json())
    legacy.pop("reviewed_story_ids", None)
    s3.put_object(
        Bucket=PENDING_BUCKET,
        Key=f"pending/{'a' * 32}/runs/{record.id}.json",
        Body=json.dumps(legacy).encode(),
    )
    loaded = store.load("a" * 32, record.id)
    assert loaded is not None
    assert loaded.reviewed_story_ids == []
    assert not loaded.fully_reviewed
