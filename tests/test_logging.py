"""Behavior specs for structured application logging (B6, AI-485).

Startup configures one stdout handler with key=value lines; the workshop and
the pipeline emit one record per lifecycle event, each carrying its fields as
LogRecord attributes (so these assert on fields, not message text). A family
token never appears raw — only its salted short hash does.
"""

import asyncio
import io
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws
from mypy_boto3_s3 import S3Client
from pydantic import SecretStr
from pydantic_ai.models.test import TestModel

import src.api.main as main_module
from src import observability
from src.config import Settings
from src.observability import (
    LOG_HANDLER_NAME,
    KeyValueFormatter,
    configure_logging,
    family_hash,
    init_error_monitoring,
)
from src.pipeline.generate import generate_story
from src.pipeline.models import IMAGE_SAFETY_CRITERIA
from src.pipeline.publish import publish_story, unpublish_story
from src.pipeline.steps.image_safety import IMAGE_SAFETY_MAX_REGENERATIONS
from src.workshop.manager import RunCapExceeded, RunManager
from src.workshop.records import PackRequest, RunStore
from tests.pipeline.test_generate import (
    _GOOD_DRAFT,
    _PASSING_REPORT,
    _calm_judge,
    _fake_images,
    _fake_narration,
)

SENTINEL = "PURPLE-OTTER-SENTINEL"

BUCKET = "cantastorie-published"
PENDING_BUCKET = "cantastorie-pending"
PUBLIC_BASE = "https://cdn.example.test/published"
FAMILY = "0123456789abcdef0123456789abcdef"
REQUEST = PackRequest(theme="the_sleepy_sea", language="it", count=1)
PIPELINE_STEPS = {"write", "safety", "narrate", "illustrate", "image_safety", "assemble", "stage"}


@pytest.fixture
def s3() -> Iterator[S3Client]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.create_bucket(Bucket=PENDING_BUCKET)
        yield client


@pytest.fixture
def info_logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger="src")
    return caplog


@pytest.fixture
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    src = logging.getLogger("src")
    saved = (list(root.handlers), root.level, src.level)
    yield
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])
    src.setLevel(saved[2])


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-test"),
        content_dir=tmp_path / "content",
        staging_dir=tmp_path / "staging",
        r2_bucket=BUCKET,
        r2_pending_bucket=PENDING_BUCKET,
        r2_public_base=PUBLIC_BASE,
    )


def _events(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "event", None) == event]


def _pack_with(
    *, safety_report: dict[str, Any] = _PASSING_REPORT, image_judge: TestModel | None = None
) -> Callable[[PackRequest, Settings], list[str]]:
    def pack(request: PackRequest, settings: Settings) -> list[str]:
        return [
            generate_story(
                request.theme,
                request.language,
                settings,
                write_model=TestModel(custom_output_args=_GOOD_DRAFT),
                safety_model=TestModel(custom_output_args=safety_report),
                revise_model=TestModel(custom_output_args=_GOOD_DRAFT),
                narration_client=_fake_narration(),
                image_transport=_fake_images(),
                image_safety_model=image_judge or _calm_judge(),
            )
        ]

    return pack


_real_pack = _pack_with()


def _scan_for(phrase: str, caplog: pytest.LogCaptureFixture) -> None:
    """Assert ``phrase`` reaches no captured record: no attribute, no formatted line."""
    assert caplog.records
    formatter = KeyValueFormatter()
    for record_ in caplog.records:
        assert phrase not in formatter.format(record_)
        for value in vars(record_).values():
            assert phrase not in str(value)


# --- family_hash ---------------------------------------------------------------


def test_family_hash_is_a_short_stable_digest_that_hides_the_token() -> None:
    digest = family_hash(FAMILY)

    assert digest == family_hash(FAMILY)
    assert len(digest) == 12
    assert all(c in "0123456789abcdef" for c in digest)
    assert digest not in FAMILY
    assert digest != family_hash("f" * 32)


# --- configure_logging -----------------------------------------------------------


@pytest.mark.usefixtures("restore_logging")
def test_configure_logging_is_idempotent_and_leaves_uvicorn_alone() -> None:
    uvicorn_handlers = list(logging.getLogger("uvicorn").handlers)

    configure_logging(Settings(_env_file=None, log_level="DEBUG"))
    configure_logging(Settings(_env_file=None, log_level="DEBUG"))

    ours = [h for h in logging.getLogger().handlers if h.get_name() == LOG_HANDLER_NAME]
    assert len(ours) == 1
    assert logging.getLogger("src").level == logging.DEBUG
    assert logging.getLogger("uvicorn").handlers == uvicorn_handlers


@pytest.mark.usefixtures("restore_logging")
def test_log_level_defaults_to_info() -> None:
    configure_logging(Settings(_env_file=None))

    assert logging.getLogger("src").level == logging.INFO


def test_formatter_emits_key_value_fields_and_quotes_spaces() -> None:
    record = logging.LogRecord("src.x", logging.INFO, __file__, 1, "run_failed", None, None)
    record.event = "run_failed"
    record.run_id = "abc"
    record.error = "boom goes it"

    line = KeyValueFormatter().format(record)

    assert "level=INFO" in line
    assert "event=run_failed" in line
    assert "run_id=abc" in line
    assert 'error="boom goes it"' in line


