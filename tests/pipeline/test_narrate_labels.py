"""Behavior specs for narrating spoken choice labels (AI-428).

docs/product.md "Content Rules" (**Branching stories**): a choice page offers
two spoken options. Each option's label is synthesized through the same
content-addressed narration cache as page text (ADR-008), so the child hears
the choices read aloud. Gemini returns no timestamps — label timings stay
empty, exactly like page audio. Non-choice pages pass through untouched.
Every OpenRouter interaction is served by httpx.MockTransport — zero network.
"""

from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from src.config import Settings
from src.pipeline.cache import ArtifactCache
from src.pipeline.models import Page
from src.pipeline.providers import NarrationClient
from src.pipeline.steps.narrate import narrate_choice_labels
from src.pipeline.steps.write import branching_story_from_draft
from tests.pipeline.test_write_branching import make_draft


def _fake_openrouter(calls: list[str]) -> httpx.MockTransport:
    """A mock OpenRouter /audio/speech that echoes deterministic audio for
    whatever text arrives, recording every call."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        text = body["input"]
        calls.append(text)
        return httpx.Response(
            200,
            content=f"pcm:{text}".encode(),
            headers={"Content-Type": "audio/pcm;rate=24000;channels=1"},
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, openrouter_api_key=SecretStr("sk-or-test"))


@pytest.fixture
def cache(tmp_path: Path) -> ArtifactCache:
    return ArtifactCache(tmp_path / "story-1")


@pytest.fixture
def fake_client(settings: Settings) -> NarrationClient:
    return NarrationClient(settings, transport=_fake_openrouter([]))


def test_labels_get_audio(
    fake_client: NarrationClient, settings: Settings, cache: ArtifactCache
) -> None:
    pages = branching_story_from_draft(
        make_draft(), story_id="s1", theme="the_little_boat", language="it"
    ).pages
    narrated = narrate_choice_labels(pages, "it", settings, cache, client=fake_client)
    choice_page = next(p for p in narrated if p.choice is not None)
    for option in choice_page.choice.options:
        assert option.audio is not None
        assert option.audio.file.endswith(".wav")
        assert option.audio.timings == []


def test_non_choice_pages_untouched(
    fake_client: NarrationClient, settings: Settings, cache: ArtifactCache
) -> None:
    pages = [Page(id="p1", text="dorme.", next_page=None)]
    assert narrate_choice_labels(pages, "it", settings, cache, client=fake_client) == pages
