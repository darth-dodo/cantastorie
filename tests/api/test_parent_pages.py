"""/parent page tests (AI-411): sign-in rendering, feature guard, identity dispatch."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.auth as auth_module
from src.api.auth import SESSION_COOKIE
from src.api.routes.parent import get_run_manager
from src.api.routes.parent import router as parent_router
from src.config import Settings, get_settings
from src.workshop.manager import RunCapExceeded
from src.workshop.records import PackRequest, new_run
from tests.api.clerk_jwt import (
    clerk_settings,
    generate_rsa_keypair,
    make_mock_fetch,
    mint_token,
    valid_payload,
)

VALID_TOKEN = "0123456789abcdef0123456789abcdef"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def reset_jwks_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module._jwks_state, "keys", None)
    monkeypatch.setattr(auth_module._jwks_state, "fetched_at", 0.0)


def _make_app(settings: Settings) -> FastAPI:
    app = FastAPI()
    app.include_router(parent_router)
    app.dependency_overrides[get_settings] = lambda: settings
    return app


def _signed_in_client(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    **payload_kwargs: Any,
) -> TestClient:
    private_key = generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", make_mock_fetch(private_key))
    token = mint_token(private_key, valid_payload(**payload_kwargs))
    client = TestClient(_make_app(settings))
    client.cookies.set(SESSION_COOKIE, token)
    return client


def test_anonymous_gets_sign_in_page_with_clerk_script() -> None:
    # issuer set explicitly: the route derives the FAPI host from it, and
    # clerk_settings() defaults clerk_issuer to "" (pk fallback would decode
    # the dummy key's suffix into garbage).
    client = TestClient(_make_app(clerk_settings(clerk_issuer="https://test.clerk.test")))
    response = client.get("/parent")
    assert response.status_code == 200
    assert "clerk.browser.js" in response.text
    assert 'data-clerk-publishable-key="pk_test_xxx"' in response.text
    # FAPI host derived from clerk_issuer
    assert "test.clerk.test/npm/@clerk/clerk-js@6" in response.text


def test_unset_clerk_config_404s_the_page() -> None:
    client = TestClient(_make_app(clerk_settings(jwks_url="")))
    assert client.get("/parent").status_code == 404


def test_signed_in_unprovisioned_gets_onboarding_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Issuer set: rendering the sign-in page runs _fapi_host, and the dummy
    # publishable key (pk_test_xxx) is not valid base64 for the pk fallback.
    client = _signed_in_client(
        monkeypatch,
        clerk_settings(clerk_issuer="https://test.clerk.test"),
        include_family_token=False,
    )
    response = client.get("/parent")
    assert response.status_code == 200
    assert "data-parent-onboarding" in response.text  # JS will POST /parent/api/provision


def test_disabled_session_gets_403(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _signed_in_client(
        monkeypatch, clerk_settings(), include_family_token=False, disabled=True
    )
    assert client.get("/parent").status_code == 403


def test_provision_url_is_unchanged_after_prefix_refactor() -> None:
    app = _make_app(clerk_settings())
    assert str(app.url_path_for("provision")) == "/parent/api/provision"


class _FakeManager:
    """Records submit calls; optionally raises RunCapExceeded."""

    def __init__(self, raise_cap: RunCapExceeded | None = None) -> None:
        self.submits: list[tuple[str, Any]] = []
        self.executed: list[Any] = []
        self.raise_cap = raise_cap

    async def submit(self, family_token: str, request: Any) -> Any:
        if self.raise_cap is not None:
            raise self.raise_cap
        self.submits.append((family_token, request))
        return new_run(family_token, request)

    async def execute(self, record: Any) -> Any:
        self.executed.append(record)
        return record


def _packs_client(
    monkeypatch: pytest.MonkeyPatch,
    manager: _FakeManager,
    *,
    family_token: str = VALID_TOKEN,
) -> TestClient:
    private_key = generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", make_mock_fetch(private_key))
    # Issuer set so the cap-hit branch can render packs.html (_fapi_host would
    # otherwise try to base64-decode the dummy publishable key). The minted
    # token must carry a matching iss so require_parent still verifies.
    issuer = "https://test.clerk.test"
    settings = clerk_settings(clerk_issuer=issuer)
    app = _make_app(settings)
    app.dependency_overrides[get_run_manager] = lambda: manager
    token = mint_token(private_key, valid_payload(family_token=family_token, iss=issuer))
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    return client


def test_pack_request_submits_under_session_family_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _FakeManager()
    client = _packs_client(monkeypatch, manager)
    response = client.post(
        "/parent/packs",
        data={"theme": "the_sleepy_sea", "language": "it", "count": "1", "premise": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/parent"
    assert len(manager.submits) == 1
    assert manager.submits[0][0] == VALID_TOKEN  # session token, no form override
    assert len(manager.executed) == 1  # background execution kicked off


def test_form_cannot_override_family_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tenancy rule: a posted family_token field is ignored entirely."""
    manager = _FakeManager()
    client = _packs_client(monkeypatch, manager)
    client.post(
        "/parent/packs",
        data={
            "theme": "the_sleepy_sea",
            "language": "it",
            "count": "1",
            "family_token": "f" * 32,
        },
        follow_redirects=False,
    )
    assert manager.submits[0][0] == VALID_TOKEN


