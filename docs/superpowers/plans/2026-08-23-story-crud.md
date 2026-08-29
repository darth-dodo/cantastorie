# Story CRUD (Published-Story Management) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parents list and hard-delete their own published pack stories; the operator browses everything published on R2 and deletes anything — one primitive (`unpublish_story`), no pipeline behavior changes.

**Architecture:** A new listing pair (`list_published_stories`, `list_orphan_story_dirs`) reads the existing per-language manifests in `src/pipeline/publish.py`. Two route pairs render HTMX row lists and call the existing `unpublish_story()` for deletion: operator face in `src/api/routes/workshop.py`, parent face in `src/api/routes/parent.py`. Parent ownership is derived from `"approved"` run records in `RunStore`.

**Tech Stack:** FastAPI + Jinja2 + HTMX (server-rendered rows), boto3/moto for R2, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-23-story-crud-design.md`

## Global Constraints

- Work happens in the worktree at `.worktrees/story-crud`, branch `story-crud`. Never touch `main`.
- Python 3.12+, ruff format/lint (line length 100), strict mypy on `src/`. Run `make check` before declaring done.
- No comments in code unless required by a reviewer (AGENTS.md rule). Docstrings are fine where neighbors have them.
- Conventional Commits (commitizen-enforced).
- Zero network in tests: moto serves all S3; Clerk sessions are minted locally against a mock JWKS.
- Deletion semantics: ONE destructive action — manifest entry removed AND `published/stories/{id}/` assets deleted. No soft-delete.
- Shared-manifest boundary: deleting a pack removes it from every family's shelf until Phase 2 overlays ship. Do NOT try to fix tenancy here.
- Baseline before starting: pytest 254 passed, vitest 107 passed (verified in this worktree).

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/pipeline/publish.py` | Modify | Add `PublishedStory`, `list_published_stories()`, `list_orphan_story_dirs()` |
| `src/api/routes/workshop.py` | Modify | `GET /workshop/library`, `POST /workshop/stories/{story_id}/delete` |
| `src/api/routes/parent.py` | Modify | `_owned_story_ids()`, `GET /parent/stories`, `POST /parent/stories/{story_id}/delete` |
| `src/templates/workshop/library.html` | Create | Operator library page (grouped rows + orphan section) |
| `src/templates/parent/stories.html` | Create | Parent "your stories" page |
| `src/templates/workshop/dashboard.html` | Modify | Library nav link |
| `src/templates/parent/packs.html` | Modify | Link to `/parent/stories` |
| `tests/pipeline/test_list_published.py` | Create | Unit specs for the listing primitives |
| `tests/api/test_library_routes.py` | Create | Route specs for both faces (moto + local Clerk JWTs) |
| `docs/product.md`, `docs/system-overview.md` | Modify | Wording sync |

---

### Task 1: Listing primitives in publish.py

**Files:**
- Modify: `src/pipeline/publish.py` (add after `delete_staged_story`, end of file)
- Test: `tests/pipeline/test_list_published.py`

**Interfaces:**
- Consumes: existing `_build_client(settings)`, `_load_manifest(client, bucket, language)` in the same file.
- Produces (used by Tasks 2–5):
  - `PublishedStory(BaseModel)` with fields `id: str`, `title: str`, `language: str`, `cover: str`
  - `list_published_stories(settings: Settings, *, client: S3Client | None = None) -> list[PublishedStory]` — sorted by `(language, id)`
  - `list_orphan_story_dirs(settings: Settings, *, client: S3Client | None = None) -> list[str]` — story dir ids under `published/stories/` that no manifest lists

- [ ] **Step 1: Write the failing test**

Create `tests/pipeline/test_list_published.py`:

