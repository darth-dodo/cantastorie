"""End-to-end spec for the authoring run (AI-361).

generate braids the whole pipeline — write → safety → narrate → illustrate →
assemble → stage (docs/architecture.md "The Authoring Pipeline"). Every
provider seam is mocked, so the run touches no network: the writer and judge are
pydantic-ai doubles, narration and OpenRouter images are httpx.MockTransport.
The proof is a staged story.json in R2 in exactly the shape the player plays.
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
from src.pipeline.content_rules import check_story
from src.pipeline.generate import generate_story
from src.pipeline.models import Story
from src.pipeline.providers import NarrationClient
from src.pipeline.publish import STAGED_PREFIX, STORY_FILE
from src.pipeline.steps.narrate import IT_UTTERANCES

_PAGE = " ".join(["The water sings shh shh."] * 8)
_GOOD_DRAFT = {"title": "La barchetta e la luna", "pages": [_PAGE] * 10}
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


def _generate(tmp_path: Path, s3: S3Client) -> tuple[Settings, str]:
    settings = _settings(tmp_path)
    staged = generate_story(
        "the_sleepy_sea",
        "it",
        settings,
        write_model=TestModel(custom_output_args=_GOOD_DRAFT),
        safety_model=TestModel(custom_output_args=_PASSING_REPORT),
        revise_model=TestModel(custom_output_args=_GOOD_DRAFT),
        narration_client=_fake_narration(),
        image_transport=_fake_images(),
    )
    return settings, staged


def _staged_json(s3: S3Client, prefix: str) -> bytes:
    return s3.get_object(Bucket=BUCKET, Key=f"{prefix}/{STORY_FILE}")["Body"].read()


def test_generate_stages_a_story_that_matches_the_player_fixture_shape(
    tmp_path: Path, s3: S3Client
) -> None:
    _settings_used, staged = _generate(tmp_path, s3)

    published = json.loads(_staged_json(s3, staged))
    fixture = json.loads(
        Path("src/static/content/it/stories/la-barchetta-e-la-luna/story.json").read_bytes()
    )
    assert published.keys() == fixture.keys()
    assert published["pages"][0].keys() == fixture["pages"][0].keys()
    assert published["pages"][0]["audio"].keys() == fixture["pages"][0]["audio"].keys()


def test_the_staged_story_validates_typed_and_conforms_to_the_content_rules(
    tmp_path: Path, s3: S3Client
) -> None:
    _settings_used, staged = _generate(tmp_path, s3)

    story = Story.model_validate_json(_staged_json(s3, staged))
    assert len(story.pages) == 10
    assert check_story(story) == []
    assert story.language == "it"


def test_every_staged_page_has_its_hashed_audio_and_image_in_r2(
    tmp_path: Path, s3: S3Client
) -> None:
    _settings_used, staged = _generate(tmp_path, s3)

    story = Story.model_validate_json(_staged_json(s3, staged))
    for page in story.pages:
        assert page.audio is not None
        s3.head_object(Bucket=BUCKET, Key=f"{staged}/{page.audio.file}")
        assert page.image is not None
        s3.head_object(Bucket=BUCKET, Key=f"{staged}/{page.image}")


def test_generate_stages_the_italian_spoken_prompts(tmp_path: Path, s3: S3Client) -> None:
    _settings, _staged = _generate(tmp_path, s3)

    response = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"{STAGED_PREFIX}/prompts/it/")
    prompt_keys = [k.rsplit("/", 1)[-1] for k in [o["Key"] for o in response.get("Contents", [])]]
    stems = {name.split(".")[0] for name in prompt_keys}
    assert stems == set(IT_UTTERANCES)


def test_rerunning_generate_reproduces_an_identical_staged_story(
    tmp_path: Path, s3: S3Client
) -> None:
    settings, staged = _generate(tmp_path, s3)
    first = _staged_json(s3, staged)

    staged_again = generate_story(
        "the_sleepy_sea",
        "it",
        settings,
        write_model=TestModel(custom_output_args=_GOOD_DRAFT),
        safety_model=TestModel(custom_output_args=_PASSING_REPORT),
        revise_model=TestModel(custom_output_args=_GOOD_DRAFT),
        narration_client=_fake_narration(),
        image_transport=_fake_images(),
    )
    assert _staged_json(s3, staged_again) == first


def test_a_premise_stages_the_story_under_its_own_folder(tmp_path: Path, s3: S3Client) -> None:
    settings = _settings(tmp_path)

    def run(premise: str | None) -> str:
        return generate_story(
            "the_sleepy_sea",
            "it",
            settings,
            write_model=TestModel(custom_output_args=_GOOD_DRAFT),
            safety_model=TestModel(custom_output_args=_PASSING_REPORT),
            revise_model=TestModel(custom_output_args=_GOOD_DRAFT),
            narration_client=_fake_narration(),
            image_transport=_fake_images(),
            premise=premise,
        )

    plain = run(None)
    premised = run("A birthday at sea.")
    assert plain != premised