@pytest.mark.usefixtures("restore_logging")
def test_configured_handler_writes_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(Settings(_env_file=None))
    logging.getLogger("src.test").info("hello", extra={"event": "hello", "run_id": "r1"})

    out = capsys.readouterr().out
    assert "event=hello" in out
    assert "run_id=r1" in out


# --- Sentry does not double-report ------------------------------------------------


def test_sentry_logging_integration_records_breadcrumbs_but_no_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(observability.sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))

    init_error_monitoring(Settings(_env_file=None, sentry_dsn="https://key@o1.ingest.sentry.io/1"))

    [kwargs] = calls
    [integration] = [i for i in kwargs["integrations"] if type(i).__name__ == "LoggingIntegration"]
    assert integration._handler is None  # event_level=None: logger.exception is no event
    assert integration._breadcrumb_handler is not None


# --- run lifecycle ---------------------------------------------------------------


def test_run_failure_logs_a_traceback_with_the_run_id(
    s3: S3Client, tmp_path: Path, info_logs: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path)

    def explode(request: PackRequest, st: Settings) -> list[str]:
        raise RuntimeError("narrate exploded")

    manager = RunManager(RunStore(settings, client=s3), settings, generate_pack=explode)
    record = asyncio.run(manager.submit(FAMILY, REQUEST))
    asyncio.run(manager.execute(record))

    [failed] = _events(info_logs, "run_failed")
    assert failed.levelno == logging.ERROR
    assert failed.run_id == record.id  # type: ignore[attr-defined]
    assert failed.exc_info is not None
    assert failed.exc_info[0] is RuntimeError
    [submitted] = _events(info_logs, "run_submitted")
    assert submitted.run_id == record.id  # type: ignore[attr-defined]
    [started] = _events(info_logs, "run_started")
    assert started.run_id == record.id  # type: ignore[attr-defined]


def test_each_pipeline_step_logs_its_duration_under_the_run_id(
    s3: S3Client, tmp_path: Path, info_logs: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path)
    manager = RunManager(RunStore(settings, client=s3), settings, generate_pack=_real_pack)
    record = asyncio.run(manager.submit(FAMILY, REQUEST))
    asyncio.run(manager.execute(record))

    steps = _events(info_logs, "step_finished")
    assert {r.step for r in steps} >= PIPELINE_STEPS  # type: ignore[attr-defined]
    for step in steps:
        assert isinstance(step.duration_ms, int)  # type: ignore[attr-defined]
        assert step.duration_ms >= 0  # type: ignore[attr-defined]
        assert step.run_id == record.id  # type: ignore[attr-defined]
    [staged] = _events(info_logs, "run_staged")
    assert isinstance(staged.duration_ms, int)  # type: ignore[attr-defined]


def test_cap_rejection_logs_the_reason(
    s3: S3Client, tmp_path: Path, info_logs: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path)
    manager = RunManager(RunStore(settings, client=s3), settings, generate_pack=lambda r, s: [])
    asyncio.run(manager.submit(FAMILY, REQUEST))

    with pytest.raises(RunCapExceeded):
        asyncio.run(manager.submit(FAMILY, REQUEST))

    [rejected] = _events(info_logs, "run_cap_rejected")
    assert rejected.reason == "active_run"  # type: ignore[attr-defined]
    assert rejected.family == family_hash(FAMILY)  # type: ignore[attr-defined]


def test_daily_cap_rejection_logs_the_daily_reason(
    s3: S3Client, tmp_path: Path, info_logs: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path).model_copy(update={"parent_daily_run_cap": 0})
    manager = RunManager(RunStore(settings, client=s3), settings, generate_pack=lambda r, s: [])

    with pytest.raises(RunCapExceeded):
        asyncio.run(manager.submit(FAMILY, REQUEST))

    [rejected] = _events(info_logs, "run_cap_rejected")
    assert rejected.reason == "daily_cap"  # type: ignore[attr-defined]


def test_reaped_runs_and_boot_resume_are_logged(
    s3: S3Client, tmp_path: Path, info_logs: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path).model_copy(update={"run_stale_after_seconds": -1})
    manager = RunManager(RunStore(settings, client=s3), settings, generate_pack=lambda r, s: [])
    record = asyncio.run(manager.submit(FAMILY, REQUEST))

    asyncio.run(main_module._reap_and_resume(manager))

    [reaped] = _events(info_logs, "run_reaped")
    assert reaped.run_id == record.id  # type: ignore[attr-defined]
    assert reaped.family == family_hash(FAMILY)  # type: ignore[attr-defined]
    [sweep] = _events(info_logs, "boot_reap")
    assert sweep.reaped == 1  # type: ignore[attr-defined]
    [resume] = _events(info_logs, "boot_resume")
    assert resume.resumed == 0  # type: ignore[attr-defined]


# --- publish / unpublish ----------------------------------------------------------


