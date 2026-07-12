"""Behavior specs for the illustration step — the AI-360 acceptance behaviors.

**Watercolor boards** and **Calm pictures** (docs/product.md): one character
sheet anchors every board, the style prompt locks the watercolor look and
bans in-image text, and the content-addressed cache means an unchanged story
costs zero image calls.
"""

import base64
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from src.config import Settings
from src.pipeline.cache import ArtifactCache
from src.pipeline.models import Page, Story
from src.pipeline.steps.illustrate import (
    STYLE_PROMPT,
    ImageClient,
    illustrate_story,
)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-test"),
        image_model="google/gemini-2.5-flash-image",
    )


def _story(page_texts: list[str] | None = None) -> Story:
    texts = page_texts or [f"The little boat rocks, page {n}." for n in range(1, 11)]
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
    """MockTransport handler: answers like OpenRouter's image-generation API.

    Returns deterministic per-prompt PNG bytes in ``message.images`` (base64
    data URL), and records every request body so the specs can assert what
    was sent. Zero network anywhere.
    """

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


# --- The locked style ---------------------------------------------------


def test_the_style_prompt_locks_the_watercolor_boards_look() -> None:
    """Given the decision-log style (docs/product.md **Watercolor boards**),
    When the style prompt constant is inspected,
    Then it encodes soft watercolor, warm palette, rounded characters and
    nothing frightening — as one diffable, hashable module constant.
    """
    for phrase in ("watercolor", "warm palette", "rounded", "nothing frightening", "bedtime"):
        assert phrase.lower() in STYLE_PROMPT.lower()


def test_the_style_prompt_forbids_any_text_inside_images() -> None:
    """Given the **Calm pictures** safety rule (images contain no text),
    When the style prompt constant is inspected,
    Then the negative instruction against in-image text is part of it —
    every image request carries the ban.
    """
    assert "no text" in STYLE_PROMPT.lower()
    assert "no writing" in STYLE_PROMPT.lower()


# --- Sheet first, everything derived from it ----------------------------


def test_one_story_yields_a_sheet_ten_page_images_and_a_cover(tmp_path: Path) -> None:
    """Given a ten-page story and a mocked image model,
    When the illustration step runs,
    Then it persists exactly one character sheet, one image per page and a
    cover — twelve .png artifacts on disk, twelve model calls, no more.
    """
    model = _FakeImageModel()
    cache = ArtifactCache(tmp_path / "story-1")

    result = illustrate_story(_story(), _settings(), cache, transport=model.transport())

    assert len(result.page_images) == 10
    for path in [result.character_sheet, result.cover, *result.page_images.values()]:
        assert path.suffix == ".png"
        assert path.exists()
    assert len(model.requests) == 12  # 1 sheet + 10 pages + 1 cover


def test_every_page_receives_the_same_character_sheet_never_the_previous_page(
    tmp_path: Path,
) -> None:
    """Given the drift rule (docs/architecture.md: chaining page-to-page
    compounds drift),
    When the pages and cover are generated,
    Then the sheet request carries no reference image, and every page and
    cover request carries exactly one reference image: the sheet itself.
    """
    model = _FakeImageModel()
    cache = ArtifactCache(tmp_path / "story-1")

    result = illustrate_story(_story(), _settings(), cache, transport=model.transport())

    sheet_bytes = result.character_sheet.read_bytes()
    sheet_data_url = "data:image/png;base64," + base64.b64encode(sheet_bytes).decode()

    sheet_request, *derived_requests = model.requests
    assert _image_parts(sheet_request) == []  # the sheet is generated first, from nothing
    assert len(derived_requests) == 11  # 10 pages + 1 cover, all derived from the sheet
    for request in derived_requests:
        assert _image_parts(request) == [sheet_data_url]


