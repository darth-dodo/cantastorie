"""Behavior specs for structured application logging (B6, AI-485).

Startup configures one stdout handler with key=value lines; the workshop and
the pipeline emit one record per lifecycle event, each carrying its fields as
LogRecord attributes (so these assert on fields, not message text). A family
token never appears raw — only its salted short hash does.
"""

import asyncio
import io
import logging
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from moto import mock_aws
from mypy_boto3_s3 import S3Client
from pydantic import BaseModel, SecretStr, ValidationError
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models.test import TestModel
from sentry_sdk.transport import Transport
from typer.testing import CliRunner

import src.api.main as main_module
from src import observability
from src.api.main import create_app
from src.api.routes import published
from src.config import Settings, get_settings
from src.observability import (
    LOG_HANDLER_NAME,
    KeyValueFormatter,
    RunIdFilter,
    configure_logging,
    family_hash,
    init_error_monitoring,
    run_context,
)
from src.pipeline import cli
from src.pipeline.content_rules import ContentViolation
from src.pipeline.generate import generate_story
from src.pipeline.models import IMAGE_SAFETY_CRITERIA
from src.pipeline.publish import AuditResult, publish_story, unpublish_story
from src.pipeline.steps.assemble import ContentRulesViolation
from src.pipeline.steps.image_safety import IMAGE_SAFETY_MAX_REGENERATIONS
from src.workshop.manager import RunCapExceeded, RunManager
from src.workshop.records import RunStore, StoryRequest
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
REQUEST = StoryRequest(theme="the_sleepy_sea", language="it")
PIPELINE_STEPS = {"write", "safety", "narrate", "illustrate", "image_safety", "assemble", "stage"}


@pytest.fixture
def s3() -> Iterator[S3Client]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.create_bucket(Bucket=PENDING_BUCKET)
        yield client


@pytest.fixture
def info_logs(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    # The stdout handler stamps run_id from the run context; give caplog's
    # handler the same filter so records here look as they do on stdout.
    caplog.set_level(logging.INFO, logger="src")
    run_filter = RunIdFilter()
    caplog.handler.addFilter(run_filter)
    yield caplog
    caplog.handler.removeFilter(run_filter)


@pytest.fixture
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    src = logging.getLogger("src")
    access = logging.getLogger("uvicorn.access")
    saved = (list(root.handlers), root.level, src.level, list(access.filters))
    yield
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])
    src.setLevel(saved[2])
    access.filters[:] = saved[3]


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


def _generate_with(
    *, safety_report: dict[str, Any] = _PASSING_REPORT, image_judge: TestModel | None = None
) -> Callable[[StoryRequest, Settings, str], str]:
    def generate(request: StoryRequest, settings: Settings, run_id: str) -> str:
        return generate_story(
            request.theme,
            request.language,
            settings,
            write_model=TestModel(custom_output_args=_GOOD_DRAFT),
            safety_model=TestModel(custom_output_args=safety_report),
            revise_model=TestModel(custom_output_args=_GOOD_DRAFT),
            narration_client=_fake_narration(),
            image_transport=_fake_images(),
            image_safety_model=image_judge or _calm_judge(),
            run_nonce=run_id,
        )

    return generate


_real_generate = _generate_with()


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


@pytest.mark.usefixtures("restore_logging")
def test_every_cli_command_configures_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    root = logging.getLogger()
    root.handlers[:] = [h for h in root.handlers if h.get_name() != LOG_HANDLER_NAME]
    monkeypatch.setattr(
        cli, "audit_published_bucket", lambda s: AuditResult(violations=[], manifests_checked=0)
    )

    result = CliRunner().invoke(cli.app, ["audit"])

    assert result.exit_code == 0
    assert [h for h in root.handlers if h.get_name() == LOG_HANDLER_NAME]


def test_quote_escapes_control_characters() -> None:
    record = logging.LogRecord("src.x", logging.INFO, __file__, 1, "e", None, None)
    record.event = "e"
    record.detail = "a\rb\tc\nd"

    line = KeyValueFormatter().format(record)

    assert "\r" not in line
    assert "\t" not in line
    assert "\n" not in line
    assert 'detail="a\\rb\\tc\\nd"' in line


