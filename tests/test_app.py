"""The player page: served whole, cookie-free, and stateless."""

from fastapi.testclient import TestClient

from src.api.main import create_app

client = TestClient(create_app())


def test_root_serves_the_player_page() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="app"' in response.text
    assert "js/main.js" in response.text


def test_no_cookies_ever_reach_child_mode() -> None:
    response = client.get("/")
    assert "set-cookie" not in response.headers


def test_player_assets_are_served() -> None:
    for path in ("css/tokens.css", "css/player.css", "js/main.js", "js/audio-engine.js"):
        assert client.get(f"/{path}").status_code == 200, path


def test_dev_manifest_fixture_is_served() -> None:
    response = client.get("/content/it/manifest.json")
    assert response.status_code == 200
    manifest = response.json()
    assert manifest["language"] == "it"
    assert manifest["stories"], "the shelf needs at least one story"


def test_api_surface_is_closed() -> None:
    # No docs, no openapi — the child app has no API to browse.
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path