```python
"""Behavior specs for listing what is published (story CRUD).

The library pages read the bucket, not a database: every manifest under
published/{lang}/manifest.json contributes its story entries, and story
directories no manifest lists are surfaced separately as orphans. All S3
traffic is served by moto's in-memory bucket: zero network.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws
from mypy_boto3_s3 import S3Client

from src.config import Settings
from src.pipeline.publish import list_orphan_story_dirs, list_published_stories

BUCKET = "cantastorie-published"
PUBLIC_BASE = "https://cdn.example.test/published"


@pytest.fixture
def s3() -> Iterator[S3Client]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        staging_dir=tmp_path / "staging",
        r2_bucket=BUCKET,
        r2_public_base=PUBLIC_BASE,
    )


def _put_manifest(s3: S3Client, language: str, stories: list[tuple[str, str]]) -> None:
    entries: list[dict[str, Any]] = [
        {
            "id": story_id,
            "title": title,
            "wash": "wash-barchetta",
            "story": f"{PUBLIC_BASE}/stories/{story_id}/story.json",
            "cover": f"{PUBLIC_BASE}/stories/{story_id}/p1.webp",
        }
        for story_id, title in stories
    ]
    body = json.dumps({"language": language, "prompts": {}, "stories": entries}).encode()
    s3.put_object(Bucket=BUCKET, Key=f"published/{language}/manifest.json", Body=body)


def test_lists_stories_across_languages(tmp_path: Path, s3: S3Client) -> None:
    _put_manifest(s3, "it", [("sea-it-1", "La barchetta")])
    _put_manifest(s3, "es", [("mar-es-1", "El mar")])

    stories = list_published_stories(_settings(tmp_path))

    assert [(s.id, s.language, s.title) for s in stories] == [
        ("mar-es-1", "es", "El mar"),
        ("sea-it-1", "it", "La barchetta"),
    ]


def test_an_empty_bucket_lists_nothing(tmp_path: Path, s3: S3Client) -> None:
    assert list_published_stories(_settings(tmp_path)) == []


def test_flags_directories_no_manifest_lists(tmp_path: Path, s3: S3Client) -> None:
    _put_manifest(s3, "it", [("sea-it-1", "La barchetta")])
    s3.put_object(Bucket=BUCKET, Key="published/stories/ghost/story.json", Body=b"{}")

    assert list_orphan_story_dirs(_settings(tmp_path)) == ["ghost"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_list_published.py -v`
Expected: FAIL — `ImportError: cannot import name 'list_published_stories'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/pipeline/publish.py`:

```python
class PublishedStory(BaseModel):
    id: str
    title: str
    language: str
    cover: str


def list_published_stories(
    settings: Settings,
    *,
    client: S3Client | None = None,
) -> list[PublishedStory]:
    """Every manifest entry across languages, sorted for stable pages."""
    client = client or _build_client(settings)
    bucket = settings.r2_bucket
    stories: list[PublishedStory] = []
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=f"{PUBLISHED_PREFIX}/"
    ):
        for item in page.get("Contents", []):
            key = item["Key"]
            if not key.endswith("/manifest.json"):
                continue
            language = key.removeprefix(f"{PUBLISHED_PREFIX}/").removesuffix("/manifest.json")
            manifest, _ = _load_manifest(client, bucket, language)
            for entry in manifest.get("stories", []):
                stories.append(
                    PublishedStory(
                        id=str(entry.get("id", "")),
                        title=str(entry.get("title", "")),
                        language=language,
                        cover=str(entry.get("cover", "")),
                    )
                )
    stories.sort(key=lambda story: (story.language, story.id))
    return stories


def list_orphan_story_dirs(
    settings: Settings,
    *,
    client: S3Client | None = None,
) -> list[str]:
    """Story directories under published/stories/ that no manifest lists."""
    client = client or _build_client(settings)
    bucket = settings.r2_bucket
    listed: set[str] = set()
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=f"{PUBLISHED_PREFIX}/"
    ):
        for item in page.get("Contents", []):
            if not item["Key"].endswith("/manifest.json"):
                continue
            language = item["Key"].removeprefix(f"{PUBLISHED_PREFIX}/").removesuffix(
                "/manifest.json"
            )
            manifest, _ = _load_manifest(client, bucket, language)
            for entry in manifest.get("stories", []):
                if entry.get("story"):
                    listed.add(str(entry["id"]))
    seen: set[str] = set()
    dirs: list[str] = []
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=f"{PUBLISHED_PREFIX}/stories/"
    ):
        for item in page.get("Contents", []):
            name = item["Key"].removeprefix(f"{PUBLISHED_PREFIX}/stories/")
            parts = name.split("/", 1)
            if len(parts) == 2 and parts[0] not in seen:
                seen.add(parts[0])
                dirs.append(parts[0])
    return [story_id for story_id in dirs if story_id not in listed]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pipeline/test_list_published.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/publish.py tests/pipeline/test_list_published.py
git commit -m "feat(pipeline): list published stories and orphan directories"
```

