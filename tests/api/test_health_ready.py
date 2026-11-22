"""Readiness (GitHub #183, M1): /health/ready says whether the service can reach R2.

Spec: openspec/specs/service-health/spec.md.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import boto3
import pytest
import yaml  # type: ignore[import-untyped]
from botocore.exceptions import EndpointConnectionError
from fastapi.testclient import TestClient
from moto import mock_aws
from pydantic import SecretStr

from src.api.main import create_app
from src.api.routes import health
from src.config import Settings, get_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from mypy_boto3_s3 import S3Client

ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = "https://acct-1234.r2.example.test"
KEY_ID = "AKIDREADYTESTKEY"
SECRET = "ready-test-secret-value"
PUBLISHED = "ready-published-bucket"
PENDING = "ready-pending-bucket"


def _r2_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        r2_endpoint_url=ENDPOINT,
        r2_access_key_id=SecretStr(KEY_ID),
        r2_secret_access_key=SecretStr(SECRET),
        r2_bucket=PUBLISHED,
        r2_public_base="https://pub.example.test/published",
        r2_pending_bucket=PENDING,
    )


def _client(settings: Settings) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    health.clear_cache()
    yield
    health.clear_cache()


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Iterator[S3Client]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=PUBLISHED)
        client.create_bucket(Bucket=PENDING)
        monkeypatch.setattr(health, "r2_client", lambda settings: client)
        yield client


class _BrokenClient:
    """Fails every call with an error that carries the endpoint and the key."""

    def head_bucket(self, **kwargs: Any) -> None:
        raise EndpointConnectionError(
            endpoint_url=f"{ENDPOINT}/{kwargs['Bucket']}?key={KEY_ID}:{SECRET}"
        )


class _HangingClient:
    def head_bucket(self, **kwargs: Any) -> None:
        time.sleep(1)


def test_liveness_answers_even_when_r2_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "r2_client", lambda settings: _BrokenClient())
    response = _client(_r2_settings()).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_when_both_buckets_answer(s3: S3Client) -> None:
    response = _client(_r2_settings()).get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"settings": "ok", "r2_published": "ok", "r2_pending": "ok"},
    }


def test_not_ready_when_the_pending_bucket_is_missing(s3: S3Client) -> None:
    s3.delete_bucket(Bucket=PENDING)
    response = _client(_r2_settings()).get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"settings": "ok", "r2_published": "ok", "r2_pending": "failed"},
    }


def test_ready_with_r2_skipped_when_r2_is_not_configured() -> None:
    unconfigured = Settings(_env_file=None)  # type: ignore[call-arg]
    response = _client(unconfigured).get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"settings": "ok", "r2_published": "skipped", "r2_pending": "skipped"},
    }


LEAKS = (KEY_ID, SECRET, ENDPOINT, "r2.example.test", PUBLISHED, PENDING, "Could not connect")


def test_a_failing_check_leaks_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(health, "r2_client", lambda settings: _BrokenClient())
    with caplog.at_level(logging.WARNING):
        response = _client(_r2_settings()).get("/health/ready")
    assert response.status_code == 503
    assert "readiness check r2_published failed: EndpointConnectionError" in caplog.text
    for leak in LEAKS:
        assert leak not in response.text
        assert leak not in caplog.text


def test_a_client_that_cannot_be_built_is_not_ready_not_a_500(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(settings: Settings) -> Any:
        raise ValueError(f"Invalid endpoint: {ENDPOINT}")

    monkeypatch.setattr(health, "r2_client", broken)
    with caplog.at_level(logging.WARNING):
        response = _client(_r2_settings()).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {
        "settings": "ok",
        "r2_published": "failed",
        "r2_pending": "failed",
    }
    assert ENDPOINT not in response.text
    assert ENDPOINT not in caplog.text


def test_each_r2_call_is_one_attempt_with_short_timeouts() -> None:
    config: Any = health.r2_client(
        _r2_settings()
    ).meta.config  # botocore stubs omit these attributes
    assert config.retries["total_max_attempts"] == 1
    assert config.connect_timeout == 2
    assert config.read_timeout == 2


def test_a_hanging_r2_call_fails_within_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "r2_client", lambda settings: _HangingClient())
    monkeypatch.setattr(health, "CHECK_TIMEOUT_S", 0.2)
    started = time.monotonic()
    response = _client(_r2_settings()).get("/health/ready")
    assert time.monotonic() - started < 2
    assert response.status_code == 503
    assert response.json()["checks"]["r2_published"] == "failed"


def test_polls_inside_the_cache_window_call_r2_once(
    s3: S3Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = s3.head_bucket

    def counting(**kwargs: Any) -> Any:
        calls.append(kwargs["Bucket"])
        return real(**kwargs)

    monkeypatch.setattr(s3, "head_bucket", counting)
    client = _client(_r2_settings())
    assert client.get("/health/ready").status_code == 200
    assert client.get("/health/ready").status_code == 200
    assert sorted(calls) == sorted([PUBLISHED, PENDING])


def test_render_gates_on_readiness() -> None:
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text())
    web = next(s for s in blueprint["services"] if s.get("type") == "web")
    assert web["healthCheckPath"] == "/health/ready"
