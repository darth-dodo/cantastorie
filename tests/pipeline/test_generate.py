"""End-to-end spec for the authoring run (AI-361).

generate braids the whole pipeline — write → safety → narrate → illustrate →
assemble → stage (docs/architecture.md "The Authoring Pipeline"). Every
provider seam is mocked, so the run touches no network: the writer and judge are
pydantic-ai doubles, ElevenLabs and OpenRouter images are httpx.MockTransport.
The proof is a staged story.json in exactly the shape the player already plays.
"""

import base64
import hashlib
import json
from pathlib import Path

import httpx
from pydantic import SecretStr
from pydantic_ai.models.test import TestModel

from src.config import Settings
from src.pipeline.content_rules import check_story
from src.pipeline.generate import generate_story
from src.pipeline.models import Story
from src.pipeline.providers import NarrationClient
from src.pipeline.steps.narrate import IT_UTTERANCES

# Five words a sentence, eight sentences a page, eight pages: clears every limit.
_PAGE = " ".join(["The water sings shh shh."] * 8)
_GOOD_DRAFT = {"title": "La barchetta e la luna", "pages": [_PAGE] * 8}
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


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-test"),
        elevenlabs_api_key=SecretStr("el-test"),
        elevenlabs_voice_id="voice-1",
        content_dir=tmp_path / "content",
        staging_dir=tmp_path / "staging",
    )


def _fake_elevenlabs() -> NarrationClient:
    """A NarrationClient over MockTransport: deterministic audio + a real-shaped
    character alignment for whatever text arrives."""

    def handler(request: httpx.Request) -> httpx.Response:
        text = json.loads(request.content)["text"]
        chars = list(text)
        return httpx.Response(
            200,
            json={
                "audio_base64": base64.b64encode(f"mp3:{text}".encode()).decode(),
                "alignment": {
                    "characters": chars,
                    "character_start_times_seconds": [round(i * 0.1, 1) for i in range(len(chars))],
                    "character_end_times_seconds": [
                        round((i + 1) * 0.1, 1) for i in range(len(chars))
                    ],
                },
            },
        )

    settings = Settings(
        _env_file=None, elevenlabs_api_key=SecretStr("el-test"), elevenlabs_voice_id="voice-1"
    )
    return NarrationClient(settings, transport=httpx.MockTransport(handler))


def _fake_images() -> httpx.MockTransport:
    """OpenRouter image generation over MockTransport: deterministic PNG bytes."""

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


def _generate(tmp_path: Path) -> tuple[Settings, Path]:
    settings = _settings(tmp_path)
    staged = generate_story(
        "the_sleepy_sea",
        "it",
        settings,
        write_model=TestModel(custom_output_args=_GOOD_DRAFT),
        safety_model=TestModel(custom_output_args=_PASSING_REPORT),
        revise_model=TestModel(custom_output_args=_GOOD_DRAFT),
        narration_client=_fake_elevenlabs(),
        image_transport=_fake_images(),
    )
    return settings, staged


def test_generate_stages_a_story_that_matches_the_player_fixture_shape(tmp_path: Path) -> None:
    """Given a theme and language with every provider mocked,
    When generate runs the whole pass,
    Then it stages a story.json whose top-level and per-page shape matches the
    dev fixture the player already plays — the player plays it unchanged.
    """
    _settings_used, staged = _generate(tmp_path)

    published = json.loads((staged / "story.json").read_bytes())
    fixture = json.loads(
        Path("src/static/content/it/stories/la-barchetta-e-la-luna/story.json").read_bytes()
    )
    assert published.keys() == fixture.keys()
    assert published["pages"][0].keys() == fixture["pages"][0].keys()
    assert published["pages"][0]["audio"].keys() == fixture["pages"][0]["audio"].keys()


def test_the_staged_story_validates_typed_and_conforms_to_the_content_rules(
    tmp_path: Path,
) -> None:
    """Given the staged story,
    When it is parsed as a Story and checked,
    Then it is a valid eight-page Story that breaks no content rule — validation
    is enforced as code, not prompt hope (docs/architecture.md "Testing").
    """
    _settings_used, staged = _generate(tmp_path)

    story = Story.model_validate_json((staged / "story.json").read_bytes())
    assert len(story.pages) == 8
    assert check_story(story) == []
    assert story.language == "it"


def test_every_staged_page_has_its_hashed_audio_and_image_on_disk(tmp_path: Path) -> None:
    """Given the staged folder,
    When its assets are listed,
    Then every page references an audio and an image whose content-hashed file
    sits right beside story.json — text, audio, images together for review.
    """
    _settings_used, staged = _generate(tmp_path)

    story = Story.model_validate_json((staged / "story.json").read_bytes())
    for page in story.pages:
        assert page.audio is not None
        assert (staged / page.audio.file).exists()
        assert page.image is not None
        assert (staged / page.image).exists()


def test_generate_stages_the_italian_spoken_prompts(tmp_path: Path) -> None:
    """Given the slice-1 Italian prompt set (docs/product.md **Spoken Prompts**),
    When generate runs,
    Then the shelf greeting, story start, and end prompt are staged under
    staging/prompts/it/ as hashed mp3s — first-class assets, ready to publish.
    """
    settings, _staged = _generate(tmp_path)

    prompt_dir = settings.staging_dir / "prompts" / "it"
    stems = {path.name.split(".")[0] for path in prompt_dir.glob("*.mp3")}
    assert stems == set(IT_UTTERANCES)


def test_rerunning_generate_reproduces_an_identical_staged_story(tmp_path: Path) -> None:
    """Given a story already generated,
    When generate runs again unchanged (content-addressed caching),
    Then the staged story.json is byte-for-byte identical — the cache makes a
    re-run reproducible and free of provider calls.
    """
    settings, staged = _generate(tmp_path)
    first = (staged / "story.json").read_bytes()

    staged_again = generate_story(
        "the_sleepy_sea",
        "it",
        settings,
        write_model=TestModel(custom_output_args=_GOOD_DRAFT),
        safety_model=TestModel(custom_output_args=_PASSING_REPORT),
        revise_model=TestModel(custom_output_args=_GOOD_DRAFT),
        narration_client=_fake_elevenlabs(),
        image_transport=_fake_images(),
    )
    assert (staged_again / "story.json").read_bytes() == first
