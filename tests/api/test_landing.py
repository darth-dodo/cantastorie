"""The landing page owns `/`; the child player moves to `/play`."""

from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


def test_landing_served_at_root() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert 'data-page="landing"' in response.text
    assert "/static/css/landing.css" in response.text
    assert "Start listening" in response.text


def test_landing_links_into_the_player() -> None:
    response = client.get("/")
    assert 'href="/play"' in response.text


def test_player_shell_moved_to_play() -> None:
    response = client.get("/play")
    assert response.status_code == 200
    assert "/static/css/player.css" in response.text


def test_root_no_longer_serves_the_player_shell() -> None:
    # The player CSS is the shell's fingerprint; it must not leak onto the
    # marketing page.
    response = client.get("/")
    assert "/static/css/player.css" not in response.text
