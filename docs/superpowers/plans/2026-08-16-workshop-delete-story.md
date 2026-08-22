# Workshop Delete Story + Assets — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator delete an individual story and its assets from the workshop for any settled run state — extending the existing single-story delete route to handle `approved` (unpublish from R2 + remove from manifest) and `rejected` (already-staged assets), and surfacing the control in the UI.

**Architecture:** Reuse the existing `POST /workshop/staged/{story_id}/delete` route. Relax its state guard from `{staged, failed}` to `{staged, failed, rejected, approved}`. For `approved`, call the already-tested `unpublish_story()` (removes the manifest entry + `published/stories/{id}/` assets) before the existing `delete_staged_story()` (pending bucket) + local `shutil.rmtree` + record update. Templates: show the delete control on approved story pages ("Remove from shelf"), and render story links on approved run pages so those stories are reachable. No new endpoints, no new storage.

**Tech Stack:** FastAPI (Jinja2 + HTMX, server-rendered), pytest + moto (fake S3), the existing `_Harness` test fixture.

## Global Constraints

- Code style: Python 3.12+, Ruff, mypy strict. Line length 100. No comments unless explicitly requested. Follow existing patterns in `src/`.
- Never suppress type errors (`as any`, `@ts-ignore`). Never commit secrets. `.env` is gitignored.
- Conventional commits enforced via pre-commit (commitizen). Pre-commit runs ruff, ruff-format, mypy, detect-secrets, end-of-file-fixer, trailing-whitespace.
- Tests: providers mocked (moto for S3); no network in unit tests. The existing `_Harness` fixture in `tests/workshop/test_routes.py` provides `sign_in()`, `settings`, `store`, `client`, `published`, and the `s3` pytest fixture provides a moto bucket. Reuse `_stage_fake_story(s3, settings)` and `_staged_keys(s3, story_id)`.
- Run `make check` and `make test` before declaring work done on the branch.
- All changes live on branch `feat/workshop-delete-story` in the worktree at `.worktrees/workshop-delete-story`.

---

### Task 1: Allow approved + rejected states in the single-story delete route (TDD)

**Files:**
- Modify: `src/api/routes/workshop.py:389-411` (`delete_staged_story_route`)
- Test: `tests/workshop/test_routes.py`

**Interfaces:**
- Consumes: `unpublish_story(story_id, settings)`, `delete_staged_story(story_id, settings)`, `RunRecord.advance`, `RunManager`, `_story_record_or_404(manager, story_id)`, `LIVE_STATES`, `shutil.rmtree`, `settings.content_dir`
- Produces: same route signature unchanged. New visible behavior: `approved`/`rejected` records now succeed; `approved` triggers `unpublish_story`.

- [ ] **Step 1: Write the failing tests** — append to `tests/workshop/test_routes.py`, after `test_deleting_a_staged_story_keeps_the_run_record_when_it_is_the_last_story`:

```python
def test_deleting_an_approved_story_unpublishes_cleans_artifacts_and_updates_run(
    tmp_path: Path, s3: S3Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _Harness(tmp_path, s3)
    harness.sign_in()
    story_id = _stage_fake_story(harness.settings, s3)
    content_dir = harness.settings.content_dir / story_id
    content_dir.mkdir(parents=True)
    (content_dir / "checkpoint.json").write_text("checkpoint")
    record = new_run("operator", PackRequest(theme="the_sleepy_sea", language="it", count=1))
    approved = record.advance("running").advance("staged", story_ids=[story_id]).advance("approved")
    harness.store.save(approved)
    unpublished: list[str] = []
    monkeypatch.setattr(
        "src.api.routes.workshop.unpublish_story",
        lambda story_id, settings: unpublished.append(story_id),
    )

    response = harness.client.post(
        f"/workshop/staged/{story_id}/delete",
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.text == ""
    assert unpublished == [story_id]
    assert _staged_keys(s3, story_id) == []
    assert not content_dir.exists()
    reloaded = harness.store.load("operator", record.id)
    assert reloaded is not None
    assert reloaded.state == "approved"
    assert reloaded.story_ids == []


def test_deleting_a_rejected_story_cleans_artifacts_without_unpublish(
    tmp_path: Path, s3: S3Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _Harness(tmp_path, s3)
    harness.sign_in()
    story_id = _stage_fake_story(harness.settings, s3)
    content_dir = harness.settings.content_dir / story_id
    content_dir.mkdir(parents=True)
    (content_dir / "checkpoint.json").write_text("checkpoint")
    record = new_run("operator", PackRequest(theme="the_sleepy_sea", language="it", count=1))
    rejected = record.advance("running").advance("staged", story_ids=[story_id]).advance("rejected")
    harness.store.save(rejected)
    unpublished: list[str] = []
    monkeypatch.setattr(
        "src.api.routes.workshop.unpublish_story",
        lambda story_id, settings: unpublished.append(story_id),
    )

    response = harness.client.post(
        f"/workshop/staged/{story_id}/delete",
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.text == ""
    assert unpublished == []
    assert _staged_keys(s3, story_id) == []
    assert not content_dir.exists()
    reloaded = harness.store.load("operator", record.id)
    assert reloaded is not None
    assert reloaded.story_ids == []
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/workshop/test_routes.py::test_deleting_an_approved_story_unpublishes_cleans_artifacts_and_updates_run tests/workshop/test_routes.py::test_deleting_a_rejected_story_cleans_artifacts_without_unpublish -v`
Expected: FAIL (currently `approved`/`rejected` return 400: "Run is in approved state..." / the guard `record.state not in {"staged", "failed"}`).

- [ ] **Step 3: Relax the guard and add the approved branch** — replace `src/api/routes/workshop.py` lines 398-408:

```python
    record = _story_record_or_404(manager, story_id)
    if record.state in LIVE_STATES:
        raise HTTPException(status_code=400)
    if record.state not in {"staged", "failed", "rejected", "approved"}:
        raise HTTPException(status_code=400)
    if record.state == "approved":
        unpublish_story(story_id, settings)
    delete_staged_story(story_id, settings)
    shutil.rmtree(settings.content_dir / story_id, ignore_errors=True)
    updated = record.model_copy(
        update={"story_ids": [s for s in record.story_ids if s != story_id]}
    )
    manager.store.save(updated)
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/workshop/test_routes.py::test_deleting_an_approved_story_unpublishes_cleans_artifacts_and_updates_run tests/workshop/test_routes.py::test_deleting_a_rejected_story_cleans_artifacts_without_unpublish -v`
Expected: PASS.

- [ ] **Step 5: Run the whole workshop route suite + typecheck**

Run: `uv run pytest tests/workshop/ && uv run mypy src/`
Expected: all pass, mypy clean.

- [ ] **Step 6: Commit**

```bash
git add src/api/routes/workshop.py tests/workshop/test_routes.py
git commit -m "feat(workshop): allow per-story delete for approved and rejected runs"
```

---

### Task 2: Show "Remove from shelf" on approved story pages

**Files:**
- Modify: `src/templates/workshop/story.html:60-64` (the delete-form block)
- Test: `tests/workshop/test_routes.py`

**Interfaces:**
- Consumes: same route from Task 1; the story page already receives `record` and `live`.
- Produces: approved story pages now render an armed delete control labeled "Remove from shelf"; rejected/failed/staged keep "Delete this story".

- [ ] **Step 1: Write the failing test** — append to `tests/workshop/test_routes.py`:

