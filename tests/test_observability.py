"""Behavior spec for LangSmith observability wiring (init_observability).

init_observability syncs the Pydantic settings into the env vars the LangSmith
SDK reads. These guard the enabled/disabled branches; monkeypatch snapshots the
touched keys so the global os.environ is restored at teardown even though
init_observability writes it directly.
"""

import os

import pytest

from src.config import Settings
from src.observability import init_observability

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