def test_cap_hit_renders_friendly_message_with_active_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = new_run(VALID_TOKEN, PackRequest(theme="the_sleepy_sea", language="it", count=1))
    running = active.advance("running")
    manager = _FakeManager(
        raise_cap=RunCapExceeded("a story pack is already being made", active=running)
    )
    client = _packs_client(monkeypatch, manager)
    response = client.post(
        "/parent/packs",
        data={"theme": "the_sleepy_sea", "language": "it", "count": "1"},
    )
    assert response.status_code == 200
    assert "already being made" in response.text
    assert "running" in response.text  # the active run's state is shown


def test_unauthenticated_pack_post_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(clerk_settings())
    response = TestClient(app).post(
        "/parent/packs", data={"theme": "the_sleepy_sea", "language": "it", "count": "1"}
    )
    assert response.status_code == 401


def _store_with_runs(manager: _FakeManager, records: list[Any]) -> None:
    class _FakeStore:
        def list_runs(self, *, family_token: str | None = None, state: Any = None) -> list[Any]:
            return [r for r in records if family_token is None or r.family_token == family_token]

        def load(self, family_token: str, run_id: str) -> Any:
            for r in records:
                if r.family_token == family_token and r.id == run_id:
                    return r
            return None

    manager.store = _FakeStore()  # type: ignore[attr-defined]


def test_my_packs_lists_only_this_familys_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    mine = new_run(VALID_TOKEN, PackRequest(theme="the_sleepy_sea", language="it", count=1))
    other = new_run("f" * 32, PackRequest(theme="the_sleepy_sea", language="it", count=1))
    manager = _FakeManager()
    _store_with_runs(manager, [mine, other])
    client = _packs_client(monkeypatch, manager)
    response = client.get("/parent")
    assert response.status_code == 200
    assert mine.id in response.text
    assert other.id not in response.text


def test_cross_tenant_progress_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE tenancy test: family A cannot view family B's run — by URL guessing either."""
    others = new_run("f" * 32, PackRequest(theme="the_sleepy_sea", language="it", count=1))
    manager = _FakeManager()
    _store_with_runs(manager, [others])
    client = _packs_client(monkeypatch, manager)  # session = VALID_TOKEN (family A)
    assert client.get(f"/parent/packs/{others.id}/progress").status_code == 404


def test_own_progress_fragment_polls_parent_url(monkeypatch: pytest.MonkeyPatch) -> None:
    mine = new_run(VALID_TOKEN, PackRequest(theme="the_sleepy_sea", language="it", count=1))
    manager = _FakeManager()
    _store_with_runs(manager, [mine])
    client = _packs_client(monkeypatch, manager)
    response = client.get(f"/parent/packs/{mine.id}/progress")
    assert response.status_code == 200
    assert f"/parent/packs/{mine.id}/progress" in response.text  # hx-get, not /workshop
    assert "/workshop" not in response.text  # no operator URLs leak to parents
    assert "Delete run" not in response.text  # operator controls hidden


def test_workshop_progress_fragment_is_unchanged_for_operator() -> None:
    """The parametrization must not alter the workshop's rendering defaults."""
    from src.api.routes import workshop as workshop_module  # noqa: PLC0415

    # smoke: the workshop progress route still renders with /workshop URLs.
    # Covered fully by the existing tests/workshop/test_routes.py suite —
    # this test just pins the default base_url in the template.
    source = (workshop_module.TEMPLATES_DIR / "workshop" / "_progress.html").read_text()
    assert 'base_url | default("/workshop/runs")' in source.replace("'", '"')


def test_clerk_loads_nowhere_in_the_child_player() -> None:
    """Settled (ADR-003): the child player never loads Clerk.

    Both admin surfaces are Clerk-gated — the parent area (/parent, AI-411) and
    the operator workshop (/workshop, AI-426) — so Clerk assets legitimately live
    in their template dirs and in workshop.js. The invariant that still holds,
    and that this guards, is that NO Clerk script, hostname, or cookie logic ever
    reaches a child-player path. Scans every template outside
    src/templates/parent|workshop and every static JS file except workshop.js.
    """
    import re  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    root = Path(__file__).resolve().parent.parent.parent / "src"
    pattern = re.compile(r"clerk", re.IGNORECASE)
    admin_template_dirs = ("templates/parent", "templates/workshop")
    offenders: list[str] = []
    for path in (root / "templates").rglob("*.html"):
        norm = str(path).replace("\\", "/")
        if any(admin_dir in norm for admin_dir in admin_template_dirs):
            continue
        if pattern.search(path.read_text()):
            offenders.append(str(path))
    for path in (root / "static" / "js").rglob("*.js"):
        if path.name == "workshop.js":  # the operator surface's own admin JS
            continue
        if pattern.search(path.read_text()):
            offenders.append(str(path))
    assert offenders == [], f"Clerk reference reached a child-player path: {offenders}"
