"""Security response headers on every page (M16, AI-496, AI-505).

Built through create_app() so the test exercises the real middleware stack,
not a router mounted on a bare FastAPI.
"""

import logging
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app
from src.api.routes._nav import fapi_host
from src.api.security import build_policies
from src.config import Settings, get_settings
from tests.api.clerk_jwt import clerk_settings

BASELINE = {
    "default-src": ["'self'"],
    "base-uri": ["'self'"],
    "object-src": ["'none'"],
    "frame-ancestors": ["'none'"],
    "form-action": ["'self'"],
}
CLERK_ENV = {
    "CLERK_PUBLISHABLE_KEY": "pk_test_xxx",
    "CLERK_SECRET_KEY": "sk_test_xxx",
    "CLERK_JWKS_URL": "https://test.clerk.test/.well-known/jwks.json",
    "CLERK_ISSUER": "https://test.clerk.test",
}
THIRD_PARTY_MARKERS = ("clerk", "challenges.cloudflare.com", "google", "gstatic")


def parse(policy: str) -> dict[str, list[str]]:
    directives: dict[str, list[str]] = {}
    for part in policy.split(";"):
        tokens = part.split()
        if tokens:
            assert tokens[0] not in directives, f"duplicate directive {tokens[0]}"
            directives[tokens[0]] = tokens[1:]
    return directives


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    # Clerk on, so /parent renders its sign-in page rather than a 404.
    clerk = clerk_settings()
    app.dependency_overrides[get_settings] = lambda: clerk
    return TestClient(app)