---

### Task 2: Operator library page (GET /workshop/library)

**Files:**
- Modify: `src/api/routes/workshop.py`
- Modify: `src/templates/workshop/dashboard.html`
- Create: `src/templates/workshop/library.html`
- Test: `tests/api/test_library_routes.py` (create — later tasks append to this file)

**Interfaces:**
- Consumes: `list_published_stories`, `list_orphan_story_dirs` (Task 1); `_scope(request, settings)`, `_to_login()`, `_base_ctx(settings, **extra)`, `WorkshopSettings` already defined in `workshop.py`; test helpers from `tests/api/clerk_jwt.py`.
- Produces: route `GET /workshop/library` rendering `workshop/library.html` with context keys `stories: list[PublishedStory]` (sorted) and `orphans: list[str]`. Template harness class `Harness` in the test file (reused by Tasks 3–5).

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_library_routes.py`:

```python
"""Behavior specs for published-story CRUD routes (operator library + parent deletes).

The operator sees everything live on R2 and can delete any story, bundled
launch content included. Parents see only their own family's approved packs
and get the same single destructive action. All S3 traffic runs on moto;
Clerk sessions are minted locally against a mock JWKS.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws
from mypy_boto3_s3 import S3Client

from src.api import auth as auth_mod
from src.api.auth import SESSION_COOKIE
from src.api.main import create_app
from src.api.routes.workshop import get_run_manager
from src.config import Settings, get_settings
from src.workshop.manager import RunManager
from src.workshop.records import PackRequest, RunRecord, RunStore, new_run
from tests.api.clerk_jwt import (
    clerk_settings,
    generate_rsa_keypair,
    make_mock_fetch,
    mint_token,
    now,
)

BUCKET = "cantastorie-published"
PUBLIC_BASE = "https://cdn.example.test/published"

FAMILY = "a" * 32


@pytest.fixture
def s3() -> Iterator[S3Client]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def _put_manifest(s3: S3Client, language: str, stories: list[tuple[str, str]]) -> None:
    entries: list[dict[str, Any]] = [
        {
            "id": story_id,
            "title": title,
            "wash": "wash-barchetta",
            "story": f"{PUBLIC_BASE}/stories/{story_id}/story.json",
            "cover": f"{PUBLIC_BASE}/stories/{story_id}/p1.webp",
        }
        for story_id, title in stories
    ]
    body = json.dumps({"language": language, "prompts": {}, "stories": entries}).encode()
    s3.put_object(Bucket=BUCKET, Key=f"published/{language}/manifest.json", Body=body)


def _put_assets(s3: S3Client, story_id: str) -> None:
    s3.put_object(Bucket=BUCKET, Key=f"published/stories/{story_id}/story.json", Body=b"{}")
    s3.put_object(Bucket=BUCKET, Key=f"published/stories/{story_id}/p1.webp", Body=b"webp:p1")


def _asset_keys(s3: S3Client, story_id: str) -> list[str]:
    response = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"published/stories/{story_id}/")
    return [item["Key"] for item in response.get("Contents", [])]


def _manifest(s3: S3Client, language: str) -> dict[str, Any]:
    body = s3.get_object(Bucket=BUCKET, Key=f"published/{language}/manifest.json")["Body"].read()
    return dict(json.loads(body))


def _approved_run(store: RunStore, family_token: str, story_ids: list[str]) -> RunRecord:
    record = new_run(family_token, PackRequest(theme="first_snow", language="it", count=1))
    record = record.advance("running").advance("staged").advance("approved", story_ids=story_ids)
    store.save(record)
    return record


class Harness:
    def __init__(self, tmp_path: Path, s3: S3Client) -> None:
        self.key = generate_rsa_keypair()
        auth_mod._fetch_jwks = make_mock_fetch(self.key)  # type: ignore[assignment]
        auth_mod._jwks_state.keys = None
        auth_mod._jwks_state.fetched_at = 0.0
        self.settings = clerk_settings().model_copy(
            update={"r2_bucket": BUCKET, "content_dir": tmp_path / "content"}
        )
        self.store = RunStore(self.settings, client=s3)
        self.manager = RunManager(self.store, self.settings, generate_pack=lambda request, s: [])
        app = create_app()
        app.dependency_overrides[get_settings] = lambda: self.settings
        app.dependency_overrides[get_run_manager] = lambda: self.manager
        self.client = TestClient(app, base_url="https://testserver")

    def sign_in(self, claims: dict[str, Any]) -> None:
        payload = {**claims, "iat": now(), "nbf": now(), "exp": now() + 3600}
        self.client.cookies.set(SESSION_COOKIE, mint_token(self.key, payload))


OPERATOR: dict[str, Any] = {"sub": "user_op", "role": "operator"}
PARENT: dict[str, Any] = {"sub": "user_parent", "family_token": FAMILY}


def test_the_library_lists_every_published_story(tmp_path: Path, s3: S3Client) -> None:
    _put_manifest(s3, "it", [("sea-it-1", "La barchetta"), ("neve-it-1", "Prima neve")])
    _put_manifest(s3, "es", [("mar-es-1", "El mar")])
    s3.put_object(Bucket=BUCKET, Key="published/stories/orphan-1/story.json", Body=b"{}")
    harness = Harness(tmp_path, s3)
    harness.sign_in(OPERATOR)

    page = harness.client.get("/workshop/library")

    assert page.status_code == 200
    assert "La barchetta" in page.text
    assert "Prima neve" in page.text
    assert "El mar" in page.text
    assert "orphan-1" in page.text


def test_the_library_redirects_non_operators_to_parent(tmp_path: Path, s3: S3Client) -> None:
    harness = Harness(tmp_path, s3)
    harness.sign_in(PARENT)

    response = harness.client.get("/workshop/library", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/parent"


def test_the_library_asks_for_sign_in_when_unauthenticated(tmp_path: Path, s3: S3Client) -> None:
    harness = Harness(tmp_path, s3)

    page = harness.client.get("/workshop/library")

    assert page.status_code == 200
    assert 'id="clerk-sign-in"' in page.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_library_routes.py -v`
Expected: FAIL — first test gets 404 (route does not exist)

- [ ] **Step 3: Write minimal implementation**

In `src/api/routes/workshop.py`, extend the existing publish import block:

```python
from src.pipeline.publish import (
    STAGED_PREFIX,
    STORY_FILE,
    _build_client,
    _content_type,
    delete_staged_story,
    list_orphan_story_dirs,
    list_published_stories,
    publish_story,
    unpublish_story,
)
```

Add routes (place near the other GET page routes):

```python
@router.get("/library", response_class=HTMLResponse)
async def library(request: Request, settings: WorkshopSettings) -> Response:
    scope = await _scope(request, settings)
    if scope is None:
        return _to_login()
    stories = sorted(list_published_stories(settings), key=lambda s: (s.language, s.title))
    return templates.TemplateResponse(
        request,
        "workshop/library.html",
        _base_ctx(settings, stories=stories, orphans=list_orphan_story_dirs(settings)),
    )
```

Create `src/templates/workshop/library.html`:

```html
{% extends "workshop/base.html" %}
{% block title %}Library{% endblock %}
{% block content %}
<div class="ws-page">
  <div class="ws-bench-header">
    <div class="ws-wordmark">Workshop</div>
    <div class="ws-tagline">everything published</div>
  </div>

  {% for language, rows in stories | groupby("language") %}
    <div class="ws-section-head">{{ language }}</div>
    {% for story in rows %}
      <div class="ws-run-card" data-story-card="{{ story.id }}">
        <a href="{{ story.cover }}" target="_blank" rel="noopener" class="ws-run-card-link">
          <div class="ws-run-card-body">
            <div class="ws-run-title">{{ story.title }}</div>
            <div class="ws-run-meta">{{ story.id }}</div>
          </div>
        </a>
        <button class="ws-delete-btn"
                hx-post="/workshop/stories/{{ story.id }}/delete"
                hx-target="closest [data-story-card]"
                hx-swap="outerHTML"
                hx-confirm="Delete “{{ story.title }}” forever?">×</button>
      </div>
    {% endfor %}
  {% endfor %}

  {% if orphans %}
    <div class="ws-section-head">Orphans</div>
    {% for orphan in orphans %}
      <div class="ws-card ws-empty-card">
        <div class="ws-empty-title">{{ orphan }}</div>
        <div class="ws-empty-sub">on the bucket but no manifest lists it</div>
      </div>
    {% endfor %}
  {% endif %}
</div>
{% endblock %}
```

Add a nav link in `src/templates/workshop/dashboard.html`, directly under the closing `</div>` of `ws-bench-header`:

```html
  <a href="/workshop/library" class="ws-pill">Library — everything published</a>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_library_routes.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/api/routes/workshop.py src/templates/workshop/library.html src/templates/workshop/dashboard.html tests/api/test_library_routes.py
git commit -m "feat(workshop): operator library lists everything published"
```

---

### Task 3: Operator delete (POST /workshop/stories/{story_id}/delete)

**Files:**
- Modify: `src/api/routes/workshop.py`
- Test: `tests/api/test_library_routes.py` (append)

**Interfaces:**
- Consumes: `unpublish_story(story_id, settings)` (existing), `_scope`/`_to_login` (existing).
- Produces: `POST /workshop/stories/{story_id}/delete` — operator-only; empty `HTMLResponse` on `HX-Request`, else 303 to `/workshop/library`. Non-operator gets 403.

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_library_routes.py`:

```python
def test_an_operator_deletes_any_published_story_forever(tmp_path: Path, s3: S3Client) -> None:
    _put_manifest(s3, "it", [("sea-it-1", "La barchetta")])
    _put_assets(s3, "sea-it-1")
    harness = Harness(tmp_path, s3)
    harness.sign_in(OPERATOR)

    response = harness.client.post(
        "/workshop/stories/sea-it-1/delete",
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert response.text == ""
    assert _manifest(s3, "it")["stories"] == []
    assert _asset_keys(s3, "sea-it-1") == []


def test_a_non_operator_cannot_delete_via_the_workshop(tmp_path: Path, s3: S3Client) -> None:
    _put_manifest(s3, "it", [("sea-it-1", "La barchetta")])
    _put_assets(s3, "sea-it-1")
    harness = Harness(tmp_path, s3)
    harness.sign_in(PARENT)

    response = harness.client.post("/workshop/stories/sea-it-1/delete")

    assert response.status_code == 403
    assert len(_asset_keys(s3, "sea-it-1")) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_library_routes.py -v -k delete`
Expected: FAIL — 404 on POST (route does not exist)

- [ ] **Step 3: Write minimal implementation**

Add to `src/api/routes/workshop.py` (after the `library` route):

```python
@router.post("/stories/{story_id}/delete")
async def delete_published_story(
    request: Request, settings: WorkshopSettings, story_id: str
) -> Response:
    scope = await _scope(request, settings)
    if scope is None:
        return _to_login()
    if not scope.is_operator:
        raise HTTPException(status_code=403)
    unpublish_story(story_id, settings)
    if request.headers.get("HX-Request"):
        return HTMLResponse("")
    return RedirectResponse("/workshop/library", status_code=303)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_library_routes.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/api/routes/workshop.py tests/api/test_library_routes.py
git commit -m "feat(workshop): destructive story delete on the library"
```

---

### Task 4: Parent "your stories" page (GET /parent/stories)

**Files:**
- Modify: `src/api/routes/parent.py`
- Modify: `src/templates/parent/packs.html`
- Create: `src/templates/parent/stories.html`
- Test: `tests/api/test_library_routes.py` (append)

**Interfaces:**
- Consumes: `list_published_stories` (Task 1); `_page_identity`, `_fapi_host`, `Manager` DI alias already in `parent.py`.
- Produces:
  - `_owned_story_ids(manager: RunManager, family_token: str) -> set[str]` — union of `story_ids` over the family's `"approved"` runs (reused by Task 5)
  - `GET /parent/stories` rendering `parent/stories.html` with `stories: list[PublishedStory]` filtered to owned ids

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_library_routes.py`:

```python
def test_a_parent_sees_only_own_approved_packs(tmp_path: Path, s3: S3Client) -> None:
    _put_manifest(s3, "it", [("sea-it-1", "La barchetta"), ("neve-it-1", "Prima neve")])
    harness = Harness(tmp_path, s3)
    _approved_run(harness.store, FAMILY, ["sea-it-1"])
    harness.sign_in(PARENT)

    page = harness.client.get("/parent/stories")

    assert page.status_code == 200
    assert "La barchetta" in page.text
    assert "Prima neve" not in page.text


def test_a_signed_out_parent_gets_the_sign_in_page(tmp_path: Path, s3: S3Client) -> None:
    harness = Harness(tmp_path, s3)

    page = harness.client.get("/parent/stories")

    assert page.status_code == 200
    assert 'id="clerk-sign-in"' in page.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_library_routes.py -v -k parent_sees`
Expected: FAIL — 404 (route does not exist)

- [ ] **Step 3: Write minimal implementation**

In `src/api/routes/parent.py`, extend imports:

```python
from src.pipeline.publish import list_published_stories, unpublish_story
```

(`unpublish_story` is used by Task 5; importing both now avoids touching this block twice.)

Add helper and routes:

```python
def _owned_story_ids(manager: RunManager, family_token: str) -> set[str]:
    return {
        story_id
        for record in manager.store.list_runs(family_token=family_token)
        if record.state == "approved"
        for story_id in record.story_ids
    }


@router.get("/stories", response_class=HTMLResponse)
async def parent_stories(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    manager: Manager,
) -> Response:
    ctx = await _page_identity(request, settings)
    if ctx is not None and ctx.is_operator:
        return RedirectResponse(home_path(True), status_code=303)
    context: dict[str, object] = {
        "fapi_host": _fapi_host(settings),
        "publishable_key": settings.clerk_publishable_key.get_secret_value(),
    }
    if ctx is None:
        return templates.TemplateResponse(request, "parent/signin.html", context)
    if ctx.family_token is None:
        context["onboarding"] = True
        return templates.TemplateResponse(request, "parent/signin.html", context)
    owned = _owned_story_ids(manager, ctx.family_token)
    stories = [s for s in list_published_stories(settings) if s.id in owned]
    return templates.TemplateResponse(
        request, "parent/stories.html", {**context, "stories": stories}
    )
```

Create `src/templates/parent/stories.html`:

```html
{% extends "parent/base.html" %}
{% block title %}Your stories{% endblock %}
{% block content %}
  <h1 class="ws-title">Your stories</h1>

  {% if stories %}
    {% for story in stories %}
      <div class="ws-card" data-story-id="{{ story.id }}">
        <img src="{{ story.cover }}" alt="" width="96" height="96" />
        <div>{{ story.title }}</div>
        <div>{{ story.language }}</div>
        <button class="ws-pill ws-pill-primary"
                hx-post="/parent/stories/{{ story.id }}/delete"
                hx-target="closest [data-story-id]"
                hx-swap="outerHTML"
                hx-confirm="Delete “{{ story.title }}” forever? This cannot be undone.">Delete forever</button>
      </div>
    {% endfor %}
  {% else %}
    <div class="ws-card"><p>No published stories yet.</p></div>
  {% endif %}

  <p><a href="/parent">Back to packs</a></p>
{% endblock %}
```

Add a link in `src/templates/parent/packs.html`, directly under the `<h1>` line:

```html
  <p><a href="/parent/stories">Your published stories</a></p>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_library_routes.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/api/routes/parent.py src/templates/parent/stories.html src/templates/parent/packs.html tests/api/test_library_routes.py
git commit -m "feat(parent): my-stories page lists this family's published packs"
```

---

### Task 5: Parent delete (POST /parent/stories/{story_id}/delete)

**Files:**
- Modify: `src/api/routes/parent.py`
- Test: `tests/api/test_library_routes.py` (append)

**Interfaces:**
- Consumes: `_owned_story_ids` (Task 4), `unpublish_story` (existing).
- Produces: `POST /parent/stories/{story_id}/delete` — requires a provisioned parent session; foreign or unknown story → 404 BEFORE any deletion; empty `HTMLResponse` on `HX-Request`, else 303 to `/parent/stories`.

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_library_routes.py`:

```python
def test_a_parent_deletes_own_story_forever(tmp_path: Path, s3: S3Client) -> None:
    _put_manifest(s3, "it", [("sea-it-1", "La barchetta")])
    _put_assets(s3, "sea-it-1")
    harness = Harness(tmp_path, s3)
    _approved_run(harness.store, FAMILY, ["sea-it-1"])
    harness.sign_in(PARENT)

    response = harness.client.post(
        "/parent/stories/sea-it-1/delete",
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert response.text == ""
    assert _manifest(s3, "it")["stories"] == []
    assert _asset_keys(s3, "sea-it-1") == []


def test_a_parent_cannot_delete_another_familys_story(tmp_path: Path, s3: S3Client) -> None:
    _put_manifest(s3, "it", [("neve-it-1", "Prima neve")])
    _put_assets(s3, "neve-it-1")
    harness = Harness(tmp_path, s3)
    harness.sign_in(PARENT)

    response = harness.client.post("/parent/stories/neve-it-1/delete")

    assert response.status_code == 404
    assert len(_asset_keys(s3, "neve-it-1")) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_library_routes.py -v -k deletes`
Expected: FAIL — 404 on POST (route does not exist)

- [ ] **Step 3: Write minimal implementation**

Add to `src/api/routes/parent.py` (after `parent_stories`):

```python
@router.post("/stories/{story_id}/delete")
async def delete_parent_story(
    request: Request,
    story_id: str,
    ctx: Annotated[ParentContext, Depends(require_parent)],
    settings: Annotated[Settings, Depends(get_settings)],
    manager: Manager,
) -> Response:
    if story_id not in _owned_story_ids(manager, ctx.family_token):
        raise HTTPException(status_code=404)
    unpublish_story(story_id, settings)
    if request.headers.get("HX-Request"):
        return HTMLResponse("")
    return RedirectResponse("/parent/stories", status_code=303)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_library_routes.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/api/routes/parent.py tests/api/test_library_routes.py
git commit -m "feat(parent): destructive delete of own published stories"
```

---

### Task 6: Full verification + docs sync

**Files:**
- Modify: `docs/product.md` (two lines)
- Modify: `docs/system-overview.md` (one insertion)

**Interfaces:**
- Consumes: everything above shipped and green.
- Produces: docs match the built behavior; `make check` + `make test` green.

- [ ] **Step 1: Update product.md**

Replace the feature-table row:

```
| **Parent dashboard** | ⏳ Planned | Language tabs, unpublish toggles, kill switch |
```

with:

```
| **Parent dashboard** | 🔄 In progress | Story rows with a single destructive delete of this family's packs — removes the story from every shelf until family overlays ship; language tabs and kill switch planned |
```

Replace the Screens table row:

```
| **Parent dashboard** | Language tabs, story rows with unpublish toggles, kill switch |
```

with:

```
| **Your stories** | This family's published packs, each with one confirmed destructive delete |
```

- [ ] **Step 2: Update system-overview.md**

At the end of the `## The App (src/api/)` section (before `### Observability`), insert:

```markdown
### Published-story CRUD

The operator library (`GET /workshop/library`, `POST /workshop/stories/{id}/delete`) lists everything published across all language manifests — flagging orphan story directories — and hard-deletes any story, launch content included. Parents get the same single destructive delete scoped to their own approved packs (`GET /parent/stories`, `POST /parent/stories/{id}/delete`). Both faces call `unpublish_story()`; listing comes from `list_published_stories()` and `list_orphan_story_dirs()`, all in [`src/pipeline/publish.py`](../src/pipeline/publish.py).
```

- [ ] **Step 3: Run the whole suite + checks**

Run: `make check && make test`
Expected: lint, format, mypy clean; pytest and vitest fully green (baseline counts plus the new tests).

- [ ] **Step 4: Commit**

```bash
git add docs/product.md docs/system-overview.md
git commit -m "docs: sync story CRUD wording and module map with shipped reality"
```

---

## Self-Review Notes (already applied)

- Spec coverage: listing (T1), operator browse (T2), operator delete incl. launch (T3), parent list w/ ownership filter (T4), parent destructive delete w/ 404 guard (T5), docs impact incl. shared-manifest boundary wording (T6). Orphan flagging (spec) → T2 template + T1 primitive. Error-handling matrix (spec §Error Handling) → covered by T3/T5 tests (403, 404-before-delete) and idempotent no-op of `unpublish_story` for already-deleted stories.
- Type consistency: `PublishedStory(id, title, language, cover)` used identically in T1/T2/T4; `Harness`, `OPERATOR`, `PARENT`, `_put_manifest`, `_put_assets`, `_asset_keys`, `_manifest`, `_approved_run` defined once (T2 file creation) and reused by reference in T3–T5 appends.
- Run-record boundary from the spec ("records untouched; publish-state derives from manifest presence"): satisfied structurally — both pages render from manifests only, never from records; no record writes anywhere in this plan.
