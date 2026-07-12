"""Behavior specs for the operator face at /workshop (AI-388, ADR-005).

The operator retires the terminal: start a run, watch progress, inspect the
staged story, publish — all in the browser. Access is a single env-var secret
held as a session cookie; with no secret configured the routes do not exist.
The tests drive the real RunManager against a moto bucket with an injected
generation seam — zero network, no mocking of the code under test.
"""

import hashlib
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws
from mypy_boto3_s3 import S3Client

from src.api.main import create_app
from src.api.routes.workshop import get_publisher, get_run_manager
from src.config import Settings, get_settings
from src.pipeline.models import Page, PageAudio, Story
from src.pipeline.publish import STAGED_PREFIX
from src.workshop.manager import RunManager
from src.workshop.records import PackRequest, RunStore, new_run

BUCKET = "cantastorie-published"
SECRET = "correct-horse-battery"

SENTENCE = "The water sings shh shh."
PAGE_TEXT = " ".join([SENTENCE] * 8)


@pytest.fixture
def s3() -> Iterator[S3Client]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def _settings(tmp_path: Path, secret: str = SECRET) -> Settings:
    return Settings(
        _env_file=None,
        workshop_secret=secret,
        r2_bucket=BUCKET,
        content_dir=tmp_path / "content",
    )


def _stage_fake_story(
    settings: Settings,
    s3: S3Client,
    story_id: str = "the-sleepy-sea-it-fake0001",
) -> str:
    """Upload a minimal staged story to R2 the way stage_story would."""
    story = Story(
        id=story_id,
        language="it",
        title="La barchetta",
        theme="the_sleepy_sea",
        shape="linear",
        pages=[Page(id="p1", text=PAGE_TEXT, audio=PageAudio(file="p1.mp3"), image="p1.webp")],
    )
    prefix = f"{STAGED_PREFIX}/{story_id}"
    s3.put_object(
        Bucket=BUCKET,
        Key=f"{prefix}/story.json",
        Body=story.model_dump_json().encode(),
        ContentType="application/json",
    )
    s3.put_object(Bucket=BUCKET, Key=f"{prefix}/p1.mp3", Body=b"mp3:p1", ContentType="audio/mpeg")
    s3.put_object(Bucket=BUCKET, Key=f"{prefix}/p1.webp", Body=b"webp:p1", ContentType="image/webp")
    return story_id


def _staged_keys(s3: S3Client, story_id: str) -> list[str]:
    response = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"{STAGED_PREFIX}/{story_id}/")
    return [item["Key"] for item in response.get("Contents", [])]


class _Harness:
    """One workshop app over a moto bucket, with login helpers."""

    def __init__(self, tmp_path: Path, s3: S3Client, secret: str = SECRET) -> None:
        self.settings = _settings(tmp_path, secret)
        self.store = RunStore(self.settings, client=s3)
        self.s3 = s3
        self.published: list[str] = []

        def fake_generate(request: PackRequest, settings: Settings) -> list[str]:
            story_id = f"{request.theme}-{request.language}-fake0001"
            _stage_fake_story(settings, s3, story_id)
            return [f"pending/staged/{story_id}"]

        self.manager = RunManager(self.store, self.settings, generate_pack=fake_generate)
        app = create_app()
        app.dependency_overrides[get_settings] = lambda: self.settings
        app.dependency_overrides[get_run_manager] = lambda: self.manager
        app.dependency_overrides[get_publisher] = lambda: self.published.append
        self.client = TestClient(app, base_url="https://testserver")

    def login(self) -> None:
        response = self.client.post(
            "/workshop/login", data={"secret": SECRET}, follow_redirects=False
        )
        assert response.status_code == 303


def test_workshop_does_not_exist_without_a_configured_secret(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3, secret="")

    assert harness.client.get("/workshop").status_code == 404


def test_unauthenticated_workshop_shows_the_login_form_and_no_runs(
    tmp_path: Path, s3: S3Client
) -> None:
    harness = _Harness(tmp_path, s3)
    harness.store.save(new_run("operator", PackRequest(theme="first_snow", language="it", count=1)))

    page = harness.client.get("/workshop")

    assert page.status_code == 200
    assert 'action="/workshop/login"' in page.text
    assert "first_snow" not in page.text


def test_a_wrong_secret_is_rejected(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)

    response = harness.client.post("/workshop/login", data={"secret": "guess"})

    assert response.status_code == 401


