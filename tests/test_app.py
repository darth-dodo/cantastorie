"""Smoke tests for the FastAPI app."""

from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_player_page_serves_shell() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="app"' in response.text
    assert "/static/css/output.css" in response.text


def test_static_mount_serves_js() -> None:
    response = client.get("/static/js/main.js")
    assert response.status_code == 200
