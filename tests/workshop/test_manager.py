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
from src.workshop import manager as manager_module
from src.workshop.manager import OPERATOR_TOKEN, RunCapExceeded, RunManager
from src.workshop.records import RunRecord, RunStore, StoryRequest, new_run

BUCKET = "cantastorie-published"
PENDING_BUCKET = "cantastorie-pending"

REQUEST = StoryRequest(theme="the_sleepy_sea", language="it")


@pytest.fixture
def s3() -> Iterator[S3Client]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.create_bucket(Bucket=PENDING_BUCKET)
        yield client


def _settings() -> Settings:
    return Settings(_env_file=None, r2_bucket=BUCKET, r2_pending_bucket=PENDING_BUCKET)


def _staged_story(request: StoryRequest, settings: Settings, run_id: str) -> str:
    """A stand-in generate seam: 'stages' the one requested story."""
    return f"pending/staged/{request.theme}-{request.language}-0"


def test_submit_persists_a_queued_record(s3: S3Client) -> None:
    settings = _settings()
    store = RunStore(settings, client=s3)
    manager = RunManager(store, settings, generate=lambda req, st, run_id: "pending/staged/stub")

    record = asyncio.run(manager.submit("family-abc", REQUEST))

    loaded = store.load("family-abc", record.id)
    assert loaded is not None
    assert loaded.state == "queued"


def test_execute_generates_exactly_one_story_and_lands_staged_with_its_id(
    s3: S3Client,
) -> None:
    """AI-480: one run is one story — the seam is called once and its prefix
    becomes the record's story_id. H1: the seam gets the run's id, so the
    story stages under an id only this run derives."""
    settings = _settings()
    store = RunStore(settings, client=s3)
    calls: list[tuple[StoryRequest, str]] = []

    def generate(request: StoryRequest, st: Settings, run_id: str) -> str:
        calls.append((request, run_id))
        return f"pending/staged/{request.theme}-{request.language}-0"

    manager = RunManager(store, settings, generate=generate)

    async def run() -> None:
        record = await manager.submit("family-abc", REQUEST)
        await manager.execute(record)

    asyncio.run(run())

    [record] = store.list_runs(family_token="family-abc")
    assert calls == [(REQUEST, record.id)]
    assert record.state == "staged"
    assert record.story_id == "the_sleepy_sea-it-0"


def test_the_default_seam_runs_generate_story_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, object]] = []

    def fake_generate_story(theme: str, language: str, settings: Settings, **kwargs: object) -> str:
        calls.append((theme, language, kwargs.get("run_nonce")))
        return f"pending/staged/{theme}-{language}-x"

    monkeypatch.setattr(manager_module, "generate_story", fake_generate_story)

    prefix = manager_module._generate_staged_story(REQUEST, _settings(), "r" * 32)

    assert prefix == "pending/staged/the_sleepy_sea-it-x"
    assert calls == [("the_sleepy_sea", "it", "r" * 32)]


def test_a_generation_error_lands_failed_with_the_reason(s3: S3Client) -> None:
    settings = _settings()
    store = RunStore(settings, client=s3)

    def explode(req: StoryRequest, st: Settings, run_id: str) -> str:
        raise RuntimeError("narration provider unreachable")

    manager = RunManager(store, settings, generate=explode)

    async def run() -> None:
        record = await manager.submit("family-abc", REQUEST)
        await manager.execute(record)

    asyncio.run(run())

    [record] = store.list_runs(family_token="family-abc")
    assert record.state == "failed"
    assert record.error == "narration provider unreachable"


