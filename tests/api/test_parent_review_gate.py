"""No approve without review (B2, AI-475).

A parent's approve publishes a story to their child's shelf, so it must follow
a real review: the server refuses (409) unless every story the run staged
still exists in the pending bucket and the parent opened its review page —
the page that renders every one of its pages, pictures and sounds. Nothing is
published on a refusal.

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
from src.workshop.records import PackRequest, RunRecord, RunStore, new_run
from tests.api.test_parent_approve import BUCKET, FAMILY, PARENT, Harness


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Iterator[S3Client]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
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
        Bucket=BUCKET,
        Key=f"pending/staged/{story_id}/story.json",
        Body=json.dumps(story).encode(),
    )


def _staged_run(store: RunStore, story_ids: list[str]) -> RunRecord:
    record = new_run(FAMILY, PackRequest(theme="the_sleepy_sea", language="en", count=1))
    record = record.advance("running").advance("staged", story_ids=story_ids)
    store.save(record)
    return record


def _approve(harness: Harness, run_id: str) -> int:
    return harness.client.post(
        f"/parent/packs/{run_id}/approve", follow_redirects=False
    ).status_code


def test_approve_without_review_is_409_and_publishes_nothing(tmp_path: Path, s3: S3Client) -> None:
    harness = Harness(tmp_path, s3)
    _stage_story(s3, "story-one")
    record = _staged_run(harness.store, ["story-one"])
    harness.sign_in(PARENT)

    assert _approve(harness, record.id) == 409
    assert harness.published == []
    reloaded = harness.store.load(FAMILY, record.id)
    assert reloaded is not None
    assert reloaded.state == "staged"


def test_reviewing_the_story_then_approving_publishes(tmp_path: Path, s3: S3Client) -> None:
    harness = Harness(tmp_path, s3)
    _stage_story(s3, "story-one")
    record = _staged_run(harness.store, ["story-one"])
    harness.sign_in(PARENT)

    page = harness.client.get(f"/parent/staged/story-one?run={record.id}")
    assert page.status_code == 200
    assert "Last page of story-one." in page.text  # the last page was delivered

    assert _approve(harness, record.id) == 303
    assert harness.published == [("story-one", FAMILY)]


def test_reviewing_only_part_of_a_run_is_409(tmp_path: Path, s3: S3Client) -> None:
    """Every staged story is reviewed, not just the first one."""
    harness = Harness(tmp_path, s3)
    _stage_story(s3, "story-one")
    _stage_story(s3, "story-two")
    record = _staged_run(harness.store, ["story-one", "story-two"])
    harness.sign_in(PARENT)

    page = harness.client.get(f"/parent/staged/story-one?run={record.id}")
    assert page.status_code == 200
    # The approve control waits for the rest; the page points at what is left.
    assert "Approve &amp; publish" not in page.text
    assert f"/parent/staged/story-two?run={record.id}" in page.text

    assert _approve(harness, record.id) == 409
    assert harness.published == []

    finished = harness.client.get(f"/parent/staged/story-two?run={record.id}")
    assert "Approve &amp; publish" in finished.text
    assert _approve(harness, record.id) == 303
    assert harness.published == [("story-one", FAMILY), ("story-two", FAMILY)]


def test_a_reviewed_story_that_is_gone_cannot_be_approved(tmp_path: Path, s3: S3Client) -> None:
    harness = Harness(tmp_path, s3)
    _stage_story(s3, "story-one")
    record = _staged_run(harness.store, ["story-one"])
    harness.sign_in(PARENT)
    harness.client.get(f"/parent/staged/story-one?run={record.id}")
    s3.delete_object(Bucket=BUCKET, Key="pending/staged/story-one/story.json")

    assert _approve(harness, record.id) == 409
    assert harness.published == []


def test_a_staged_run_with_no_stories_cannot_be_approved(tmp_path: Path, s3: S3Client) -> None:
    harness = Harness(tmp_path, s3)
    record = _staged_run(harness.store, [])
    harness.sign_in(PARENT)

    assert _approve(harness, record.id) == 409
    assert harness.published == []


def test_a_record_saved_before_review_tracking_counts_as_unreviewed(
    tmp_path: Path, s3: S3Client
) -> None:
    harness = Harness(tmp_path, s3)
    _stage_story(s3, "story-one")
    record = _staged_run(harness.store, ["story-one"])
    key = f"pending/{FAMILY}/runs/{record.id}.json"
    legacy = json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    legacy.pop("reviewed_story_ids", None)
    s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(legacy).encode())
    harness.sign_in(PARENT)

    assert _approve(harness, record.id) == 409
    assert harness.published == []


def test_the_run_row_never_offers_approve(tmp_path: Path, s3: S3Client) -> None:
    """The Being-made row links to review; approving happens only on the review page."""
    harness = Harness(tmp_path, s3)
    _stage_story(s3, "story-one")
    record = _staged_run(harness.store, ["story-one"])
    harness.sign_in(PARENT)

    row = harness.client.get(f"/parent/packs/{record.id}/progress").text

    assert 'data-testid="parent-review-link"' in row
    assert "/approve" not in row
    assert 'data-testid="parent-approve"' not in row


@pytest.mark.parametrize("story_ids", [[], ["story-missing"]])
def test_the_run_row_shows_an_error_when_nothing_can_be_reviewed(
    tmp_path: Path, s3: S3Client, story_ids: list[str]
) -> None:
    harness = Harness(tmp_path, s3)
    record = _staged_run(harness.store, story_ids)
    harness.sign_in(PARENT)

    row = harness.client.get(f"/parent/packs/{record.id}/progress").text

    assert "/approve" not in row
    assert 'data-testid="parent-approve"' not in row
    assert 'data-testid="parent-review-missing"' in row


@pytest.mark.parametrize("story_ids", [[], ["story-missing"]])
def test_an_unreviewable_run_can_still_be_rejected_from_its_row(
    tmp_path: Path, s3: S3Client, story_ids: list[str]
) -> None:
    """The error state is not a dead end: the parent can clear the run."""
    harness = Harness(tmp_path, s3)
    record = _staged_run(harness.store, story_ids)
    harness.sign_in(PARENT)

    row = harness.client.get(f"/parent/packs/{record.id}/progress").text

    assert f'action="/parent/packs/{record.id}/reject"' in row
    assert 'data-testid="parent-reject"' in row
    response = harness.client.post(f"/parent/packs/{record.id}/reject", follow_redirects=False)
    assert response.status_code == 303
    reloaded = harness.store.load(FAMILY, record.id)
    assert reloaded is not None
    assert reloaded.state == "rejected"
    assert harness.published == []
