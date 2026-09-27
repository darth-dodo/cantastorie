"""Behavior spec for LangSmith observability wiring (init_observability).

init_observability syncs the Pydantic settings into the env vars the LangSmith
SDK reads. These guard the enabled/disabled branches; monkeypatch snapshots the
touched keys so the global os.environ is restored at teardown even though
init_observability writes it directly.
"""

import os
from typing import Any

import pytest

from src import observability
from src.config import Settings
from src.observability import init_error_monitoring, init_observability

LANGSMITH_ENV = (
    "LANGSMITH_TRACING",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
    "LANGSMITH_ENDPOINT",
)


@pytest.fixture(autouse=True)
def _isolate_langsmith_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # delenv records each key's pre-test value, so monkeypatch restores the
    # global os.environ at teardown even though init_observability writes it directly.
    for key in LANGSMITH_ENV:
        monkeypatch.delenv(key, raising=False)


def test_tracing_enabled_exports_the_langsmith_endpoint() -> None:
    """With tracing on, the configured endpoint reaches the SDK env var — so a
    non-default (e.g. EU) endpoint is honored, not silently the SDK default."""
    settings = Settings(
        _env_file=None,
        langsmith_tracing=True,
        langsmith_api_key="ls_test_key",
        langsmith_endpoint="https://eu.smith.langchain.com",
    )

    init_observability(settings)

    assert os.environ["LANGSMITH_ENDPOINT"] == "https://eu.smith.langchain.com"
    assert os.environ["LANGSMITH_TRACING"] == "true"


def test_tracing_disabled_leaves_the_sdk_inert() -> None:
    """The default (tracing off) must not switch the SDK on."""
    settings = Settings(_env_file=None, langsmith_tracing=False)

    init_observability(settings)

    assert "LANGSMITH_TRACING" not in os.environ


# --- Sentry error monitoring (ADR-009) ---------------------------------------


@pytest.fixture
def sentry_init_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record sentry_sdk.init calls instead of starting a real client, so no test
    ever leaves a global Sentry hub configured behind it."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(observability.sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
    return calls


def test_no_dsn_leaves_sentry_uninitialized(sentry_init_calls: list[dict[str, Any]]) -> None:
    """Unset SENTRY_DSN (the default) means Sentry never starts — no data leaves."""
    init_error_monitoring(Settings(_env_file=None))

    assert sentry_init_calls == []


def test_dsn_starts_sentry_for_errors_only(sentry_init_calls: list[dict[str, Any]]) -> None:
    """Sentry catches exceptions; LangSmith stays the tracing layer (ADR-007)."""
    settings = Settings(
        _env_file=None,
        sentry_dsn="https://key@o1.ingest.de.sentry.io/1",
        sentry_environment="production",
        sentry_release="abc123",
    )

    init_error_monitoring(settings)

    [kwargs] = sentry_init_calls
    assert kwargs["dsn"] == "https://key@o1.ingest.de.sentry.io/1"
    assert kwargs["environment"] == "production"
    assert kwargs["release"] == "abc123"
    assert kwargs["traces_sample_rate"] == 0.0


def test_sentry_events_carry_no_child_data(sentry_init_calls: list[dict[str, Any]]) -> None:
    """Parent requests and pipeline prompts can name a child: events must carry
    no PII headers/IPs, no request bodies, and no local variables."""
    init_error_monitoring(Settings(_env_file=None, sentry_dsn="https://key@o1.ingest.sentry.io/1"))

    [kwargs] = sentry_init_calls
    assert kwargs["send_default_pii"] is False
    assert kwargs["max_request_body_size"] == "never"
    assert kwargs["include_local_variables"] is False


def test_render_git_commit_becomes_the_sentry_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render exposes the deployed commit as RENDER_GIT_COMMIT; the SDK does not
    auto-detect it, so Settings maps it onto sentry_release."""
    monkeypatch.delenv("SENTRY_RELEASE", raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "deadbeef")

    assert Settings(_env_file=None).sentry_release == "deadbeef"
