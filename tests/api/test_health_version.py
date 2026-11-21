from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from src.api.main import create_app

if TYPE_CHECKING:
    import pytest


def test_version_reports_the_deployed_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RENDER_GIT_COMMIT", "1600301abc")
    response = TestClient(create_app()).get("/health/version")
    assert response.status_code == 200
    assert response.json() == {"commit": "1600301abc"}


def test_version_is_empty_locally_and_says_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    response = TestClient(create_app()).get("/health/version")
    assert response.json() == {"commit": ""}