@pytest.fixture
def env_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Build apps from real env vars: create_app() reads get_settings() once."""
    for name in ("ASSET_BASE", "R2_PUBLIC_BASE", "CSP_REPORT_ONLY", *CLERK_ENV):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def app_client(monkeypatch: pytest.MonkeyPatch, **env: str) -> TestClient:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    return TestClient(create_app(), raise_server_exceptions=False)


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


# 1.1 Policy builder: baseline and no unsafe sources


@pytest.mark.parametrize("clerk_on", [False, True])
def test_every_policy_has_the_baseline_and_no_unsafe_script(clerk_on: bool) -> None:
    base = clerk_settings() if clerk_on else settings()
    for policy in build_policies(base):
        directives = parse(policy)
        for name, sources in BASELINE.items():
            assert directives[name] == sources, (name, policy)
        assert "'unsafe-inline'" not in directives["script-src"]
        assert "'unsafe-eval'" not in directives["script-src"]


def test_public_policy_is_self_only_for_scripts_styles_and_fonts() -> None:
    public, _ = build_policies(clerk_settings())
    directives = parse(public)
    assert directives["script-src"] == ["'self'"]
    assert directives["style-src"] == ["'self'"]
    assert directives["font-src"] == ["'self'"]


@pytest.mark.parametrize("clerk_on", [False, True])
def test_no_policy_names_a_google_host(clerk_on: bool) -> None:
    base = clerk_settings() if clerk_on else settings()
    for policy in build_policies(base):
        assert "google" not in policy
        assert "gstatic" not in policy
        assert parse(policy)["font-src"] == ["'self'"]


# 1.2 Asset origins


def test_absolute_asset_base_origin_is_allowed_for_assets_not_scripts() -> None:
    public, admin = build_policies(settings(asset_base="https://pub-example.r2.dev/published"))
    for policy in (public, admin):
        directives = parse(policy)
        for name in ("img-src", "media-src", "connect-src"):
            assert "https://pub-example.r2.dev" in directives[name]
        assert "https://pub-example.r2.dev" not in directives["script-src"]
        assert "pub-example" not in " ".join(directives["script-src"])


def test_r2_public_base_on_another_origin_is_added_and_deduplicated() -> None:
    public, _ = build_policies(
        settings(
            asset_base="https://pub-example.r2.dev/published",
            r2_public_base="https://cdn.example.org:8443/published",
        )
    )
    directives = parse(public)
    assert directives["img-src"] == [
        "'self'",
        "https://pub-example.r2.dev",
        "https://cdn.example.org:8443",
    ]
    same, _ = build_policies(
        settings(
            asset_base="https://pub-example.r2.dev/published",
            r2_public_base="https://pub-example.r2.dev/published",
        )
    )
    assert parse(same)["connect-src"] == ["'self'", "https://pub-example.r2.dev"]


@pytest.mark.parametrize("asset_base", ["/static/content", "/published"])
def test_relative_asset_base_adds_nothing_beyond_self(asset_base: str) -> None:
    public, _ = build_policies(settings(asset_base=asset_base))
    directives = parse(public)
    for name in ("img-src", "media-src", "connect-src"):
        assert directives[name] == ["'self'"]


# 1.3 Admin policy


def test_admin_policy_adds_the_clerk_hosts() -> None:
    base = clerk_settings()
    host = f"https://{fapi_host(base)}"
    _, admin = build_policies(base)
    directives = parse(admin)
    assert host == "https://test.clerk.test"
    assert directives["script-src"] == [
        "'self'",
        host,
        "https://challenges.cloudflare.com",
        "https://*.protect.clerk.com",
    ]
    assert directives["connect-src"] == ["'self'", host, "https://*.protect.clerk.com:*"]
    assert directives["img-src"] == ["'self'", "https://img.clerk.com", "data:"]
    assert directives["frame-src"] == [
        "https://challenges.cloudflare.com",
        "https://*.protect.clerk.com",
    ]
    assert directives["worker-src"] == ["'self'", "blob:"]
    assert directives["style-src"] == ["'self'", "'unsafe-inline'"]
    assert directives["font-src"] == ["'self'"]


def test_admin_policy_equals_public_when_clerk_is_unset() -> None:
    public, admin = build_policies(settings())
    assert admin == public


def test_public_policy_names_no_clerk_host_even_with_clerk_on() -> None:
    public, _ = build_policies(clerk_settings())
    for marker in THIRD_PARTY_MARKERS:
        assert marker not in public


# 2.1 Headers on every response, through create_app()


@pytest.mark.parametrize("path", ["/play", "/", "/health", "/no-such-page"])
def test_public_paths_carry_the_public_policy(env_app: pytest.MonkeyPatch, path: str) -> None:
    client = app_client(env_app, **CLERK_ENV)
    public, _ = build_policies(get_settings())
    response = client.get(path)
    assert response.headers["Content-Security-Policy"] == public
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response.headers["X-Frame-Options"] == "DENY"


@pytest.mark.parametrize("path", ["/parent", "/workshop"])
def test_admin_paths_carry_the_admin_policy(env_app: pytest.MonkeyPatch, path: str) -> None:
    client = app_client(env_app, **CLERK_ENV)
    public, admin = build_policies(get_settings())
    assert admin != public
    response = client.get(path, follow_redirects=False)
    assert response.headers["Content-Security-Policy"] == admin


def test_admin_policy_uses_the_host_the_clerk_script_tags_load_from(
    env_app: pytest.MonkeyPatch,
) -> None:
    client = app_client(env_app, **CLERK_ENV)
    response = client.get("/parent")
    assert response.status_code == 200
    assert 'src="https://test.clerk.test/npm/@clerk/clerk-js' in response.text
    directives = parse(response.headers["Content-Security-Policy"])
    assert "https://test.clerk.test" in directives["script-src"]
    assert "https://test.clerk.test" in directives["connect-src"]


def test_parent_without_clerk_is_a_404_with_the_public_policy(
    env_app: pytest.MonkeyPatch,
) -> None:
    client = app_client(env_app)
    public, _ = build_policies(get_settings())
    response = client.get("/parent")
    assert response.status_code == 404
    assert response.headers["Content-Security-Policy"] == public


def test_child_player_header_names_no_third_party_source(env_app: pytest.MonkeyPatch) -> None:
    """Header twin of test_clerk_loads_nowhere_in_the_child_player (ADR-003)."""
    client = app_client(env_app, ASSET_BASE="https://pub-example.r2.dev/published", **CLERK_ENV)
    directives = parse(client.get("/play").headers["Content-Security-Policy"])
    assert directives["script-src"] == ["'self'"]
    for sources in directives.values():
        for source in sources:
            for marker in THIRD_PARTY_MARKERS:
                assert marker not in source


def test_static_files_carry_the_policy(env_app: pytest.MonkeyPatch) -> None:
    client = app_client(env_app)
    public, _ = build_policies(get_settings())
    response = client.get("/static/css/tokens.css")
    assert response.status_code == 200
    assert response.headers["Content-Security-Policy"] == public


# 2.2 Report-only switch


def test_default_enforces_the_policy(env_app: pytest.MonkeyPatch) -> None:
    response = app_client(env_app).get("/play")
    assert "Content-Security-Policy" in response.headers
    assert "Content-Security-Policy-Report-Only" not in response.headers


def test_report_only_sends_the_same_policy_under_the_report_only_name(
    env_app: pytest.MonkeyPatch,
) -> None:
    enforced = app_client(env_app).get("/play").headers["Content-Security-Policy"]
    response = app_client(env_app, CSP_REPORT_ONLY="true").get("/play")
    assert response.headers["Content-Security-Policy-Report-Only"] == enforced
    assert "Content-Security-Policy" not in response.headers


# 2.4 Server errors carry the headers too


def _boom_client(monkeypatch: pytest.MonkeyPatch, path: str, **env: str) -> TestClient:
    client = app_client(monkeypatch, **env)

    async def boom() -> None:
        raise RuntimeError("forced failure")

    client.app.add_api_route(path, boom)  # type: ignore[attr-defined]
    return client


@pytest.mark.parametrize(("path", "admin"), [("/boom-test", False), ("/workshop/boom-test", True)])
def test_a_500_carries_the_headers_and_the_paths_policy(
    env_app: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    path: str,
    admin: bool,
) -> None:
    client = _boom_client(env_app, path, **CLERK_ENV)
    public, admin_policy = build_policies(get_settings())
    with (
        patch("sentry_sdk.capture_exception") as capture,
        caplog.at_level(logging.ERROR),
    ):
        response = client.get(path)
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Content-Security-Policy"] == (admin_policy if admin else public)
    capture.assert_called_once()
    assert isinstance(capture.call_args.args[0], RuntimeError)
    assert any(
        record.exc_info and isinstance(record.exc_info[1], RuntimeError)
        for record in caplog.records
    )


def test_a_500_in_report_only_mode_uses_the_report_only_name(
    env_app: pytest.MonkeyPatch,
) -> None:
    client = _boom_client(env_app, "/boom-test", CSP_REPORT_ONLY="true")
    with patch("sentry_sdk.capture_exception"):
        response = client.get("/boom-test")
    assert response.status_code == 500
    assert "Content-Security-Policy-Report-Only" in response.headers
    assert "Content-Security-Policy" not in response.headers
