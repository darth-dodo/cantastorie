"""Behavior specs for workshop run records (AI-387, ADR-004).

A run record is the durable trace of one pack request: queued → running →
staged → approved | rejected, with a retryable failed. Records persist to R2
under pending/{family-token}/runs/ because Render's disk is ephemeral — moto
serves all S3 traffic here, zero network.
"""

from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws
from mypy_boto3_s3 import S3Client

from src.config import Settings
from src.workshop import records
from src.workshop.records import (
    InvalidTransition,
    PackRequest,
    RunStore,
    new_run,
)

BUCKET = "cantastorie-published"

REQUEST = PackRequest(theme="the_sleepy_sea", language="it", count=1)


@pytest.fixture
def s3() -> Iterator[S3Client]:
    """A moto-backed S3 client with the bucket already created."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        r2_endpoint_url="http://localhost",
        r2_access_key_id="test",
        r2_secret_access_key="test",
        r2_bucket=BUCKET,
        r2_public_base="http://localhost",
    )


def test_records_live_in_the_private_pending_bucket_when_configured(s3: S3Client) -> None:
    """The published bucket is public by design (setup.md); pending content
    must never share it in production. R2_PENDING_BUCKET points the store at
    a private bucket; the public one stays untouched."""
    s3.create_bucket(Bucket="cantastorie-pending")
    settings = Settings(
        _env_file=None,
        r2_endpoint_url="http://localhost",
        r2_access_key_id="test",
        r2_secret_access_key="test",
        r2_bucket=BUCKET,
        r2_public_base="http://localhost",
        r2_pending_bucket="cantastorie-pending",
    )
    store = RunStore(settings, client=s3)
    record = new_run("family-abc", REQUEST)

    store.save(record)

    pending = s3.list_objects_v2(Bucket="cantastorie-pending")
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


def test_pack_request_count_is_capped_at_three() -> None:
    with pytest.raises(ValueError, match="count"):
        PackRequest(theme="the_sleepy_sea", language="it", count=4)


def test_store_round_trips_a_record_under_the_pending_prefix(s3: S3Client) -> None:
    store = RunStore(_settings(), client=s3)
    record = new_run("family-abc", REQUEST)

    store.save(record)
    loaded = store.load("family-abc", record.id)

    assert loaded == record
    keys = [obj["Key"] for obj in s3.list_objects_v2(Bucket=BUCKET)["Contents"]]
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
    contents = s3.list_objects_v2(Bucket=BUCKET)["Contents"]
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
    s3.put_object(Bucket=BUCKET, Key=malformed_key, Body=b"not valid json")
    caplog.set_level("WARNING")

    records = store.list_runs()

    assert records == [record]
    assert any(
        message.startswith(f"Skipping malformed run record at {malformed_key}")
        for message in caplog.messages
    )
