"""Cross-origin state-changing requests are refused (M4, AI-496).

The /workshop and /parent write routes used to rely on Clerk's SameSite
cookie alone. The guard runs before routing, so each probe route below
records whether its handler ran — a refused request must change nothing.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.main import create_app

PROBE_PATHS = ("/workshop/__probe", "/parent/__probe", "/elsewhere/__probe")


@pytest.fixture
def app_and_calls() -> tuple[FastAPI, list[str]]:
    app = create_app()
    calls: list[str] = []
    for path in PROBE_PATHS:

        @app.api_route(path, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
        async def probe(_path: str = path) -> dict[str, str]:
            calls.append(_path)
            return {"ok": "yes"}

    return app, calls


@pytest.fixture
def client(app_and_calls: tuple[FastAPI, list[str]]) -> TestClient:
    return TestClient(app_and_calls[0])


@pytest.fixture
def calls(app_and_calls: tuple[FastAPI, list[str]]) -> list[str]:
    return app_and_calls[1]


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("path", ["/workshop/__probe", "/parent/__probe"])
def test_a_cross_origin_write_is_refused_and_changes_nothing(
    client: TestClient, calls: list[str], method: str, path: str
) -> None:
    response = client.request(method, path, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert calls == []


def test_a_same_origin_write_goes_through(client: TestClient, calls: list[str]) -> None:
    # TestClient's base URL is http://testserver — an HTMX post from the page.
    response = client.post(
        "/workshop/__probe", headers={"Origin": "http://testserver", "HX-Request": "true"}
    )
    assert response.status_code == 200
    assert calls == ["/workshop/__probe"]


def test_a_same_origin_write_behind_a_tls_terminating_proxy_goes_through(
    client: TestClient, calls: list[str]
) -> None:
    # Render terminates TLS: the app sees http while the browser's Origin is
    # https. The proxy's X-Forwarded-Proto names the scheme the browser used.
    response = client.post(
        "/parent/__probe",
        headers={"Origin": "https://testserver", "X-Forwarded-Proto": "https"},
    )
    assert response.status_code == 200
    assert calls == ["/parent/__probe"]


def test_a_different_port_is_a_different_origin(client: TestClient, calls: list[str]) -> None:
    response = client.post("/parent/__probe", headers={"Origin": "http://testserver:8080"})
    assert response.status_code == 403
    assert calls == []


def test_an_opaque_null_origin_is_refused(client: TestClient, calls: list[str]) -> None:
    response = client.post("/parent/__probe", headers={"Origin": "null"})
    assert response.status_code == 403
    assert calls == []


def test_sec_fetch_site_cross_site_without_origin_is_refused(
    client: TestClient, calls: list[str]
) -> None:
    response = client.post("/workshop/__probe", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_sec_fetch_site_same_origin_without_origin_goes_through(
    client: TestClient, calls: list[str], site: str
) -> None:
    response = client.post("/workshop/__probe", headers={"Sec-Fetch-Site": site})
    assert response.status_code == 200


def test_a_write_with_neither_header_goes_through(client: TestClient, calls: list[str]) -> None:
    # Non-browser clients (curl, the test suite) send no Origin or Sec-Fetch-Site.
    response = client.post("/parent/__probe")
    assert response.status_code == 200
    assert calls == ["/parent/__probe"]


def test_a_cross_origin_read_is_not_the_guards_business(
    client: TestClient, calls: list[str]
) -> None:
    response = client.get("/workshop/__probe", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200


def test_writes_outside_workshop_and_parent_are_not_guarded(
    client: TestClient, calls: list[str]
) -> None:
    response = client.post("/elsewhere/__probe", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200


def test_a_real_workshop_write_route_is_guarded_before_auth(client: TestClient) -> None:
    # Clerk is unset here, so the route itself would answer 404; the guard
    # answers first.
    response = client.post("/workshop/runs/abc/approve", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
