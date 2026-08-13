"""Behavior specs for choice-card illustration (AI-428).

docs/product.md "Branching stories": a choice offers two options; each option
gets a watercolor card so a pre-reader can pick by picture. Cards are drawn
against the same character sheet as pages — never chained — and keyed
`f"{page_id}:{option_index}"`. A linear story has no choices, hence no cards.
"""

import base64
import hashlib
import json
from pathlib import Path

import httpx
from pydantic import SecretStr

from src.config import Settings
from src.pipeline.cache import ArtifactCache
from src.pipeline.content_rules import ARM_PAGES, PAGE_COUNT
from src.pipeline.models import Page, Story
from src.pipeline.steps.illustrate import CARD_PROMPT, STYLE_PROMPT, illustrate_story
from src.pipeline.steps.write import BranchingStoryDraft, branching_story_from_draft

SHARED = PAGE_COUNT - ARM_PAGES
PAGE_TEXT = " ".join(["dorme."] * 30)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-test"),
        image_model="google/gemini-2.5-flash-image",
    )


def make_draft() -> BranchingStoryDraft:
    return BranchingStoryDraft(
        title="La lanterna e la barchetta",
        shared_pages=[PAGE_TEXT] * SHARED,
        option_labels=("la lanterna", "la barchetta"),
        arm_a=[PAGE_TEXT] * ARM_PAGES,
        arm_b=[PAGE_TEXT] * ARM_PAGES,
    )


def _linear_story() -> Story:
    texts = [f"The little boat rocks, page {n}." for n in range(1, 11)]
    pages = [
        Page(id=f"page-{n}", text=text, next_page=f"page-{n + 1}" if n < len(texts) else None)
        for n, text in enumerate(texts, start=1)
    ]
    return Story(
        id="story-1",
        language="it",
        title="La barchetta sonnolenta",
        theme="the_little_boat",
        shape="linear",
        pages=pages,
    )


class _FakeImageModel:
    """MockTransport handler answering like OpenRouter's image API (no network)."""

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        prompt = body["messages"][0]["content"][0]["text"]
        fake_png = b"png:" + hashlib.sha256(prompt.encode()).digest()
        data_url = "data:image/png;base64," + base64.b64encode(fake_png).decode()
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "images": [{"image_url": {"url": data_url}}]}}
                ]
            },
        )


def _image_parts(request_body: dict[str, object]) -> list[str]:
    messages = request_body["messages"]
    assert isinstance(messages, list)
    content = messages[0]["content"]
    return [part["image_url"]["url"] for part in content if part["type"] == "image_url"]


def test_choice_options_get_cards(tmp_path: Path) -> None:
    """Given a branching story with one choice page,
    When the illustration step runs,
    Then each of the choice's two options gets a card, keyed
    `f"{page_id}:{option_index}"`, and every card is on disk.
    """
    model = _FakeImageModel()
    cache = ArtifactCache(tmp_path / "s1")
    story = branching_story_from_draft(
        make_draft(), story_id="s1", theme="the_little_boat", language="it"
    )

    result = illustrate_story(story, _settings(), cache, transport=model.transport())

    choice_page = next(p for p in story.pages if p.choice is not None)
    assert f"{choice_page.id}:0" in result.card_images
    assert f"{choice_page.id}:1" in result.card_images
    assert all(path.exists() for path in result.card_images.values())


def test_linear_story_has_no_cards(tmp_path: Path) -> None:
    """Given a linear story (no choices),
    When the illustration step runs,
    Then no cards are produced.
    """
    model = _FakeImageModel()
    cache = ArtifactCache(tmp_path / "story-1")

    result = illustrate_story(_linear_story(), _settings(), cache, transport=model.transport())

    assert result.card_images == {}


def test_each_card_is_drawn_against_the_character_sheet(tmp_path: Path) -> None:
    """Given the drift rule (never chain images),
    When a choice option's card is generated,
    Then its request carries exactly the character sheet as reference, and its
    prompt embeds the option label, the card prompt and the locked style.
    """
    model = _FakeImageModel()
    cache = ArtifactCache(tmp_path / "s1")
    story = branching_story_from_draft(
        make_draft(), story_id="s1", theme="the_little_boat", language="it"
    )

    result = illustrate_story(story, _settings(), cache, transport=model.transport())

    sheet_bytes = result.character_sheet.read_bytes()
    sheet_data_url = "data:image/png;base64," + base64.b64encode(sheet_bytes).decode()
    choice_page = next(p for p in story.pages if p.choice is not None)

    card_requests = [
        req
        for req in model.requests
        for label in (opt.label for opt in choice_page.choice.options)
        if label in req["messages"][0]["content"][0]["text"]
        and CARD_PROMPT.split("{label}")[0] in req["messages"][0]["content"][0]["text"]
    ]
    assert len(card_requests) == 2
    for request in card_requests:
        assert _image_parts(request) == [sheet_data_url]
        assert STYLE_PROMPT in request["messages"][0]["content"][0]["text"]
