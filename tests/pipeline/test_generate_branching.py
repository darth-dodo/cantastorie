"""End-to-end spec for the branching authoring run (AI-428).

Mirrors tests/pipeline/test_generate.py but drives the pipeline with
``shape="branching"``: the writer and reviser doubles return a
``BranchingStoryDraft``, so generate braids the full branching pipeline —
write → safety → narrate → narrate_choice_labels → illustrate → assemble →
stage. Every provider seam is mocked, so the run touches no network. The proof
is a staged story.json whose shape is branching, whose options carry hashed
label audio and card images, and whose every referenced asset is staged in R2.
"""

import base64
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import boto3
import httpx
import pytest
from moto import mock_aws
from mypy_boto3_s3 import S3Client
from pydantic import SecretStr
from pydantic_ai.models.test import TestModel

from src.config import Settings
from src.pipeline.content_rules import ARM_PAGES, PAGE_COUNT, check_story
from src.pipeline.generate import generate_story
from src.pipeline.models import Story
from src.pipeline.providers import NarrationClient
from src.pipeline.publish import STORY_FILE

SHARED = PAGE_COUNT - ARM_PAGES
# 30 words a page keeps every heard path (10 pages) inside the 250-600 word band
# and every page inside the 30-70 floor/ceiling, with room for two choice labels.
_PAGE = " ".join(["dorme."] * 30)
_GOOD_BRANCHING_DRAFT = {
    "title": "La lanterna e la barchetta",
    "shared_pages": [_PAGE] * SHARED,
    "option_labels": ["la lanterna", "la barchetta"],
    "arm_a": [_PAGE] * ARM_PAGES,
    "arm_b": [_PAGE] * ARM_PAGES,
}
_PASSING_REPORT = {
    "verdicts": [
        {"rule": rule, "passed": True, "reason": "ok"}
        for rule in (
            "mildest_peril_only",
            "no_fear_reinforcement",
            "no_brands",
            "no_romance",
            "kindness_resolves",
            "within_limits",
            "right_language",
            "calm_pictures",
            "nothing_real",
        )
    ]
}

BUCKET = "cantastorie-published"


@pytest.fixture
def s3() -> Iterator[S3Client]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-test"),
        content_dir=tmp_path / "content",
        staging_dir=tmp_path / "staging",
        r2_bucket=BUCKET,
    )


def _fake_narration() -> NarrationClient:
    def handler(request: httpx.Request) -> httpx.Response:
        text = json.loads(request.content)["input"]
        return httpx.Response(
            200, content=f"mp3:{text}".encode(), headers={"Content-Type": "audio/mpeg"}
        )

    settings = Settings(_env_file=None, openrouter_api_key=SecretStr("sk-or-test"))
    return NarrationClient(settings, transport=httpx.MockTransport(handler))


def _fake_images() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content)["messages"][0]["content"][0]["text"]
        png = b"png:" + hashlib.sha256(prompt.encode()).digest()
        data_url = "data:image/png;base64," + base64.b64encode(png).decode()
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "images": [{"image_url": {"url": data_url}}]}}
                ]
            },
        )

    return httpx.MockTransport(handler)


def _generate_branching(tmp_path: Path) -> tuple[Settings, str]:
    settings = _settings(tmp_path)
    staged = generate_story(
        "the_little_boat",
        "it",
        settings,
        shape="branching",
        write_model=TestModel(custom_output_args=_GOOD_BRANCHING_DRAFT),
        safety_model=TestModel(custom_output_args=_PASSING_REPORT),
        revise_model=TestModel(custom_output_args=_GOOD_BRANCHING_DRAFT),
        narration_client=_fake_narration(),
        image_transport=_fake_images(),
    )
    return settings, staged


def _staged_json(s3: S3Client, prefix: str) -> bytes:
    return s3.get_object(Bucket=BUCKET, Key=f"{prefix}/{STORY_FILE}")["Body"].read()


def test_generate_stages_a_branching_story(tmp_path: Path, s3: S3Client) -> None:
    _settings_used, staged = _generate_branching(tmp_path)

    story = Story.model_validate_json(_staged_json(s3, staged))
    assert story.shape == "branching"
    assert check_story(story) == []


def test_the_staged_branching_story_carries_hashed_option_assets(
    tmp_path: Path, s3: S3Client
) -> None:
    _settings_used, staged = _generate_branching(tmp_path)

    story = Story.model_validate_json(_staged_json(s3, staged))
    choice_page = next(p for p in story.pages if p.choice is not None)
    for option in choice_page.choice.options:
        assert option.audio is not None
        assert "." in option.audio.file and "/" not in option.audio.file
        assert option.card_image is not None
        assert "." in option.card_image and "/" not in option.card_image


def test_every_referenced_branching_asset_is_staged(tmp_path: Path, s3: S3Client) -> None:
    _settings_used, staged = _generate_branching(tmp_path)

    story = Story.model_validate_json(_staged_json(s3, staged))
    for page in story.pages:
        assert page.audio is not None
        s3.head_object(Bucket=BUCKET, Key=f"{staged}/{page.audio.file}")
        assert page.image is not None
        s3.head_object(Bucket=BUCKET, Key=f"{staged}/{page.image}")
        if page.choice is not None:
            for option in page.choice.options:
                assert option.audio is not None
                s3.head_object(Bucket=BUCKET, Key=f"{staged}/{option.audio.file}")
                assert option.card_image is not None
                s3.head_object(Bucket=BUCKET, Key=f"{staged}/{option.card_image}")
