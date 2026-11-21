"""Security response headers on every page (M16, AI-496).

Built through create_app() so the test exercises the real middleware stack,
not a router mounted on a bare FastAPI.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app
from src.config import get_settings
from tests.api.clerk_jwt import clerk_settings


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    # Clerk on, so /parent renders its sign-in page rather than a 404.
    settings = clerk_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


@pytest.mark.parametrize("path", ["/health", "/play", "/parent"])
def test_every_page_carries_the_security_headers(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response.headers["X-Frame-Options"] == "DENY"
    csp = response.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "object-src 'none'" in csp


def test_error_responses_carry_the_headers_too(client: TestClient) -> None:
    response = client.get("/no-such-page")
    assert response.status_code == 404
    assert response.headers["X-Frame-Options"] == "DENY"
