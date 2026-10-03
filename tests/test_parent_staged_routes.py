"""Parent routes added by the overhaul: staged assets, reject, the Being-made
run row's polling contract, and the language filter."""

from __future__ import annotations

import io
from typing import Any

import pytest

import src.api.routes.parent as parent_module
from src.pipeline.publish import PublishedStory
from src.workshop.records import RunRecord, StoryRequest, new_run
from tests.test_parent_redesign import (
    VALID_TOKEN,
    _FakeManager,
    _make_client,
    _make_queued_run,
    reset_jwks_cache,  # noqa: F401 — autouse: fresh JWKS per test
)

OTHER_TOKEN = "ffffffffffffffffffffffffffffffff"  # pragma: allowlist secret


def _staged_run(family_token: str = VALID_TOKEN, story_id: str = "story-abc") -> RunRecord:
    run = new_run(family_token, StoryRequest(theme="the_sleepy_sea", language="it"))
    return run.advance("running").advance("staged", story_ids=[story_id])


class _FakeS3:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.keys.append(Key)
        return {"Body": io.BytesIO(b"RIFF-bytes")}


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> _FakeS3:
    fake = _FakeS3()
    monkeypatch.setattr(parent_module, "_build_client", lambda settings: fake)
    return fake


# ── Staged assets: family-scoped, no traversal ────────────────────────────────


def test_own_staged_asset_is_served_privately(monkeypatch: pytest.MonkeyPatch, s3: _FakeS3) -> None:
    run = _staged_run()
    client = _make_client(monkeypatch, _FakeManager({run.id: run}))
    r = client.get("/parent/staged/story-abc/assets/p1.wav")
    assert r.status_code == 200
    assert r.content == b"RIFF-bytes"
    # Content-hashed names never change bytes: cache privately, don't refetch.
    assert r.headers["cache-control"] == "private, max-age=86400, immutable"
    assert s3.keys == ["pending/staged/story-abc/p1.wav"]


class _CountingManager(_FakeManager):
    """Counts full-run scans, the cost ?run= exists to avoid."""

    def __init__(self, runs: dict[str, RunRecord]) -> None:
        super().__init__(runs)
        self.scans = 0
        store_list = self.store.list_runs

        def counted(**kwargs: Any) -> list[RunRecord]:
            self.scans += 1
            return store_list(**kwargs)

        self.store.list_runs = counted  # type: ignore[method-assign]


def test_asset_with_run_param_checks_ownership_without_scanning_runs(
    monkeypatch: pytest.MonkeyPatch, s3: _FakeS3
) -> None:
    run = _staged_run()
    manager = _CountingManager({run.id: run})
    client = _make_client(monkeypatch, manager)
    r = client.get(f"/parent/staged/story-abc/assets/p1.wav?run={run.id}")
    assert r.status_code == 200
    assert manager.scans == 0


def test_asset_run_param_cannot_borrow_another_familys_run(
    monkeypatch: pytest.MonkeyPatch, s3: _FakeS3
) -> None:
    """?run= names a run, but load() is scoped to the session's family."""
    theirs = _staged_run(family_token=OTHER_TOKEN)
    client = _make_client(monkeypatch, _FakeManager({theirs.id: theirs}))
    r = client.get(f"/parent/staged/story-abc/assets/p1.wav?run={theirs.id}")
    assert r.status_code == 404
    assert s3.keys == []


def test_asset_run_param_must_contain_the_story(
    monkeypatch: pytest.MonkeyPatch, s3: _FakeS3
) -> None:
    """Own run, wrong story: a run id can't unlock a story it didn't make."""
    run = _staged_run(story_id="story-abc")
    client = _make_client(monkeypatch, _FakeManager({run.id: run}))
    r = client.get(f"/parent/staged/story-xyz/assets/p1.wav?run={run.id}")
    assert r.status_code == 404
    assert s3.keys == []


def test_another_familys_staged_asset_is_404_and_never_fetched(
    monkeypatch: pytest.MonkeyPatch, s3: _FakeS3
) -> None:
    theirs = _staged_run(family_token=OTHER_TOKEN)
    client = _make_client(monkeypatch, _FakeManager({theirs.id: theirs}))
    assert client.get("/parent/staged/story-abc/assets/p1.wav").status_code == 404
    assert s3.keys == []


# (A bare ".." never reaches this route: the URL normalizes to the story page.)
@pytest.mark.parametrize("name", ["..p1.wav", "p1..wav"])
def test_dotdot_asset_names_are_refused(
    monkeypatch: pytest.MonkeyPatch, s3: _FakeS3, name: str
) -> None:
    run = _staged_run()
    client = _make_client(monkeypatch, _FakeManager({run.id: run}))
    assert client.get(f"/parent/staged/story-abc/assets/{name}").status_code == 404
    assert s3.keys == []


# ── Reject ────────────────────────────────────────────────────────────────────


def test_reject_own_staged_run(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _staged_run()
    manager = _FakeManager({run.id: run})
    client = _make_client(monkeypatch, manager)
    r = client.post(f"/parent/packs/{run.id}/reject", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/parent"
    assert manager.store.load(VALID_TOKEN, run.id).state == "rejected"


def test_reject_another_familys_run_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    theirs = _staged_run(family_token=OTHER_TOKEN)
    manager = _FakeManager({theirs.id: theirs})
    client = _make_client(monkeypatch, manager)
    assert client.post(f"/parent/packs/{theirs.id}/reject").status_code == 404
    assert manager.store._runs[theirs.id].state == "staged"


def test_reject_a_run_that_is_not_staged_is_400(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _make_queued_run()
    client = _make_client(monkeypatch, _FakeManager({run.id: run}))
    assert client.post(f"/parent/packs/{run.id}/reject").status_code == 400


# ── Being-made row: polls while live, stops once settled ─────────────────────


@pytest.mark.parametrize(
    ("states", "polls"),
    [
        ((), True),  # queued
        (("running",), True),
        (("running", "staged"), False),
        (("running", "failed"), False),
    ],
)
def test_run_row_polls_only_while_live(
    monkeypatch: pytest.MonkeyPatch, s3: _FakeS3, states: tuple[str, ...], polls: bool
) -> None:
    run = new_run(VALID_TOKEN, StoryRequest(theme="the_sleepy_sea", language="it"))
    for state in states:
        run = run.advance(state, story_ids=[] if state == "staged" else None)  # type: ignore[arg-type]
    monkeypatch.setattr(parent_module, "_checkpointed_steps", lambda record, settings: set())
    client = _make_client(monkeypatch, _FakeManager({run.id: run}))
    text = client.get(f"/parent/packs/{run.id}/progress").text
    assert ('hx-trigger="every 2s"' in text) is polls


# ── Language filter ───────────────────────────────────────────────────────────


def test_language_filter_shows_only_that_language(monkeypatch: pytest.MonkeyPatch) -> None:
    runs: dict[str, RunRecord] = {}
    stories = []
    for story_id, lang in (("it-story", "it"), ("en-story", "en")):
        run = _staged_run(story_id=story_id).advance("approved")
        runs[run.id] = run
        stories.append(
            PublishedStory(
                id=story_id, title=story_id, language=lang, cover="", family_token=VALID_TOKEN
            )
        )
    client = _make_client(monkeypatch, _FakeManager(runs), stories=stories)
    text = client.get("/parent/stories?lang=en").text
    assert 'data-story-id="en-story"' in text
    assert 'data-story-id="it-story"' not in text