```python
def test_the_staged_story_page_shows_remove_from_shelf_when_approved(
    tmp_path: Path, s3: S3Client
) -> None:
    harness = _Harness(tmp_path, s3)
    harness.sign_in()
    story_id = _stage_fake_story(harness.settings, s3)
    record = new_run("operator", PackRequest(theme="the_sleepy_sea", language="it", count=1))
    approved = record.advance("running").advance("staged", story_ids=[story_id]).advance("approved")
    harness.store.save(approved)

    page = harness.client.get(f"/workshop/staged/{story_id}?run={record.id}")

    assert f'action="/workshop/staged/{story_id}/delete"' in page.text
    assert "data-delete-btn" in page.text
    assert "Remove from shelf" in page.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/workshop/test_routes.py::test_the_staged_story_page_shows_remove_from_shelf_when_approved -v`
Expected: FAIL (current template excludes `approved`, so no delete control).

- [ ] **Step 3: Change the template guard** — in `src/templates/workshop/story.html`, replace lines 60-64:

```jinja
  {% if record and record.state not in live and record.state not in ["approved"] %}
    <form method="post" action="/workshop/staged/{{ story.id }}/delete" class="ws-delete-story-row">
      <button type="submit" class="ws-delete-story-link" data-delete-btn>× Delete this story</button>
    </form>
  {% endif %}
```

with:

```jinja
  {% if record and record.state not in live %}
    <form method="post" action="/workshop/staged/{{ story.id }}/delete" class="ws-delete-story-row">
      <button type="submit" class="ws-delete-story-link" data-delete-btn>{% if record.state == "approved" %}× Remove from shelf{% else %}× Delete this story{% endif %}</button>
    </form>
  {% endif %}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/workshop/test_routes.py::test_the_staged_story_page_shows_remove_from_shelf_when_approved -v`
Expected: PASS.

- [ ] **Step 5: Run the existing story-page test to confirm no regression**

Run: `uv run pytest tests/workshop/test_routes.py::test_the_staged_story_page_shows_delete_when_the_run_is_settled -v`
Expected: PASS (staged still shows "Delete this story").

- [ ] **Step 6: Commit**

```bash
git add src/templates/workshop/story.html tests/workshop/test_routes.py
git commit -m "feat(workshop): show Remove from shelf on approved story pages"
```

---

### Task 3: Link approved stories on the run page so they are reachable

**Files:**
- Modify: `src/templates/workshop/_progress.html:65-75` (operator controls block)
- Test: `tests/workshop/test_routes.py`

**Interfaces:**
- Consumes: `record`, `staged_stories` (list of `{id, title, page_count}`), `live` — already passed to `_progress.html` by `run_page` and `run_progress`.
- Produces: approved runs render story links with `data-testid="review-link"` linking to `/workshop/staged/{id}?run={record.id}`.

- [ ] **Step 1: Write the failing test** — append to `tests/workshop/test_routes.py`:

```python
def test_approved_run_progress_links_its_published_stories(
    tmp_path: Path, s3: S3Client
) -> None:
    harness = _Harness(tmp_path, s3)
    harness.sign_in()
    story_id = _stage_fake_story(harness.settings, s3)
    record = new_run("operator", PackRequest(theme="the_sleepy_sea", language="it", count=1))
    approved = record.advance("running").advance("staged", story_ids=[story_id]).advance("approved")
    harness.store.save(approved)

    progress = harness.client.get(f"/workshop/runs/{record.id}/progress")

    assert f'href="/workshop/staged/{story_id}?run={record.id}"' in progress.text
    assert 'data-testid="review-link"' in progress.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/workshop/test_routes.py::test_approved_run_progress_links_its_published_stories -v`
Expected: FAIL (current template only renders review pills for `state == "staged"`).

- [ ] **Step 3: Add approved story links in the operator block** — in `src/templates/workshop/_progress.html`, replace lines 65-75:

```jinja
    {% if is_operator %}
      {% if record.state == "staged" and staged_stories %}
        <div class="ws-review-pills">
          {% for story in staged_stories %}
            <a href="/workshop/staged/{{ story.id }}?run={{ record.id }}" class="ws-pill ws-pill-confirm" data-testid="review-link">Review {{ story.page_count }} page{{ "" if story.page_count == 1 else "s" }}</a>
          {% endfor %}
        </div>
      {% endif %}
    {% elif record.state == "staged" %}
      <div class="ws-progress-sub">Your stories are made — they'll appear on the shelf after review.</div>
    {% endif %}
```

with:

```jinja
    {% if is_operator %}
      {% if staged_stories %}
        <div class="ws-review-pills">
          {% for story in staged_stories %}
            <a href="/workshop/staged/{{ story.id }}?run={{ record.id }}" class="ws-pill ws-pill-confirm" data-testid="review-link">{% if record.state == "approved" %}On the shelf — {{ story.page_count }} page{{ "" if story.page_count == 1 else "s" }}{% else %}Review {{ story.page_count }} page{{ "" if story.page_count == 1 else "s" }}{% endif %}</a>
          {% endfor %}
        </div>
      {% endif %}
    {% elif record.state == "staged" %}
      <div class="ws-progress-sub">Your stories are made — they'll appear on the shelf after review.</div>
    {% endif %}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/workshop/test_routes.py::test_approved_run_progress_links_its_published_stories -v`
Expected: PASS.

- [ ] **Step 5: Run the broader route + playtest the live-run guard**

Run: `uv run pytest tests/workshop/ && uv run pytest tests/workshop/test_routes.py::test_live_runs_hide_the_delete_control_from_progress tests/workshop/test_routes.py::test_settled_runs_show_armed_delete_controls_on_dashboard_and_progress -v`
Expected: all PASS (live runs still show no story links; staged pills unchanged in behavior).

- [ ] **Step 6: Commit**

```bash
git add src/templates/workshop/_progress.html tests/workshop/test_routes.py
git commit -m "feat(workshop): link approved stories on the run page"
```

---

### Task 4: Full verification and lint gate

**Files:** (no new files)

- [ ] **Step 1: Run the complete test suite**

Run: `make test`
Expected: 248 pytest + 107 vitest pass (existing) plus the 5 new python tests.

- [ ] **Step 2: Run the lint/format/typecheck gate**

Run: `make check`
Expected: ruff, ruff-format, mypy, detect-secrets all clean (pre-commit would also catch end-of-file-fixer / trailing-whitespace).

- [ ] **Step 3: Confirm the spec's intended asset cleanup end-to-end via the route** — a quick manual reasoning check (no code): for an approved story, `DELETE /workshop/staged/{id}/delete` now (a) calls `unpublish_story` → manifest entry removed + `published/stories/{id}/` deleted; (b) `delete_staged_story` → `pending/staged/{id}/` deleted; (c) `shutil.rmtree(content/{id})`; (d) record's `story_ids` updated, state preserved. This matches the spec's "Assets deleted for an approved story" list.

- [ ] **Step 4: Do not commit further (verification only).** If any check failed, stop and report before proceeding.

---

## Self-Review Notes

- **Spec coverage:** Route guard relaxation + approved/rejected branches (Task 1) ↔ spec "Route change". Approved story-page control (Task 2) ↔ spec template `story.html`. Approved run-page links (Task 3) ↔ spec template `_progress.html`. Asset cleanup order matches spec. Out-of-scope items (Approach B published section, parent-area toggles, prompt cleanup) intentionally absent.
- **Placeholder scan:** Every step has concrete code or exact test assertions; no TBD/TODO.
- **Type consistency:** `unpublish_story(story_id, settings)` and `delete_staged_story(story_id, settings)` signatures match their imports in `workshop.py` (lines 38-41). `_staged_keys(s3, story_id)` and `_stage_fake_story(s3, settings)` reused verbatim from the existing test module. `harness.published` and `monkeypatch.setattr("src.api.routes.workshop.unpublish_story", ...)` match the existing `test_deleting_an_approved_run_...` test.