@pytest.mark.usefixtures("restore_logging")
def test_the_stdout_handler_stamps_run_id_from_the_run_context(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(Settings(_env_file=None))
    with run_context("ctx-run"):
        logging.getLogger("src.test").info("inner", extra={"event": "inner"})

    assert "run_id=ctx-run" in capsys.readouterr().out


# --- tracebacks never carry exception messages ----------------------------------


def test_formatted_tracebacks_name_exception_types_but_drop_their_messages() -> None:
    class Draft(BaseModel):
        pages: list[str]

    try:
        try:
            Draft.model_validate({"pages": SENTINEL})
        except ValidationError as invalid:
            raise UnexpectedModelBehavior(f"bad output {SENTINEL}") from invalid
    except UnexpectedModelBehavior:
        record = logging.LogRecord(
            "src.x", logging.ERROR, __file__, 1, "run_failed", None, sys.exc_info()
        )
    record.event = "run_failed"

    line = KeyValueFormatter().format(record)

    assert SENTINEL not in line
    assert "UnexpectedModelBehavior" in line
    assert "ValidationError" in line
    assert "Traceback (most recent call last)" in line


# --- uvicorn access log and the published proxy carry no raw token --------------


@pytest.mark.usefixtures("restore_logging")
def test_access_log_lines_have_family_tokens_replaced_by_their_hash(
    caplog: pytest.LogCaptureFixture,
) -> None:
    configure_logging(Settings(_env_file=None))
    configure_logging(Settings(_env_file=None))
    access = logging.getLogger("uvicorn.access")
    assert len([f for f in access.filters if isinstance(f, observability.AccessLogRedactor)]) == 1
    caplog.set_level(logging.INFO, logger="uvicorn.access")

    for path in (
        f"/published/families/{FAMILY}/it/manifest.json",
        f"/workshop/library?family_token={FAMILY}&x=1",
    ):
        access.info('%s - "%s %s HTTP/%s" %d', "1.2.3.4:5", "GET", path, "1.1", 200)

    messages = [r.getMessage() for r in caplog.records if r.name == "uvicorn.access"]
    assert len(messages) == 2
    for message in messages:
        assert FAMILY not in message
        assert family_hash(FAMILY) in message


def test_the_published_proxy_warning_carries_no_raw_token(
    monkeypatch: pytest.MonkeyPatch, info_logs: pytest.LogCaptureFixture
) -> None:
    error = ClientError({"Error": {"Code": "AccessDenied", "Message": "nope"}}, "GetObject")

    class _Denied:
        def get_object(self, **_: object) -> None:
            raise error

    monkeypatch.setattr(published, "_build_client", lambda _settings: _Denied())
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, r2_bucket=BUCKET)

    response = TestClient(app).get(f"/published/families/{FAMILY}/it/manifest.json")

    assert response.status_code == 404
    [warning] = _events(info_logs, "published_proxy_error")
    assert warning.family == family_hash(FAMILY)  # type: ignore[attr-defined]
    assert warning.path == "families/<family>/it/manifest.json"  # type: ignore[attr-defined]
    _scan_for(FAMILY, info_logs)


# --- Sentry does not double-report ------------------------------------------------


class _EnvelopeSink(Transport):
    def __init__(self, options: Any = None) -> None:
        super().__init__(options)
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Any) -> None:
        for item in envelope.items:
            if item.type == "event":
                self.events.append(item.payload.json)


@pytest.fixture
def sentry_sink(monkeypatch: pytest.MonkeyPatch) -> Iterator[_EnvelopeSink]:
    sink = _EnvelopeSink()
    real_init = observability.sentry_sdk.init
    monkeypatch.setattr(
        observability.sentry_sdk, "init", lambda **kwargs: real_init(transport=sink, **kwargs)
    )
    yield sink
    real_init()  # back to a disabled client


def test_logged_errors_become_breadcrumbs_not_sentry_events(sentry_sink: _EnvelopeSink) -> None:
    init_error_monitoring(Settings(_env_file=None, sentry_dsn="https://key@o1.ingest.sentry.io/1"))
    log = logging.getLogger("src.test")

    log.info("crumb", extra={"event": "crumb"})
    try:
        raise RuntimeError("boom")
    except RuntimeError as error:
        log.exception("run_failed", extra={"event": "run_failed"})
        logging.getLogger("botocore").error("third-party error")
        observability.sentry_sdk.capture_exception(error)
    observability.sentry_sdk.flush()

    [event] = sentry_sink.events
    assert event["exception"]["values"][-1]["type"] == "RuntimeError"
    crumbs = [c.get("message") for c in event.get("breadcrumbs", {}).get("values", [])]
    assert "crumb" in crumbs


# --- run lifecycle ---------------------------------------------------------------


