"""No approve without review (B2, AI-475), for a run's one story (AI-480).

A parent's approve publishes a story to their child's shelf, so it must follow
a real review: the server refuses (409) unless the run's story still exists in
the pending bucket and the parent opened its review page — the page that
renders every one of its pages, pictures and sounds. Nothing is published on
a refusal.

Runs on moto end to end; the staged story JSON lives in the pending bucket.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from mypy_boto3_s3 import S3Client

import src.api.routes.parent as parent_module
from src.api.routes._templates import TEMPLATES_DIR
from src.workshop.records import V1_STORY_IDS_KEY, RunRecord, RunStore, StoryRequest, new_run
from tests.api.test_parent_approve import BUCKET, FAMILY, PARENT, PENDING_BUCKET, Harness


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Iterator[S3Client]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.create_bucket(Bucket=PENDING_BUCKET)
        monkeypatch.setattr(parent_module, "_build_client", lambda settings: client)
        yield client


def _stage_story(s3: S3Client, story_id: str) -> None:
    story = {
        "id": story_id,
        "title": f"Title of {story_id}",
        "language": "en",
        "theme": "the_sleepy_sea",
        "shape": "linear",
        "pages": [
            {"id": "p1", "text": f"First page of {story_id}.", "image": None, "audio": None},
            {"id": "p2", "text": f"Last page of {story_id}.", "image": None, "audio": None},
        ],
    }
    s3.put_object(
        Bucket=PENDING_BUCKET,
        Key=f"pending/staged/{story_id}/story.json",
        Body=json.dumps(story).encode(),
    )


def _staged_run(store: RunStore, story_id: str | None) -> RunRecord:
    record = new_run(FAMILY, StoryRequest(theme="the_sleepy_sea", language="en"))
    record = record.advance("running").advance("staged", story_id=story_id)
    store.save(record)
    return record


def _approve(harness: Harness, run_id: str) -> int:
    return harness.client.post(f"/parent/runs/{run_id}/approve", follow_redirects=False).status_code


def _approve_detail(harness: Harness, run_id: str) -> tuple[int, str]:
    response = harness.client.post(f"/parent/runs/{run_id}/approve", follow_redirects=False)
    return response.status_code, response.json()["detail"]


def test_approve_without_review_is_409_and_publishes_nothing(tmp_path: Path, s3: S3Client) -> None:
    harness = Harness(tmp_path, s3)
    _stage_story(s3, "story-one")
    record = _staged_run(harness.store, "story-one")
    harness.sign_in(PARENT)

    assert _approve_detail(harness, record.id) == (409, "Review the story before approving")
    assert harness.published == []
    reloaded = harness.store.load(FAMILY, record.id)
    assert reloaded is not None
    assert reloaded.state == "staged"


def test_reviewing_the_story_then_approving_publishes(tmp_path: Path, s3: S3Client) -> None:
    harness = Harness(tmp_path, s3)
    _stage_story(s3, "story-one")
    record = _staged_run(harness.store, "story-one")
    harness.sign_in(PARENT)

    page = harness.client.get(f"/parent/staged/story-one?run={record.id}")
    assert page.status_code == 200
    assert "Last page of story-one." in page.text  # the last page was delivered

    assert _approve(harness, record.id) == 303
    assert harness.published == [("story-one", FAMILY)]


def test_a_reviewed_story_that_is_gone_cannot_be_approved(tmp_path: Path, s3: S3Client) -> None:
    harness = Harness(tmp_path, s3)
    _stage_story(s3, "story-one")
    record = _staged_run(harness.store, "story-one")
    harness.sign_in(PARENT)
    harness.client.get(f"/parent/staged/story-one?run={record.id}")
    s3.delete_object(Bucket=PENDING_BUCKET, Key="pending/staged/story-one/story.json")

    assert _approve_detail(harness, record.id) == (409, "The staged story is missing")
    assert harness.published == []


def test_a_staged_run_with_no_story_cannot_be_approved(tmp_path: Path, s3: S3Client) -> None:
    harness = Harness(tmp_path, s3)
    record = _staged_run(harness.store, None)
    harness.sign_in(PARENT)

    assert _approve_detail(harness, record.id) == (409, "The run has no staged story to publish")
    assert harness.published == []


def test_a_record_saved_before_review_tracking_counts_as_unreviewed(
    tmp_path: Path, s3: S3Client
) -> None:
    harness = Harness(tmp_path, s3)
    _stage_story(s3, "story-one")
    record = _staged_run(harness.store, "story-one")
    key = f"pending/{FAMILY}/runs/{record.id}.json"
    # As saved before B2 and AI-480: schema 1, a list of story ids, no review field.
    legacy = json.loads(s3.get_object(Bucket=PENDING_BUCKET, Key=key)["Body"].read())
    legacy.pop("reviewed")
    legacy.pop("story_id")
    legacy.update({"schema_version": 1, V1_STORY_IDS_KEY: ["story-one"]})
    s3.put_object(Bucket=PENDING_BUCKET, Key=key, Body=json.dumps(legacy).encode())
    harness.sign_in(PARENT)

    assert _approve(harness, record.id) == 409
    assert harness.published == []


def test_the_run_row_never_offers_approve(tmp_path: Path, s3: S3Client) -> None:
    """The Being-made row links to review; approving happens only on the review page."""
    harness = Harness(tmp_path, s3)
    _stage_story(s3, "story-one")
    record = _staged_run(harness.store, "story-one")
    harness.sign_in(PARENT)

    row = harness.client.get(f"/parent/runs/{record.id}/progress").text

    assert 'data-testid="parent-review-link"' in row
    assert "/approve" not in row
    assert 'data-testid="parent-approve"' not in row


@pytest.mark.parametrize("story_id", [None, "story-missing"])
def test_the_run_row_shows_an_error_when_nothing_can_be_reviewed(
    tmp_path: Path, s3: S3Client, story_id: str | None
) -> None:
    harness = Harness(tmp_path, s3)
    record = _staged_run(harness.store, story_id)
    harness.sign_in(PARENT)

    row = harness.client.get(f"/parent/runs/{record.id}/progress").text

    assert "/approve" not in row
    assert 'data-testid="parent-approve"' not in row
    assert 'data-testid="parent-review-missing"' in row


@pytest.mark.parametrize("story_id", [None, "story-missing"])
def test_an_unreviewable_run_can_still_be_rejected_from_its_row(
    tmp_path: Path, s3: S3Client, story_id: str | None
) -> None:
    """The error state is not a dead end: the parent can clear the run."""
    harness = Harness(tmp_path, s3)
    record = _staged_run(harness.store, story_id)
    harness.sign_in(PARENT)

    row = harness.client.get(f"/parent/runs/{record.id}/progress").text

    assert f'action="/parent/runs/{record.id}/reject"' in row
    assert 'data-testid="parent-reject"' in row
    response = harness.client.post(f"/parent/runs/{record.id}/reject", follow_redirects=False)
    assert response.status_code == 303
    reloaded = harness.store.load(FAMILY, record.id)
    assert reloaded is not None
    assert reloaded.state == "rejected"
    assert harness.published == []


def test_the_review_page_offers_approve_once_the_story_is_seen(
    tmp_path: Path, s3: S3Client
) -> None:
    """One story, one review: opening it is enough — there is no next story."""
    harness = Harness(tmp_path, s3)
    _stage_story(s3, "story-one")
    record = _staged_run(harness.store, "story-one")
    harness.sign_in(PARENT)

    page = harness.client.get(f"/parent/staged/story-one?run={record.id}").text

    assert f'action="/parent/runs/{record.id}/approve"' in page
    assert "Review the next story" not in page
    assert 'data-testid="parent-review-next"' not in page


def test_the_review_template_has_no_next_story_path() -> None:
    review = (Path(TEMPLATES_DIR) / "parent" / "review.html").read_text()
    assert "unreviewed_story_ids" not in review
    assert "Review the next story" not in review