def test_image_requests_go_to_openrouter_with_image_modality(tmp_path: Path) -> None:
    """Given OpenRouter as the one gateway (settings.image_model, no vendor SDKs),
    When any image is requested,
    Then the call targets the configured model with modalities image+text,
    and every prompt embeds the locked style prompt.
    """
    model = _FakeImageModel()
    cache = ArtifactCache(tmp_path / "story-1")

    illustrate_story(_story(), _settings(), cache, transport=model.transport())

    for request in model.requests:
        assert request["model"] == "google/gemini-2.5-flash-image"
        assert request["modalities"] == ["image", "text"]
        messages = request["messages"]
        assert isinstance(messages, list)
        assert STYLE_PROMPT in messages[0]["content"][0]["text"]


# --- Content-addressed caching ------------------------------------------


def test_rerunning_an_unchanged_story_makes_zero_image_calls(tmp_path: Path) -> None:
    """Given a story fully illustrated once,
    When the step runs again with unchanged inputs,
    Then zero image calls are made — sheet, pages and cover are all served
    from disk (store and load agree on the .png suffix).
    """
    cache = ArtifactCache(tmp_path / "story-1")
    first_model = _FakeImageModel()
    first = illustrate_story(_story(), _settings(), cache, transport=first_model.transport())

    second_model = _FakeImageModel()
    second = illustrate_story(_story(), _settings(), cache, transport=second_model.transport())

    assert len(second_model.requests) == 0
    assert second == first


def test_editing_one_pages_text_regenerates_only_that_pages_image(tmp_path: Path) -> None:
    """Given a story fully illustrated once,
    When page 5's text changes and the step re-runs,
    Then exactly one image call happens — page 5's, keyed on page text +
    character-sheet hash + style prompt + model; the sheet, the other seven
    pages and the cover are untouched.
    """
    cache = ArtifactCache(tmp_path / "story-1")
    texts = [f"The little boat rocks, page {n}." for n in range(1, 11)]
    first = illustrate_story(
        _story(texts), _settings(), cache, transport=_FakeImageModel().transport()
    )

    texts[4] = "The little boat finds a sleepy lighthouse."
    rerun_model = _FakeImageModel()
    second = illustrate_story(_story(texts), _settings(), cache, transport=rerun_model.transport())

    assert len(rerun_model.requests) == 1
    assert texts[4] in rerun_model.requests[0]["messages"][0]["content"][0]["text"]
    assert second.character_sheet == first.character_sheet
    assert second.cover == first.cover
    assert second.page_images["page-5"] != first.page_images["page-5"]
    unchanged = {page_id for page_id in first.page_images if page_id != "page-5"}
    for page_id in unchanged:
        assert second.page_images[page_id] == first.page_images[page_id]


def test_swapping_the_image_model_regenerates_everything(tmp_path: Path) -> None:
    """Given the model id is part of every cache key,
    When the configured image model changes,
    Then the sheet, all pages and the cover are regenerated — ten calls.
    """
    cache = ArtifactCache(tmp_path / "story-1")
    illustrate_story(_story(), _settings(), cache, transport=_FakeImageModel().transport())

    swapped = _settings().model_copy(update={"image_model": "openai/gpt-image-1"})
    rerun_model = _FakeImageModel()
    illustrate_story(_story(), swapped, cache, transport=rerun_model.transport())

    assert len(rerun_model.requests) == 12  # 1 sheet + 10 pages + 1 cover


# --- Transport hygiene ---------------------------------------------------


def test_the_image_client_never_leaks_its_key_when_rendered() -> None:
    """Given an image client holding a real key,
    When the client is rendered via repr (as a log line would),
    Then the key's secret value does not appear — keys only in env, never logged.
    """
    client = ImageClient(_settings())
    assert "sk-or-test" not in repr(client)
    client.close()


def test_a_reply_without_an_image_fails_loudly_instead_of_caching_junk(tmp_path: Path) -> None:
    """Given a model reply that carries text but no image,
    When the step runs,
    Then it raises instead of persisting a broken artifact — the cache only
    ever holds real images.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "sorry"}}]}
        )

    cache = ArtifactCache(tmp_path / "story-1")
    with pytest.raises(RuntimeError, match="no image"):
        illustrate_story(_story(), _settings(), cache, transport=httpx.MockTransport(handler))
    assert not list((tmp_path / "story-1").rglob("*.png"))