def test_run_failure_logs_a_traceback_with_the_run_id(
    s3: S3Client, tmp_path: Path, info_logs: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path)

    def explode(request: StoryRequest, st: Settings, run_id: str) -> str:
        raise RuntimeError("narrate exploded")

    manager = RunManager(RunStore(settings, client=s3), settings, generate=explode)
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
    manager = RunManager(RunStore(settings, client=s3), settings, generate=_real_generate)
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
    manager = RunManager(
        RunStore(settings, client=s3), settings, generate=lambda r, s, run_id: "pending/staged/x"
    )
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
    manager = RunManager(
        RunStore(settings, client=s3), settings, generate=lambda r, s, run_id: "pending/staged/x"
    )

    with pytest.raises(RunCapExceeded):
        asyncio.run(manager.submit(FAMILY, REQUEST))

    [rejected] = _events(info_logs, "run_cap_rejected")
    assert rejected.reason == "daily_cap"  # type: ignore[attr-defined]


def test_reaped_runs_and_boot_resume_are_logged(
    s3: S3Client, tmp_path: Path, info_logs: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path).model_copy(update={"run_stale_after_seconds": -1})
    manager = RunManager(
        RunStore(settings, client=s3), settings, generate=lambda r, s, run_id: "pending/staged/x"
    )
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
    story_id = _real_generate(REQUEST, settings, "r" * 32).rsplit("/", 1)[-1]

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
    manager = RunManager(RunStore(settings, client=s3), settings, generate=_real_generate)
    record = asyncio.run(manager.submit(FAMILY, REQUEST))
    done = asyncio.run(manager.execute(record))
    publish_story(done.story_id, settings, client=s3, family_token=FAMILY)
    unpublish_story(done.story_id, settings, client=s3, family_token=FAMILY)

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
    generate = _generate_with(image_judge=_failing_image_judge())
    manager = RunManager(RunStore(settings, client=s3), settings, generate=generate)
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
    assert rejected.run_id == record.id  # type: ignore[attr-defined]
    assert all(r.run_id == record.id for r in redraws)  # type: ignore[attr-defined]
    [failed] = _events(info_logs, "run_failed")
    assert failed.run_id == record.id  # type: ignore[attr-defined]
    assert failed.story_id == rejected.story_id  # type: ignore[attr-defined]
    assert failed.error_type == "ImageSafetyRejectedError"  # type: ignore[attr-defined]
    assert failed.outcome == "safety_rejected"  # type: ignore[attr-defined]
    assert failed.criteria == "no_text"  # type: ignore[attr-defined]
    assert failed.exc_info is None
    _scan_for(SENTINEL, info_logs)


def test_text_gate_rejection_logs_criteria_without_the_judge_reason(
    s3: S3Client, tmp_path: Path, info_logs: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path)
    generate = _generate_with(safety_report=_failing_text_report())
    manager = RunManager(RunStore(settings, client=s3), settings, generate=generate)
    record = asyncio.run(manager.submit(FAMILY, REQUEST))
    done = asyncio.run(manager.execute(record))

    assert done.state == "failed"
    [rejected] = _events(info_logs, "story_safety_rejected")
    assert rejected.criteria == "safety/no_brands"  # type: ignore[attr-defined]
    assert rejected.run_id == record.id  # type: ignore[attr-defined]
    [failed] = _events(info_logs, "run_failed")
    assert failed.run_id == record.id  # type: ignore[attr-defined]
    assert failed.story_id == rejected.story_id  # type: ignore[attr-defined]
    assert failed.error_type == "StoryRejectedError"  # type: ignore[attr-defined]
    assert failed.criteria == "safety/no_brands"  # type: ignore[attr-defined]
    assert failed.failure_count == 1  # type: ignore[attr-defined]
    assert failed.exc_info is None
    _scan_for(SENTINEL, info_logs)


def test_a_content_rules_violation_logs_rule_names_without_a_traceback(
    s3: S3Client, tmp_path: Path, info_logs: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path)

    def violate(request: StoryRequest, st: Settings, run_id: str) -> str:
        raise ContentRulesViolation(
            [ContentViolation(rule="page_words", page_id="p1", detail=f"too long: {SENTINEL}")]
        )

    manager = RunManager(RunStore(settings, client=s3), settings, generate=violate)
    record = asyncio.run(manager.submit(FAMILY, REQUEST))
    asyncio.run(manager.execute(record))

    [failed] = _events(info_logs, "run_failed")
    assert failed.run_id == record.id  # type: ignore[attr-defined]
    assert failed.error_type == "ContentRulesViolation"  # type: ignore[attr-defined]
    assert failed.outcome == "safety_rejected"  # type: ignore[attr-defined]
    assert failed.criteria == "content_rules/page_words"  # type: ignore[attr-defined]
    assert failed.failure_count == 1  # type: ignore[attr-defined]
    assert failed.exc_info is None
    _scan_for(SENTINEL, info_logs)
