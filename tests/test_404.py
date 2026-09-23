"""Branded 404 page tests (AI-449).

Requirements:
  (a) Unknown routes return HTTP 404 with branded HTML (not bare JSON).
  (b) /parent with Clerk unconfigured returns 404 with branded HTML.
"""

from fastapi.testclient import TestClient

from src.api.main import app, create_app
from src.config import Settings, get_settings

client = TestClient(app)

# A stable marker that lives in 404.html — picked once and tested twice.
_MARKER = "not-found"


def test_unknown_route_returns_404() -> None:
    response = client.get("/nope")
    assert response.status_code == 404


def test_unknown_route_renders_branded_html() -> None:
    response = client.get("/nope")
    assert _MARKER in response.text
    assert "/static/css/tokens.css" in response.text


def test_unknown_route_has_home_link() -> None:
    response = client.get("/nope")
    assert 'href="/"' in response.text or 'href="/play"' in response.text


def test_parent_unconfigured_returns_404() -> None:
    """GET /parent with no Clerk config renders the branded 404 page."""
    app2 = create_app()
    app2.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, clerk_jwks_url="")
    c = TestClient(app2)
    response = c.get("/parent")
    assert response.status_code == 404
    assert _MARKER in response.text
    assert "/static/css/tokens.css" in response.text
