"""Render preview environments are read-only (AI-464).

render.yaml gives every PR preview blank Clerk keys and dummy write keys.
These tests build Settings from the Blueprint's own previewValues and prove
the claim the comments make: the public surfaces work, and every surface
that could write (parent area, workshop) does not exist.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from src.api.main import create_app
from src.config import Settings, get_settings

BLUEPRINT = Path(__file__).resolve().parents[1] / "render.yaml"


def _blueprint() -> dict[str, Any]:
    return yaml.safe_load(BLUEPRINT.read_text())


def _preview_env() -> dict[str, str]:
    """The env a preview sees for the keys the Blueprint overrides."""
    (service,) = _blueprint()["services"]
    return {e["key"]: e["previewValue"] for e in service["envVars"] if "previewValue" in e}


def test_previews_are_generated_automatically_and_expire() -> None:
    previews = _blueprint()["previews"]
    assert previews["generation"] == "automatic"
    assert 0 < previews["expireAfterDays"] <= 7


def test_preview_overrides_cover_every_write_credential() -> None:
    env = _preview_env()
    assert env["CLERK_PUBLISHABLE_KEY"] == ""
    assert env["CLERK_JWKS_URL"] == ""
    for key in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "OPENROUTER_API_KEY"):
        assert env[key] == "preview-disabled"
    # A blank endpoint means no R2 client can reach the live bucket, and the
    # R2 validator (which needs a separate pending bucket, B1) is skipped.
    assert env["R2_ENDPOINT_URL"] == ""


def test_previews_read_same_origin_fixtures_not_the_cors_locked_bucket() -> None:
    assert _preview_env()["ASSET_BASE"] == "/static/content"


def test_render_never_auto_deploys_so_ci_gates_production() -> None:
    # H3 (AI-479): a push to main must pass CI before it ships. The CI
    # `deploy` job fires the Render deploy hook; Render itself must not.
    (service,) = _blueprint()["services"]
    assert service["autoDeploy"] is False


@pytest.fixture
def preview_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # A production-looking env underneath, so the test proves the preview
    # overrides win rather than passing on an empty developer environment.
    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", "pk_live_x")
    monkeypatch.setenv("CLERK_JWKS_URL", "https://clerk.example/.well-known/jwks.json")
    # Production R2, minus the pending bucket: a preview must still boot even
    # if Render does not copy R2_PENDING_BUCKET from the base service.
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://acct.eu.r2.cloudflarestorage.com")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "live-access")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "live-secret")
    monkeypatch.setenv("R2_BUCKET", "cantastorie")
    monkeypatch.setenv("R2_PUBLIC_BASE", "https://pub.example.r2.dev/published")
    monkeypatch.delenv("R2_PENDING_BUCKET", raising=False)
    for key, value in _preview_env().items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def test_a_preview_boots_with_r2_disabled(preview_client: TestClient) -> None:
    settings = preview_client.app.dependency_overrides[get_settings]()  # type: ignore[attr-defined]
    assert settings.r2_endpoint_url == ""


@pytest.mark.parametrize("path", ["/", "/play", "/health", "/static/content/en/manifest.json"])
def test_public_surfaces_work_in_a_preview(preview_client: TestClient, path: str) -> None:
    assert preview_client.get(path).status_code == 200


@pytest.mark.parametrize("path", ["/parent", "/parent/stories", "/workshop"])
def test_write_surfaces_do_not_exist_in_a_preview(preview_client: TestClient, path: str) -> None:
    assert preview_client.get(path, follow_redirects=False).status_code == 404


def test_preview_posts_cannot_start_or_publish_runs(preview_client: TestClient) -> None:
    assert preview_client.post("/parent/packs", data={"theme": "the_sleepy_sea"}).status_code == 404
    assert (
        preview_client.post("/workshop/runs", data={"theme": "the_sleepy_sea"}).status_code == 404
    )
