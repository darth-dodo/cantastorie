"""Behavior specs for the boot-time resume lifespan (B5, AI-477).

`resume_on_boot()` and `reap_stale()` existed but nothing ever called them
(docs/audits/release-readiness.md → B5): every deploy stranded a `running`
record for the full stale-after window. The fix wires both into a FastAPI
`lifespan` — scheduled as a background task, never awaited during startup,
so the health check (and Render's deploy gate) stays responsive even while a
resume is still generating.

These tests drive the real RunManager/RunStore against a moto bucket and a
real `TestClient(app)` used as a context manager, which is what actually
triggers ASGI lifespan startup/shutdown (a bare `TestClient(app)` — no `with`
— never sends the lifespan protocol at all, which is why none of the app's
other smoke tests exercise this path).
"""

import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws
from mypy_boto3_s3 import S3Client

from src.api.main import create_app
from src.api.routes.workshop import get_run_manager
from src.config import Settings, get_settings
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


def _aged(record: RunRecord, age: timedelta) -> RunRecord:
    return record.model_copy(update={"updated_at": datetime.now(UTC) - age})


def _blocking_manager(
    store: RunStore, settings: Settings, hold: threading.Event, calls: list[PackRequest]
) -> RunManager:
    """A manager whose generation seam blocks on `hold` — lets a test observe
    that startup returns while the resumed run is still in flight."""

    def generate_pack(request: PackRequest, st: Settings) -> list[str]:
        calls.append(request)
        hold.wait(timeout=5)
        story_id = f"{request.theme}-{request.language}-resumed"
        return [f"pending/staged/{story_id}"]

    return RunManager(store, settings, generate_pack=generate_pack)


def _wired_app(settings: Settings, manager: RunManager):
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_run_manager] = lambda: manager
    return app


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    pytest.fail("condition never became true")


def test_boot_schedules_exactly_one_resume_without_blocking_startup(s3: S3Client) -> None:
    settings = _settings()
    store = RunStore(settings, client=s3)
    running = new_run("family-abc", REQUEST).advance("running")
    store.save(running)

    hold = threading.Event()
    calls: list[PackRequest] = []
    manager = _blocking_manager(store, settings, hold, calls)
    app = _wired_app(settings, manager)

    with TestClient(app) as client:
        # Startup already returned (we are inside the `with` block) even
        # though the resumed run's generation is still blocked on `hold` —
        # this is the "health check stays responsive" requirement.
        assert client.get("/health").status_code == 200
        still_running = store.load("family-abc", running.id)
        assert still_running is not None
        assert still_running.state == "running"  # not finished yet

        hold.set()  # let the background resume finish
        _wait_until(lambda: store.load("family-abc", running.id).state != "running")

    assert calls == [REQUEST]  # scheduled exactly once
    resumed = store.load("family-abc", running.id)
    assert resumed is not None
    assert resumed.state == "staged"


def test_a_stale_record_is_reaped_at_boot(s3: S3Client) -> None:
    settings = _settings()
    store = RunStore(settings, client=s3)
    zombie = _aged(new_run("family-abc", REQUEST).advance("running"), timedelta(hours=2))
    store.save(zombie)

    hold = threading.Event()
    hold.set()  # nothing should ever call generate_pack for a reaped run
    calls: list[PackRequest] = []
    manager = _blocking_manager(store, settings, hold, calls)
    app = _wired_app(settings, manager)

    with TestClient(app) as client:
        client.get("/health")
        _wait_until(lambda: store.load("family-abc", zombie.id).state == "failed")

    reaped = store.load("family-abc", zombie.id)
    assert reaped is not None
    assert reaped.state == "failed"
    assert "interrupted" in (reaped.error or "")
    assert calls == []  # reaped before resume ever looked at it


def test_shutdown_cancels_the_boot_resume_task(s3: S3Client) -> None:
    settings = _settings()
    store = RunStore(settings, client=s3)
    running = new_run("family-abc", REQUEST).advance("running")
    store.save(running)

    hold = threading.Event()  # never set from the test; generate_pack blocks
    calls: list[PackRequest] = []
    manager = _blocking_manager(store, settings, hold, calls)
    app = _wired_app(settings, manager)

    with TestClient(app) as client:
        client.get("/health")
        _wait_until(lambda: len(calls) == 1)  # resume is in flight
        task = app.state.resume_task
        assert not task.done()
        # Exiting the `with` block here triggers shutdown while generate_pack
        # is still blocked on `hold` — the task must be cancelled, not waited
        # out to completion.

    # The lifespan's shutdown handler cancelled the task and awaited it
    # cleanly rather than leaving it dangling.
    assert task.done()
    assert task.cancelled()
    hold.set()  # release the orphaned generator thread (bounded by its own timeout anyway)


def test_no_r2_configuration_means_no_boot_task_at_all() -> None:
    """Without R2/pending-bucket configuration there is no RunStore to scan —
    the lifespan must not construct a manager or schedule anything (also
    keeps every other test's plain `TestClient(app)` — none of which use
    `with` — inert even if lifespan ever became eager)."""
    settings = Settings(_env_file=None)  # no r2_bucket, no r2_pending_bucket
    assert settings.pending_bucket == ""
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert getattr(app.state, "resume_task", None) is None
