"""Behavior specs for the engine flag (ADR-012, move-pipeline-to-temporal).

Spec: "The engine is chosen per run by a flag". `PIPELINE_ENGINE` defaults to
`inprocess`, so production keeps running generation in the web process until
the flag is turned on, and turning it on without the Temporal settings is
refused at boot instead of failing at the first submit.
"""

import base64
import os

import pytest
from pydantic import SecretStr, ValidationError

from src.config import Settings

PAYLOAD_KEY = base64.b64encode(b"k" * 32).decode()

TEMPORAL_ENV = {
    "TEMPORAL_ADDRESS": "localhost:7233",
    "TEMPORAL_NAMESPACE": "cantastorie-test",
    "TEMPORAL_API_KEY": "temporal-api-key-sample",
    "TEMPORAL_PAYLOAD_KEY": PAYLOAD_KEY,
}


@pytest.fixture
def temporal_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in TEMPORAL_ENV.items():
        monkeypatch.setenv(name, value)


def test_engine_defaults_to_inprocess() -> None:
    settings = Settings(_env_file=None)
    assert settings.pipeline_engine == "inprocess"


def test_engine_defaults_to_inprocess_without_any_temporal_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (*TEMPORAL_ENV, "PIPELINE_ENGINE"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.pipeline_engine == "inprocess"
    assert settings.temporal_address == ""


def test_engine_accepts_temporal_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, temporal_env: None
) -> None:
    monkeypatch.setenv("PIPELINE_ENGINE", "temporal")
    settings = Settings(_env_file=None)
    assert settings.pipeline_engine == "temporal"
    assert settings.temporal_address == "localhost:7233"
    assert settings.temporal_namespace == "cantastorie-test"
    assert settings.temporal_api_key.get_secret_value() == "temporal-api-key-sample"
    assert settings.temporal_payload_key.get_secret_value() == PAYLOAD_KEY


@pytest.mark.parametrize("engine", ["", "Temporal ", "celery", "inline", "render"])
def test_engine_refuses_other_values(
    monkeypatch: pytest.MonkeyPatch, temporal_env: None, engine: str
) -> None:
    monkeypatch.setenv("PIPELINE_ENGINE", engine)
    with pytest.raises(ValidationError, match="pipeline_engine"):
        Settings(_env_file=None)


@pytest.mark.parametrize("missing", list(TEMPORAL_ENV))
def test_temporal_engine_refuses_a_missing_setting(
    monkeypatch: pytest.MonkeyPatch, temporal_env: None, missing: str
) -> None:
    monkeypatch.setenv("PIPELINE_ENGINE", "temporal")
    monkeypatch.delenv(missing)
    with pytest.raises(ValidationError, match=missing):
        Settings(_env_file=None)


@pytest.mark.parametrize("key", ["not base64 at all!", base64.b64encode(b"short").decode()])
def test_temporal_engine_refuses_a_payload_key_that_is_not_32_bytes(
    monkeypatch: pytest.MonkeyPatch, temporal_env: None, key: str
) -> None:
    monkeypatch.setenv("PIPELINE_ENGINE", "temporal")
    monkeypatch.setenv("TEMPORAL_PAYLOAD_KEY", key)
    with pytest.raises(ValidationError, match="TEMPORAL_PAYLOAD_KEY"):
        Settings(_env_file=None)


def test_temporal_settings_are_ignored_while_the_engine_is_inprocess() -> None:
    settings = Settings(_env_file=None, temporal_address="localhost:7233")
    assert settings.pipeline_engine == "inprocess"


def test_temporal_secrets_never_appear_in_repr_or_str() -> None:
    settings = Settings(
        _env_file=None,
        pipeline_engine="temporal",
        temporal_address="localhost:7233",
        temporal_namespace="cantastorie-test",
        temporal_api_key=SecretStr("temporal-api-key-sample"),
        temporal_payload_key=SecretStr(PAYLOAD_KEY),
    )
    for rendered in (repr(settings), str(settings)):
        assert "temporal-api-key-sample" not in rendered
        assert PAYLOAD_KEY not in rendered


FAKE_API_KEY = "tmprl-FAKEAPIKEYSECRETVALUE1234567890"


@pytest.mark.parametrize(
    ("overrides", "missing"),
    [
        ({"TEMPORAL_PAYLOAD_KEY": ""}, "TEMPORAL_PAYLOAD_KEY"),
        ({"TEMPORAL_PAYLOAD_KEY": base64.b64encode(b"z" * 31).decode()}, None),
        ({"TEMPORAL_NAMESPACE": ""}, "TEMPORAL_NAMESPACE"),
    ],
)
def test_a_refused_temporal_config_prints_no_part_of_a_secret(
    monkeypatch: pytest.MonkeyPatch,
    temporal_env: None,
    overrides: dict[str, str],
    missing: str | None,
) -> None:
    monkeypatch.setenv("PIPELINE_ENGINE", "temporal")
    monkeypatch.setenv("TEMPORAL_API_KEY", FAKE_API_KEY)
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)
    payload_key = os.environ["TEMPORAL_PAYLOAD_KEY"]
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)
    text = str(caught.value)
    if missing:
        assert missing in text
    for secret in (s for s in (FAKE_API_KEY, payload_key) if s):
        for start in range(max(len(secret) - 7, 1)):
            assert secret[start : start + 8] not in text
