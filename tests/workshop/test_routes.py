"""Behavior specs for the operator face at /workshop (AI-388, ADR-004).

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
        staging_dir=tmp_path / "staging",
        content_dir=tmp_path / "content",
    )


def _stage_fake_story(settings: Settings, story_id: str = "the-sleepy-sea-it-fake0001") -> str:
    """Lay a minimal staged story on disk the way stage_story would."""
    story_dir = settings.staging_dir / story_id
    story_dir.mkdir(parents=True)
    (story_dir / "p1.mp3").write_bytes(b"mp3:p1")
    (story_dir / "p1.webp").write_bytes(b"webp:p1")
    story = Story(
        id=story_id,
        language="it",
        title="La barchetta",
        theme="the_sleepy_sea",
        shape="linear",
        pages=[Page(id="p1", text=PAGE_TEXT, audio=PageAudio(file="p1.mp3"), image="p1.webp")],
    )
    (story_dir / "story.json").write_text(story.model_dump_json())
    return story_id


class _Harness:
    """One workshop app over a moto bucket, with login helpers."""

    def __init__(self, tmp_path: Path, s3: S3Client, secret: str = SECRET) -> None:
        self.settings = _settings(tmp_path, secret)
        self.store = RunStore(self.settings, client=s3)
        self.published: list[str] = []

        def fake_generate(request: PackRequest, settings: Settings) -> list[Path]:
            staged = settings.staging_dir / f"{request.theme}-{request.language}-fake0001"
            staged.mkdir(parents=True, exist_ok=True)
            return [staged]

        self.manager = RunManager(self.store, self.settings, generate_pack=fake_generate)
        app = create_app()
        app.dependency_overrides[get_settings] = lambda: self.settings
        app.dependency_overrides[get_run_manager] = lambda: self.manager
        app.dependency_overrides[get_publisher] = lambda: self.published.append
        self.client = TestClient(app)

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
    assert "first_snow" not in page.text  # no workshop content before the gate


def test_a_wrong_secret_is_rejected(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)

    response = harness.client.post("/workshop/login", data={"secret": "guess"})

    assert response.status_code == 401


def test_login_sets_a_session_and_shows_the_dashboard(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)

    harness.login()
    page = harness.client.get("/workshop")

    assert page.status_code == 200
    assert 'action="/workshop/runs"' in page.text  # the start-a-run form


def test_the_session_cookie_is_not_the_secret_itself(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)

    harness.login()

    cookie = harness.client.cookies.get("workshop_session")
    assert cookie
    assert SECRET not in cookie
    assert cookie == hashlib.sha256(SECRET.encode()).hexdigest()


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
    assert record.state == "staged"  # TestClient runs background tasks to completion
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
    assert "hx-get" in fragment.text  # still polling while the run is live


def test_a_settled_run_stops_polling_and_links_its_stories(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    story_id = _stage_fake_story(harness.settings)
    record = new_run("operator", PackRequest(theme="the_sleepy_sea", language="it", count=1))
    harness.store.save(record.advance("running").advance("staged", story_ids=[story_id]))

    fragment = harness.client.get(f"/workshop/runs/{record.id}/progress")

    assert "hx-get" not in fragment.text  # staged is settled; no more polling
    assert f"/workshop/staged/{story_id}" in fragment.text


def test_the_staged_story_page_shows_text_audio_and_images(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    story_id = _stage_fake_story(harness.settings)

    page = harness.client.get(f"/workshop/staged/{story_id}")

    assert page.status_code == 200
    assert SENTENCE in page.text
    assert f"/workshop/staged/{story_id}/assets/p1.mp3" in page.text
    assert f"/workshop/staged/{story_id}/assets/p1.webp" in page.text


def test_staged_assets_are_served_and_traversal_is_blocked(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    story_id = _stage_fake_story(harness.settings)
    (harness.settings.staging_dir.parent / "secret.txt").write_text("keys")

    assert harness.client.get(f"/workshop/staged/{story_id}/assets/p1.mp3").content == b"mp3:p1"
    escape = harness.client.get(f"/workshop/staged/{story_id}/assets/../../secret.txt")
    assert escape.status_code in (400, 404)


def test_approving_a_staged_run_publishes_its_stories_and_settles_the_record(
    tmp_path: Path, s3: S3Client
) -> None:
    harness = _Harness(tmp_path, s3)
    harness.login()
    story_id = _stage_fake_story(harness.settings)
    record = new_run("operator", PackRequest(theme="the_sleepy_sea", language="it", count=1))
    harness.store.save(record.advance("running").advance("staged", story_ids=[story_id]))

    response = harness.client.post(f"/workshop/runs/{record.id}/approve", follow_redirects=False)

    assert response.status_code == 303
    assert harness.published == [story_id]
    reloaded = harness.store.load("operator", record.id)
    assert reloaded is not None
    assert reloaded.state == "approved"
