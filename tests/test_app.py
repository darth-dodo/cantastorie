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
    assert "/static/css/tokens.css" in response.text
    assert "/static/css/player.css" in response.text


def test_static_mount_serves_js() -> None:
    assert client.get("/static/js/main.js").status_code == 200
    assert client.get("/static/js/audio-engine.js").status_code == 200


def test_dev_manifest_fixture_is_served() -> None:
    # The manifest-driven shelf reads this fixture until R2 exists; the
    # static mount must serve it under the asset base the template declares.
    response = client.get("/static/content/it/manifest.json")
    assert response.status_code == 200
    manifest = response.json()
    assert manifest["language"] == "it"
    assert manifest["stories"], "the shelf needs at least one story"