def test_publish_and_unpublish_log_story_id_and_lane(
    s3: S3Client, tmp_path: Path, info_logs: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path)
    story_id = _real_pack(REQUEST, settings)[0].rsplit("/", 1)[-1]

    publish_story(story_id, settings, client=s3, family_token=FAMILY)
    unpublish_story(story_id, settings, client=s3, family_token=FAMILY)
    publish_story(story_id, settings, client=s3)

    family_pub, shared_pub = _events(info_logs, "story_published")
    assert family_pub.story_id == story_id  # type: ignore[attr-defined]
    assert family_pub.lane == "family"  # type: ignore[attr-defined]
    assert family_pub.family == family_hash(FAMILY)  # type: ignore[attr-defined]
    assert shared_pub.lane == "shared"  # type: ignore[attr-defined]
    [unpub] = _events(info_logs, "story_unpublished")
    assert unpub.story_id == story_id  # type: ignore[attr-defined]
    assert unpub.lane == "family"  # type: ignore[attr-defined]


# --- privacy ---------------------------------------------------------------------


def test_a_full_run_never_logs_the_raw_family_token(
    s3: S3Client, tmp_path: Path, info_logs: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path)
    manager = RunManager(RunStore(settings, client=s3), settings, generate_pack=_real_pack)
    record = asyncio.run(manager.submit(FAMILY, REQUEST))
    done = asyncio.run(manager.execute(record))
    publish_story(done.story_ids[0], settings, client=s3, family_token=FAMILY)
    unpublish_story(done.story_ids[0], settings, client=s3, family_token=FAMILY)

    assert info_logs.records
    formatter = KeyValueFormatter()
    stream = io.StringIO()
    for record_ in info_logs.records:
        stream.write(formatter.format(record_) + "\n")
        for value in vars(record_).values():
            assert FAMILY not in str(value)
    assert FAMILY not in stream.getvalue()
    assert family_hash(FAMILY) in stream.getvalue()


# --- safety rejections: criterion names only, never the judge's free text -------


def _failing_image_judge() -> TestModel:
    return TestModel(
        custom_output_args={
            "verdicts": [
                {"criterion": c, "passed": c != "no_text", "reason": f"sign reads {SENTINEL}"}
                for c in IMAGE_SAFETY_CRITERIA
            ]
        }
    )


def _failing_text_report() -> dict[str, Any]:
    return {
        "verdicts": [
            {
                "rule": v["rule"],
                "passed": v["rule"] != "no_brands",
                "reason": f"mentions {SENTINEL}",
            }
            for v in _PASSING_REPORT["verdicts"]
        ]
    }


def test_image_redraws_and_rejection_log_slot_attempt_and_criteria_only(
    s3: S3Client, tmp_path: Path, info_logs: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path)
    pack = _pack_with(image_judge=_failing_image_judge())
    manager = RunManager(RunStore(settings, client=s3), settings, generate_pack=pack)
    record = asyncio.run(manager.submit(FAMILY, REQUEST))
    asyncio.run(manager.execute(record))

    redraws = _events(info_logs, "image_redraw")
    assert {r.attempt for r in redraws} == set(range(1, IMAGE_SAFETY_MAX_REGENERATIONS + 1))  # type: ignore[attr-defined]
    assert all(r.criteria == "no_text" and r.slot for r in redraws)  # type: ignore[attr-defined]
    steps = _events(info_logs, "step_finished")
    assert all(r.run_id == record.id for r in steps)  # type: ignore[attr-defined]
    assert any(r.step == "image_safety" for r in steps)  # type: ignore[attr-defined]
    [rejected] = _events(info_logs, "image_safety_rejected")
    assert rejected.criteria == "no_text"  # type: ignore[attr-defined]
    assert rejected.failed_slots >= 1  # type: ignore[attr-defined]
    [failed] = _events(info_logs, "run_failed")
    assert failed.run_id == record.id  # type: ignore[attr-defined]
    assert failed.error_type == "ImageSafetyRejectedError"  # type: ignore[attr-defined]
    assert failed.outcome == "safety_rejected"  # type: ignore[attr-defined]
    assert failed.criteria == "no_text"  # type: ignore[attr-defined]
    assert failed.exc_info is None
    _scan_for(SENTINEL, info_logs)


def test_text_gate_rejection_logs_criteria_without_the_judge_reason(
    s3: S3Client, tmp_path: Path, info_logs: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path)
    pack = _pack_with(safety_report=_failing_text_report())
    manager = RunManager(RunStore(settings, client=s3), settings, generate_pack=pack)
    record = asyncio.run(manager.submit(FAMILY, REQUEST))
    done = asyncio.run(manager.execute(record))

    assert done.state == "failed"
    [rejected] = _events(info_logs, "story_safety_rejected")
    assert rejected.criteria == "safety/no_brands"  # type: ignore[attr-defined]
    [failed] = _events(info_logs, "run_failed")
    assert failed.error_type == "StoryRejectedError"  # type: ignore[attr-defined]
    assert failed.criteria == "safety/no_brands"  # type: ignore[attr-defined]
    assert failed.failure_count == 1  # type: ignore[attr-defined]
    assert failed.exc_info is None
    _scan_for(SENTINEL, info_logs)