def test_login_sets_a_session_and_shows_the_dashboard(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)

    harness.login()
    page = harness.client.get("/workshop")

    assert page.status_code == 200
    assert 'action="/workshop/runs"' in page.text


def test_the_session_cookie_is_not_the_secret_itself(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)

    harness.login()

    cookie = harness.client.cookies.get("workshop_session")
    assert cookie
    assert SECRET not in cookie
    assert cookie == hashlib.sha256(SECRET.encode()).hexdigest()


def test_login_sets_a_secure_expiring_workshop_scoped_cookie(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)

    response = harness.client.post(
        "/workshop/login", data={"secret": SECRET}, follow_redirects=False
    )

    cookie = response.headers["set-cookie"]
    assert "Max-Age=86400" in cookie
    assert "Path=/workshop" in cookie
    assert "Secure" in cookie


def test_starting_a_run_requires_the_session(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)

    response = harness.client.post(
        "/workshop/runs",
        data={"theme": "first_snow", "language": "it", "count": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/workshop"
    assert harness.store.list_runs() == []


def test_starting_a_run_executes_it_in_the_background_to_staged(
    tmp_path: Path, s3: S3Client
) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()

    response = harness.client.post(
        "/workshop/runs",
        data={"theme": "the_sleepy_sea", "language": "it", "count": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    [record] = harness.store.list_runs()
    assert record.state == "staged"
    assert record.story_ids == ["the_sleepy_sea-it-fake0001"]
    assert response.headers["location"] == f"/workshop/runs/{record.id}"


def test_the_progress_fragment_reports_the_run_state(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    record = new_run("operator", PackRequest(theme="first_snow", language="it", count=1))
    harness.store.save(record.advance("running"))

    fragment = harness.client.get(f"/workshop/runs/{record.id}/progress")

    assert fragment.status_code == 200
    assert "running" in fragment.text
    assert "hx-get" in fragment.text


def test_a_settled_run_stops_polling_and_links_its_stories(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    story_id = _stage_fake_story(harness.settings, s3)
    record = new_run("operator", PackRequest(theme="the_sleepy_sea", language="it", count=1))
    harness.store.save(record.advance("running").advance("staged", story_ids=[story_id]))

    fragment = harness.client.get(f"/workshop/runs/{record.id}/progress")

    assert "hx-get" not in fragment.text
    assert f"/workshop/staged/{story_id}" in fragment.text


def test_the_staged_story_page_shows_text_audio_and_images(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    story_id = _stage_fake_story(harness.settings, s3)

    page = harness.client.get(f"/workshop/staged/{story_id}")

    assert page.status_code == 200
    assert SENTENCE in page.text
    assert f"/workshop/staged/{story_id}/assets/p1.mp3" in page.text
    assert f"/workshop/staged/{story_id}/assets/p1.webp" in page.text


def test_the_staged_story_page_shows_delete_when_the_run_is_settled(
    tmp_path: Path, s3: S3Client
) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    story_id = _stage_fake_story(harness.settings, s3)
    record = new_run("operator", PackRequest(theme="the_sleepy_sea", language="it", count=1))
    harness.store.save(record.advance("running").advance("staged", story_ids=[story_id]))

    page = harness.client.get(f"/workshop/staged/{story_id}")

    assert 'hx-post="/workshop/staged/' in page.text
    assert "Delete this story" in page.text


def test_staged_assets_are_served_and_traversal_is_blocked(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    story_id = _stage_fake_story(harness.settings, s3)

    assert harness.client.get(f"/workshop/staged/{story_id}/assets/p1.mp3").content == b"mp3:p1"
    escape = harness.client.get(f"/workshop/staged/{story_id}/assets/../../secret.txt")
    assert escape.status_code in (400, 404)


def test_approving_a_staged_run_publishes_its_stories_and_settles_the_record(
    tmp_path: Path, s3: S3Client
) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    story_id = _stage_fake_story(harness.settings, s3)
    record = new_run("operator", PackRequest(theme="the_sleepy_sea", language="it", count=1))
    harness.store.save(record.advance("running").advance("staged", story_ids=[story_id]))

    response = harness.client.post(f"/workshop/runs/{record.id}/approve", follow_redirects=False)

    assert response.status_code == 303
    assert harness.published == [story_id]
    reloaded = harness.store.load("operator", record.id)
    assert reloaded is not None
    assert reloaded.state == "approved"


def test_approving_a_non_staged_run_is_rejected_without_publishing(
    tmp_path: Path, s3: S3Client
) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    story_id = _stage_fake_story(harness.settings, s3)
    record = new_run("operator", PackRequest(theme="the_sleepy_sea", language="it", count=1))
    failed = record.advance("running").advance("failed", story_ids=[story_id])
    harness.store.save(failed)

    response = harness.client.post(f"/workshop/runs/{record.id}/approve", follow_redirects=False)

    assert response.status_code == 400
    assert response.json() == {"detail": "Run is in failed state, must be staged to approve"}
    assert harness.published == []
    assert harness.store.load("operator", record.id) == failed


def test_deleting_a_run_requires_the_session(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)
    record = new_run("operator", PackRequest(theme="first_snow", language="it", count=1))
    harness.store.save(record)

    response = harness.client.post(f"/workshop/runs/{record.id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/workshop"
    assert harness.store.load("operator", record.id) is not None


def test_deleting_a_staged_story_removes_its_artifacts_and_updates_its_run(
    tmp_path: Path, s3: S3Client
) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    story_id = _stage_fake_story(harness.settings, s3)
    content_dir = harness.settings.content_dir / story_id
    content_dir.mkdir(parents=True)
    (content_dir / "checkpoint.json").write_text("checkpoint")
    record = new_run("operator", PackRequest(theme="the_sleepy_sea", language="it", count=1))
    staged = record.advance("running").advance("staged", story_ids=[story_id])
    harness.store.save(staged)

    response = harness.client.post(
        f"/workshop/staged/{story_id}/delete",
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.text == ""
    assert _staged_keys(s3, story_id) == []
    assert not content_dir.exists()
    reloaded = harness.store.load("operator", record.id)
    assert reloaded is not None
    assert reloaded.story_ids == []


def test_deleting_a_staged_story_keeps_the_run_record_when_it_is_the_last_story(
    tmp_path: Path, s3: S3Client
) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    story_id = _stage_fake_story(harness.settings, s3)
    record = new_run("operator", PackRequest(theme="the_sleepy_sea", language="it", count=1))
    staged = record.advance("running").advance("staged", story_ids=[story_id])
    harness.store.save(staged)

    response = harness.client.post(f"/workshop/staged/{story_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    reloaded = harness.store.load("operator", record.id)
    assert reloaded is not None
    assert reloaded.story_ids == []


@pytest.mark.parametrize("state", ["queued", "running", "approved"])
def test_deleting_a_story_from_a_protected_run_is_rejected(
    tmp_path: Path, s3: S3Client, state: str
) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    story_id = _stage_fake_story(harness.settings, s3)
    record = new_run("operator", PackRequest(theme="the_sleepy_sea", language="it", count=1))
    if state == "queued":
        protected = record.model_copy(update={"story_ids": [story_id]})
    elif state == "running":
        protected = record.advance("running", story_ids=[story_id])
    else:
        protected = (
            record.advance("running").advance("staged", story_ids=[story_id]).advance("approved")
        )
    harness.store.save(protected)

    response = harness.client.post(f"/workshop/staged/{story_id}/delete", follow_redirects=False)

    assert response.status_code == 400
    assert _staged_keys(s3, story_id)
    assert harness.store.load("operator", record.id) == protected


def test_deleting_an_unknown_staged_story_returns_not_found(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()

    response = harness.client.post("/workshop/staged/no-such-story/delete", follow_redirects=False)

    assert response.status_code == 404


def test_deleting_a_failed_story_redirects_for_non_htmx_requests(
    tmp_path: Path, s3: S3Client
) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    story_id = _stage_fake_story(harness.settings, s3)
    record = new_run("operator", PackRequest(theme="the_sleepy_sea", language="it", count=1))
    failed = record.advance("running").advance("failed", story_ids=[story_id])
    harness.store.save(failed)

    response = harness.client.post(f"/workshop/staged/{story_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/workshop"
    reloaded = harness.store.load("operator", record.id)
    assert reloaded is not None
    assert reloaded.story_ids == []


def test_deleting_an_unknown_run_returns_not_found(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()

    response = harness.client.post("/workshop/runs/no-such-run/delete", follow_redirects=False)

    assert response.status_code == 404


def test_deleting_a_live_run_is_rejected(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    record = new_run("operator", PackRequest(theme="first_snow", language="it", count=1)).advance(
        "running"
    )
    harness.store.save(record)

    response = harness.client.post(f"/workshop/runs/{record.id}/delete", follow_redirects=False)

    assert response.status_code == 400
    assert harness.store.load("operator", record.id) == record


def test_deleting_an_approved_run_cleans_its_artifacts_and_unpublishes(
    tmp_path: Path, s3: S3Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
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
        f"/workshop/runs/{record.id}/delete",
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.text == ""
    assert unpublished == [story_id]
    assert _staged_keys(s3, story_id) == []
    assert not content_dir.exists()
    assert harness.store.load("operator", record.id) is None


def test_deleting_a_run_does_not_remove_shared_story_artifacts(
    tmp_path: Path, s3: S3Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    story_id = _stage_fake_story(harness.settings, s3)
    content_dir = harness.settings.content_dir / story_id
    content_dir.mkdir(parents=True)
    (content_dir / "checkpoint.json").write_text("checkpoint")
    request = PackRequest(theme="the_sleepy_sea", language="it", count=1)
    deleted = (
        new_run("operator", request)
        .advance("running")
        .advance("staged", story_ids=[story_id])
        .advance("approved")
    )
    shared = (
        new_run("operator", request)
        .advance("running")
        .advance("staged", story_ids=[story_id])
        .advance("approved")
    )
    harness.store.save(deleted)
    harness.store.save(shared)
    unpublished: list[str] = []
    monkeypatch.setattr(
        "src.api.routes.workshop.unpublish_story",
        lambda story_id, settings: unpublished.append(story_id),
    )

    response = harness.client.post(
        f"/workshop/runs/{deleted.id}/delete",
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.text == ""
    assert unpublished == []
    assert _staged_keys(s3, story_id)
    assert content_dir.is_dir()
    assert harness.store.load("operator", deleted.id) is None
    assert harness.store.load("operator", shared.id) == shared


def test_settled_runs_show_confirmed_delete_controls_on_dashboard_and_progress(
    tmp_path: Path, s3: S3Client
) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    record = new_run("operator", PackRequest(theme="first_snow", language="it", count=1))
    settled = record.advance("running").advance("staged")
    harness.store.save(settled)

    dashboard = harness.client.get("/workshop")
    progress = harness.client.get(f"/workshop/runs/{record.id}/progress")

    action = f'hx-post="/workshop/runs/{record.id}/delete"'
    confirmation = 'hx-confirm="Delete this run and all its artifacts?"'
    assert action in dashboard.text
    assert confirmation in dashboard.text
    assert 'hx-target="closest tr"' in dashboard.text
    assert 'hx-swap="outerHTML"' in dashboard.text
    assert action in progress.text
    assert confirmation in progress.text
    assert 'hx-target="closest #run-progress"' in progress.text
    assert 'hx-swap="outerHTML"' in progress.text


def test_live_runs_hide_the_delete_control_from_progress(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    record = new_run("operator", PackRequest(theme="first_snow", language="it", count=1)).advance(
        "running"
    )
    harness.store.save(record)

    progress = harness.client.get(f"/workshop/runs/{record.id}/progress")

    assert f'hx-post="/workshop/runs/{record.id}/delete"' not in progress.text


def test_live_runs_hide_the_delete_control_on_dashboard(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    record = new_run("operator", PackRequest(theme="first_snow", language="it", count=1)).advance(
        "running"
    )
    harness.store.save(record)

    dashboard = harness.client.get("/workshop")

    assert f'hx-post="/workshop/runs/{record.id}/delete"' not in dashboard.text


def test_delete_route_returns_empty_for_htmx_requests(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    record = (
        new_run("operator", PackRequest(theme="first_snow", language="it", count=1))
        .advance("running")
        .advance("staged")
    )
    harness.store.save(record)

    response = harness.client.post(
        f"/workshop/runs/{record.id}/delete",
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.text == ""


def test_delete_route_redirects_for_non_htmx_requests(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    record = (
        new_run("operator", PackRequest(theme="first_snow", language="it", count=1))
        .advance("running")
        .advance("staged")
    )
    harness.store.save(record)

    response = harness.client.post(f"/workshop/runs/{record.id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/workshop"
