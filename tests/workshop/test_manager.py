"""Behavior specs for the workshop run manager (AI-387, ADR-005).

The manager wraps the pipeline's step functions: one in-process run at a time,
every state change persisted to the store before anything else happens, and
resume-on-boot re-entering whatever a restart interrupted. The generation seam
is injectable, so the whole lifecycle is exercised with zero network — the
same seam discipline as generate_story's provider arguments.
"""

import asyncio
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws
from mypy_boto3_s3 import S3Client

from src.config import Settings
from src.workshop.manager import RunManager
from src.workshop.records import PackRequest, RunRecord, RunStore, new_run

BUCKET = "cantastorie-published"

REQUEST = PackRequest(theme="the_sleepy_sea", language="it", count=1)


@pytest.fixture
def s3() -> Iterator[S3Client]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def _settings() -> Settings:
    return Settings(_env_file=None, r2_bucket=BUCKET)


def _staged_pack(request: PackRequest, settings: Settings) -> list[str]:
    """A stand-in generate seam: 'stages' one prefix per requested story."""
    staged = []
    for n in range(request.count):
        story_id = f"{request.theme}-{request.language}-{n}"
        staged.append(f"pending/staged/{story_id}")
    return staged


def test_submit_persists_a_queued_record(s3: S3Client) -> None:
    settings = _settings()
    store = RunStore(settings, client=s3)
    manager = RunManager(store, settings, generate_pack=lambda req, st: [])

    record = asyncio.run(manager.submit("family-abc", REQUEST))

    loaded = store.load("family-abc", record.id)
    assert loaded is not None
    assert loaded.state == "queued"


def test_execute_lands_staged_with_the_pack_story_ids(s3: S3Client) -> None:
    settings = _settings()
    store = RunStore(settings, client=s3)
    manager = RunManager(store, settings, generate_pack=_staged_pack)
    request = PackRequest(theme="the_sleepy_sea", language="it", count=2)

    async def run() -> None:
        record = await manager.submit("family-abc", request)
        await manager.execute(record)

    asyncio.run(run())

    [record] = store.list_runs(family_token="family-abc")
    assert record.state == "staged"
    assert record.story_ids == ["the_sleepy_sea-it-0", "the_sleepy_sea-it-1"]


def test_a_generation_error_lands_failed_with_the_reason(s3: S3Client) -> None:
    settings = _settings()
    store = RunStore(settings, client=s3)

    def explode(req: PackRequest, st: Settings) -> list[str]:
        raise RuntimeError("narration provider unreachable")

    manager = RunManager(store, settings, generate_pack=explode)

    async def run() -> None:
        record = await manager.submit("family-abc", REQUEST)
        await manager.execute(record)

    asyncio.run(run())

    [record] = store.list_runs(family_token="family-abc")
    assert record.state == "failed"
    assert record.error == "narration provider unreachable"


def test_the_running_state_is_persisted_before_generation_starts(s3: S3Client) -> None:
    settings = _settings()
    store = RunStore(settings, client=s3)
    seen: list[str] = []

    def observe(req: PackRequest, st: Settings) -> list[str]:
        [record] = store.list_runs(family_token="family-abc")
        seen.append(record.state)
        return []

    manager = RunManager(store, settings, generate_pack=observe)

    async def run() -> None:
        record = await manager.submit("family-abc", REQUEST)
        await manager.execute(record)

    asyncio.run(run())

    assert seen == ["running"]


def test_runs_execute_one_at_a_time(s3: S3Client) -> None:
    settings = _settings()
    store = RunStore(settings, client=s3)
    active = 0
    peak = 0
    guard = threading.Lock()

    def slow_generate(req: PackRequest, st: Settings) -> list[str]:
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with guard:
            active -= 1
        return []

    manager = RunManager(store, settings, generate_pack=slow_generate)

    async def run() -> None:
        first = await manager.submit("family-abc", REQUEST)
        second = await manager.submit("family-abc", REQUEST)
        await asyncio.gather(manager.execute(first), manager.execute(second))

    asyncio.run(run())

    assert peak == 1
    assert [r.state for r in store.list_runs()] == ["staged", "staged"]


def _aged(record: "RunRecord", age: timedelta) -> "RunRecord":
    """A copy stamped as though its last heartbeat was `age` in the past."""
    return record.model_copy(update={"updated_at": datetime.now(UTC) - age})


def test_reap_stale_fails_a_running_run_with_no_recent_heartbeat(s3: S3Client) -> None:
    settings = _settings()
    store = RunStore(settings, client=s3)
    # A running record whose backing process died: its heartbeat is hours old.
    zombie = _aged(new_run("family-abc", REQUEST).advance("running"), timedelta(hours=2))
    live = new_run("family-abc", REQUEST).advance("running")  # fresh — genuinely running
    store.save(zombie)
    store.save(live)
    manager = RunManager(store, settings, generate_pack=_staged_pack)

    reaped = manager.reap_stale()

    assert {r.id for r in reaped} == {zombie.id}
    reaped_zombie = store.load("family-abc", zombie.id)
    assert reaped_zombie is not None
    assert reaped_zombie.state == "failed"
    assert "interrupted" in (reaped_zombie.error or "")
    reloaded_live = store.load("family-abc", live.id)
    assert reloaded_live is not None
    assert reloaded_live.state == "running"  # a fresh run is never reaped


def test_reap_stale_retires_a_queued_run_that_never_started(s3: S3Client) -> None:
    settings = _settings()
    store = RunStore(settings, client=s3)
    zombie = _aged(new_run("family-abc", REQUEST), timedelta(hours=2))  # stuck in queued
    store.save(zombie)
    manager = RunManager(store, settings, generate_pack=_staged_pack)

    reaped = manager.reap_stale()

    assert [r.id for r in reaped] == [zombie.id]
    retired = store.load("family-abc", zombie.id)
    assert retired is not None
    assert retired.state == "failed"


def test_reap_stale_leaves_an_old_staged_run_awaiting_review(s3: S3Client) -> None:
    settings = _settings()
    store = RunStore(settings, client=s3)
    # A staged run has no process behind it either, but it is not a zombie —
    # it is waiting for the operator. Only live states (queued/running) are swept.
    settled = _aged(
        new_run("family-xyz", REQUEST).advance("running").advance("staged"), timedelta(hours=2)
    )
    store.save(settled)
    manager = RunManager(store, settings, generate_pack=_staged_pack)

    reaped = manager.reap_stale()

    assert reaped == []
    reloaded = store.load("family-xyz", settled.id)
    assert reloaded is not None
    assert reloaded.state == "staged"


def test_resume_on_boot_reenters_queued_and_running_runs_only(s3: S3Client) -> None:
    settings = _settings()
    store = RunStore(settings, client=s3)
    interrupted = new_run("family-abc", REQUEST).advance("running")
    never_started = new_run("family-abc", REQUEST)
    settled = new_run("family-xyz", REQUEST).advance("running").advance("staged")
    for record in (interrupted, never_started, settled):
        store.save(record)

    manager = RunManager(store, settings, generate_pack=_staged_pack)
    resumed = asyncio.run(manager.resume_on_boot())

    assert {r.id for r in resumed} == {interrupted.id, never_started.id}
    assert all(r.state == "staged" for r in resumed)
    reloaded = store.load("family-xyz", settled.id)
    assert reloaded is not None
    assert reloaded.updated_at == settled.updated_at