def test_a_generation_error_is_reported_to_sentry(
    s3: S3Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """execute() swallows the exception to land the run failed, so Sentry's
    request integration never sees it — the manager must report it explicitly."""
    captured: list[BaseException] = []
    monkeypatch.setattr(manager_module.sentry_sdk, "capture_exception", captured.append)
    settings = _settings()
    store = RunStore(settings, client=s3)
    boom = RuntimeError("narration provider unreachable")

    def explode(req: StoryRequest, st: Settings, run_id: str) -> str:
        raise boom

    manager = RunManager(store, settings, generate=explode)

    async def run() -> None:
        record = await manager.submit("family-abc", REQUEST)
        await manager.execute(record)

    asyncio.run(run())

    assert captured == [boom]


def test_the_running_state_is_persisted_before_generation_starts(s3: S3Client) -> None:
    settings = _settings()
    store = RunStore(settings, client=s3)
    seen: list[str] = []

    def observe(req: StoryRequest, st: Settings, run_id: str) -> str:
        [record] = store.list_runs(family_token="family-abc")
        seen.append(record.state)
        return "pending/staged/observed"

    manager = RunManager(store, settings, generate=observe)

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

    def slow_generate(req: StoryRequest, st: Settings, run_id: str) -> str:
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with guard:
            active -= 1
        return "pending/staged/slow"

    manager = RunManager(store, settings, generate=slow_generate)

    async def run() -> None:
        # The operator is cap-exempt, so it can hold two runs at once — exactly
        # what the execute-lock must serialize (AI-411 caps block two live
        # family runs, which is a submit rule orthogonal to this lock test).
        first = await manager.submit(OPERATOR_TOKEN, REQUEST)
        second = await manager.submit(OPERATOR_TOKEN, REQUEST)
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
    manager = RunManager(store, settings, generate=_staged_story)

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
    manager = RunManager(store, settings, generate=_staged_story)

    reaped = manager.reap_stale()

    assert [r.id for r in reaped] == [zombie.id]
    retired = store.load("family-abc", zombie.id)
    assert retired is not None
    assert retired.state == "failed"


def _counting_store(store: RunStore) -> tuple[RunStore, "list[int]"]:
    """Wrap store.list_runs to count how many full sweeps a call triggers.

    Each list_runs() is one LIST + a GET per record on R2, so sweep count is the
    hot-path cost we care about. Returns the store and a one-element counter.
    """
    calls = [0]
    original = store.list_runs

    def counted(*args: object, **kwargs: object) -> list[RunRecord]:
        calls[0] += 1
        return original(*args, **kwargs)  # type: ignore[arg-type]

    store.list_runs = counted  # type: ignore[method-assign]
    return store, calls


def test_reap_stale_sweeps_the_store_only_once_per_call(s3: S3Client) -> None:
    """Reap used to call list_runs twice (queued + running), each a full R2
    sweep. It only needs one sweep, filtering states in memory."""
    settings = _settings()
    store, calls = _counting_store(RunStore(settings, client=s3))
    store.save(_aged(new_run("family-abc", REQUEST).advance("running"), timedelta(hours=2)))
    manager = RunManager(store, settings, generate=_staged_story)

    manager.reap_stale()

    assert calls[0] == 1


def test_reap_stale_is_throttled_within_the_min_interval(s3: S3Client) -> None:
    """Back-to-back reaps (the 2s poll) must not each sweep the store; only the
    first within reap_min_interval_seconds does any work."""
    settings = _settings()
    store, calls = _counting_store(RunStore(settings, client=s3))
    store.save(new_run("family-abc", REQUEST).advance("running"))  # fresh, not stale
    manager = RunManager(store, settings, generate=_staged_story)

    manager.reap_stale()
    manager.reap_stale()
    manager.reap_stale()

    assert calls[0] == 1  # only the first reap swept the store


def test_reap_stale_sweeps_again_after_the_min_interval_elapses(s3: S3Client) -> None:
    """Once the throttle window passes, the next reap sweeps again."""
    settings = Settings(
        _env_file=None,
        r2_bucket=BUCKET,
        r2_pending_bucket=PENDING_BUCKET,
        reap_min_interval_seconds=0,
    )
    store, calls = _counting_store(RunStore(settings, client=s3))
    store.save(new_run("family-abc", REQUEST).advance("running"))
    manager = RunManager(store, settings, generate=_staged_story)

    manager.reap_stale()
    manager.reap_stale()

    assert calls[0] == 2  # interval 0 means every call sweeps


def test_reap_stale_leaves_an_old_staged_run_awaiting_review(s3: S3Client) -> None:
    settings = _settings()
    store = RunStore(settings, client=s3)
    # A staged run has no process behind it either, but it is not a zombie —
    # it is waiting for the operator. Only live states (queued/running) are swept.
    settled = _aged(
        new_run("family-xyz", REQUEST).advance("running").advance("staged"), timedelta(hours=2)
    )
    store.save(settled)
    manager = RunManager(store, settings, generate=_staged_story)

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

    manager = RunManager(store, settings, generate=_staged_story)
    resumed = asyncio.run(manager.resume_on_boot())

    assert {r.id for r in resumed} == {interrupted.id, never_started.id}
    assert all(r.state == "staged" for r in resumed)
    reloaded = store.load("family-xyz", settled.id)
    assert reloaded is not None
    assert reloaded.updated_at == settled.updated_at


# ---------------------------------------------------------------------------
# Per-family run caps (AI-411)
# ---------------------------------------------------------------------------


def test_second_active_run_is_rejected(s3: S3Client) -> None:
    settings = _settings()
    store = RunStore(settings, client=s3)
    manager = RunManager(store, settings, generate=lambda req, st, run_id: "pending/staged/stub")

    first = asyncio.run(manager.submit("a" * 32, REQUEST))
    assert first.state == "queued"
    with pytest.raises(RunCapExceeded) as excinfo:
        asyncio.run(manager.submit("a" * 32, REQUEST))
    assert excinfo.value.active is not None
    assert excinfo.value.active.id == first.id
    assert str(excinfo.value) == "a story is already being made for this family"


def test_daily_cap_rejects_fourth_submit(s3: S3Client) -> None:
    settings = _settings()  # default parent_daily_run_cap = 3
    store = RunStore(settings, client=s3)
    manager = RunManager(store, settings, generate=lambda req, st, run_id: "pending/staged/stub")
    token = "b" * 32
    for _ in range(3):
        record = asyncio.run(manager.submit(token, REQUEST))
        # settle it so the active-run rule doesn't fire first
        store.save(record.advance("running").advance("failed", error="x"))
    with pytest.raises(RunCapExceeded) as excinfo:
        asyncio.run(manager.submit(token, REQUEST))
    assert excinfo.value.active is None  # daily cap, not active-run
    assert str(excinfo.value) == "that's all the stories for today — tomorrow brings more"


def test_operator_is_exempt_from_caps(s3: S3Client) -> None:
    settings = _settings()
    store = RunStore(settings, client=s3)
    manager = RunManager(store, settings, generate=lambda req, st, run_id: "pending/staged/stub")
    for _ in range(5):
        asyncio.run(manager.submit(OPERATOR_TOKEN, REQUEST))
    # five concurrent queued operator runs, no exception


def test_other_family_runs_do_not_count(s3: S3Client) -> None:
    settings = _settings()
    store = RunStore(settings, client=s3)
    manager = RunManager(store, settings, generate=lambda req, st, run_id: "pending/staged/stub")
    asyncio.run(manager.submit("a" * 32, REQUEST))
    record = asyncio.run(manager.submit("c" * 32, REQUEST))
    assert record.state == "queued"


# ── Global daily run cap (H4) ───────────────────────────────────────────────


def _capped(s3: S3Client, cap: int) -> tuple[RunStore, RunManager]:
    settings = _settings().model_copy(update={"global_daily_run_cap": cap})
    store = RunStore(settings, client=s3)
    return store, RunManager(store, settings, generate=lambda req, st, run_id: "stub")


def test_the_global_cap_refuses_a_family_once_the_day_is_spent(s3: S3Client) -> None:
    """H4: per-family caps multiply with sign-ups, so the whole service gets a
    daily ceiling too. Each family here is under its own cap; the third run
    across all of them is refused, and nothing is recorded for it."""
    store, manager = _capped(s3, cap=2)
    asyncio.run(manager.submit("a" * 32, REQUEST))
    asyncio.run(manager.submit("b" * 32, REQUEST))

    with pytest.raises(RunCapExceeded) as excinfo:
        asyncio.run(manager.submit("c" * 32, REQUEST))

    assert excinfo.value.active is None
    assert str(excinfo.value) == "the story workshop is resting for today — tomorrow brings more"
    assert store.list_runs(family_token="c" * 32) == []


def test_operator_runs_count_toward_the_global_cap_but_are_never_refused(
    s3: S3Client,
) -> None:
    """H4: operator runs spend money too, so they count; the operator is
    still never refused, and their runs can use up the day for families."""
    store, manager = _capped(s3, cap=2)
    for _ in range(3):
        asyncio.run(manager.submit(OPERATOR_TOKEN, REQUEST))

    assert store.daily_run_count(datetime.now(UTC).date()) == 3
    with pytest.raises(RunCapExceeded):
        asyncio.run(manager.submit("a" * 32, REQUEST))


def test_a_family_refused_by_its_own_cap_does_not_use_up_the_global_day(
    s3: S3Client,
) -> None:
    """A submit refused by the per-family cap never reached generation, so it
    must not count toward the service's daily total."""
    store, manager = _capped(s3, cap=10)
    asyncio.run(manager.submit("a" * 32, REQUEST))
    with pytest.raises(RunCapExceeded):
        asyncio.run(manager.submit("a" * 32, REQUEST))  # one active run per family

    assert store.daily_run_count(datetime.now(UTC).date()) == 1


def test_global_cap_reports_up_front(s3: S3Client) -> None:
    """The make screen asks before the form is filled in, without counting a run."""
    store, manager = _capped(s3, cap=1)
    assert manager.global_cap() is None
    asyncio.run(manager.submit("a" * 32, REQUEST))

    cap = manager.global_cap()

    assert cap is not None
    assert store.daily_run_count(datetime.now(UTC).date()) == 1


def test_the_global_cap_alerts_once_near_it_and_once_at_it(
    s3: S3Client, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """H4: the operator hears about it before the day runs out (80%) and when
    it does, once each — not on every submit — with no family token in it."""
    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        manager_module.sentry_sdk,
        "capture_message",
        lambda message, level=None: alerts.append((message, level)),
    )
    _, manager = _capped(s3, cap=5)
    caplog.set_level("INFO", logger="src.workshop.manager")

    for token in ["a" * 32, "b" * 32, "c" * 32, "d" * 32, "e" * 32]:
        asyncio.run(manager.submit(token, REQUEST))
    with pytest.raises(RunCapExceeded):
        asyncio.run(manager.submit("f" * 32, REQUEST))

    assert alerts == [
        ("Global daily run cap nearly reached: 4 of 5", "warning"),
        ("Global daily run cap reached: 5 of 5", "warning"),
    ]
    events = [getattr(r, "event", None) for r in caplog.records]
    assert events.count("global_run_cap_near") == 1
    assert events.count("global_run_cap_reached") == 1
    assert events.count("global_run_cap_rejected") == 1
    for record in caplog.records:
        assert "a" * 32 not in str(vars(record))
