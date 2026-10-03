"""Behavior specs for the image safety step (B4, AI-476).

**Calm pictures** (docs/product.md "Safety") is judged on the rendered
images, not on the story text: a cross-family vision judge sees every page,
choice card and cover and verdicts three criteria — no text, nothing
frightening, calm. A failing image is regenerated up to a bound, then the
story is rejected. The judge goes through the artifact cache, so an unchanged
image costs zero judge calls. Every provider is mocked; zero network.
"""

import base64
import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from pydantic_ai.messages import BinaryContent, ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from src.config import Settings
from src.pipeline.cache import ArtifactCache
from src.pipeline.models import (
    IMAGE_SAFETY_CRITERIA,
    SAFETY_RULES,
    ChoiceOption,
    ChoicePoint,
    ImageSafetyReport,
    ImageSafetyVerdict,
    Page,
    Story,
)
from src.pipeline.providers import build_model
from src.pipeline.steps.image_safety import (
    IMAGE_SAFETY_MAX_REGENERATIONS,
    IMAGE_SAFETY_TEMPERATURE,
    ImageSafetyRejectedError,
    illustrate_safely,
    judge_image,
)
from src.pipeline.steps.safety import SAFETY_INSTRUCTIONS

SCARY_PAGE = "The little boat meets a friendly gull, page 3."


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-test"),
        content_dir=tmp_path / "content",
    )


def _story(*, branching: bool = False) -> Story:
    texts = [f"The little boat rocks, page {n}." for n in range(1, 6)]
    texts[2] = SCARY_PAGE
    pages = [
        Page(id=f"p{n}", text=text, next_page=f"p{n + 1}" if n < len(texts) else None)
        for n, text in enumerate(texts, start=1)
    ]
    if branching:
        pages[-1] = pages[-1].model_copy(
            update={
                "next_page": None,
                "choice": ChoicePoint(
                    options=(
                        ChoiceOption(label="the moon", next_page="p1"),
                        ChoiceOption(label="the sea", next_page="p2"),
                    )
                ),
            }
        )
    return Story(
        id="story-1",
        language="it",
        title="La barchetta sonnolenta",
        theme="the_little_boat",
        shape="branching" if branching else "linear",
        pages=pages,
    )


class _Images:
    """OpenRouter image double: a fresh PNG per call, SCARY for the marked page.

    ``scary_calls`` is how many generations of the marked page come back
    frightening before it turns calm (``None``: frightening forever).
    """

    def __init__(self, scary_calls: int | None = 1) -> None:
        self.scary_calls = scary_calls
        self.prompts: list[str] = []

    def _png(self, prompt: str) -> bytes:
        n = len(self.prompts)
        marked = SCARY_PAGE in prompt
        seen = sum(SCARY_PAGE in p for p in self.prompts)
        scary = marked and (self.scary_calls is None or seen <= self.scary_calls)
        return (b"SCARY" if scary else b"CALM") + f":{n}:{prompt}".encode()

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            prompt = json.loads(request.content)["messages"][0]["content"][0]["text"]
            self.prompts.append(prompt)
            png = self._png(prompt)
            data_url = "data:image/png;base64," + base64.b64encode(png).decode()
            message = {"role": "assistant", "images": [{"image_url": {"url": data_url}}]}
            return httpx.Response(200, json={"choices": [{"message": message}]})

        return httpx.MockTransport(handler)

    def calls_for(self, text: str) -> int:
        return sum(text in p for p in self.prompts)


def _report(failing: dict[str, str] | None = None) -> dict[str, object]:
    failing = failing or {}
    return {
        "verdicts": [
            {"criterion": c, "passed": c not in failing, "reason": failing.get(c, "ok")}
            for c in IMAGE_SAFETY_CRITERIA
        ]
    }


class VisionJudge(FunctionModel):
    """A vision-judge double: fails any image whose bytes carry SCARY."""

    def __init__(self) -> None:
        self.calls = 0
        self.images: list[BinaryContent] = []
        self.temperatures: list[object] = []

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.calls += 1
            self.temperatures.append((info.model_settings or {}).get("temperature"))
            images = [
                item
                for message in messages
                for part in getattr(message, "parts", [])
                for item in (part.content if isinstance(part.content, list) else [])
                if isinstance(item, BinaryContent)
            ]
            assert len(images) == 1, "exactly one image per judge call"
            self.images.append(images[0])
            scary = images[0].data.startswith(b"SCARY")
            args = _report({"nothing_frightening": "a looming shadow"} if scary else None)
            assert info.output_tools, "structured output expected"
            return ModelResponse(
                parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=args)]
            )

        super().__init__(respond)


# --- the text gate no longer pretends to judge pictures -----------------------


def test_calm_pictures_is_not_a_text_gate_rule() -> None:
    """Given the text safety gate runs before any image exists,
    Then calm_pictures is neither a text rule nor in the judge's instructions.
    """
    assert "calm_pictures" not in SAFETY_RULES
    assert "calm_pictures" not in SAFETY_INSTRUCTIONS


def test_an_image_report_must_cover_all_three_criteria_exactly_once() -> None:
    verdicts = [
        ImageSafetyVerdict(criterion=c, passed=True, reason="ok") for c in IMAGE_SAFETY_CRITERIA
    ]
    assert ImageSafetyReport(verdicts=verdicts).passed
    assert set(IMAGE_SAFETY_CRITERIA) == {"no_text", "nothing_frightening", "calm"}
    with pytest.raises(ValidationError):
        ImageSafetyReport(verdicts=verdicts[:-1])


def test_the_vision_judge_must_be_a_different_family_than_the_image_model() -> None:
    with pytest.raises(ValidationError, match="image_safety_model"):
        Settings(
            _env_file=None,
            openrouter_api_key=SecretStr("sk-or-test"),
            image_model="google/gemini-3.1-flash-lite-image",
            image_safety_model="google/gemini-2.5-flash",
        )


def test_the_default_vision_judge_is_cross_family() -> None:
    settings = Settings(_env_file=None, openrouter_api_key=SecretStr("sk-or-test"))
    assert settings.image_safety_model.split("/")[0] != settings.image_model.split("/")[0]


# --- judging --------------------------------------------------------------------


def test_a_passing_story_is_unaffected_and_every_published_image_is_judged(
    tmp_path: Path,
) -> None:
    """Given every rendered image is calm,
    When illustrate_safely runs,
    Then each page, card and the cover is judged once at temperature 0,
    no image is regenerated, and the set equals plain illustration.
    """
    images = _Images(scary_calls=0)
    judge = VisionJudge()
    story = _story(branching=True)
    settings = _settings(tmp_path)
    cache = ArtifactCache(tmp_path / "story")

    result = illustrate_safely(
        story, settings, cache, transport=images.transport(), judge_model=judge
    )

    # 5 pages + 2 cards + 1 cover are shown to a child; the sheet is not.
    assert judge.calls == 8
    assert set(judge.temperatures) == {IMAGE_SAFETY_TEMPERATURE}
    assert all(img.media_type == "image/png" for img in judge.images)
    assert len(images.prompts) == 1 + 5 + 2 + 1  # sheet + pages + cards + cover
    assert set(result.page_images) == {f"p{n}" for n in range(1, 6)}
    assert len(result.card_images) == 2


def test_a_rejected_page_is_regenerated_and_then_passes(tmp_path: Path) -> None:
    """Given the judge rejects page 3's first render as frightening,
    When illustrate_safely runs,
    Then page 3 alone is regenerated once and the calm render is kept.
    """
    images = _Images(scary_calls=1)
    judge = VisionJudge()
    story = _story()

    result = illustrate_safely(
        story,
        _settings(tmp_path),
        ArtifactCache(tmp_path / "story"),
        transport=images.transport(),
        judge_model=judge,
    )

    assert images.calls_for(SCARY_PAGE) == 2
    assert images.calls_for("page 1.") == 1
    assert result.page_images["p3"].read_bytes().startswith(b"CALM")


def test_a_page_that_keeps_failing_rejects_the_story_after_the_bound(tmp_path: Path) -> None:
    """Given page 3 renders frightening every time,
    When illustrate_safely runs,
    Then page 3 is generated 1 + IMAGE_SAFETY_MAX_REGENERATIONS times
    and the story is rejected with a reason naming the page and criterion.
    """
    images = _Images(scary_calls=None)
    judge = VisionJudge()

    with pytest.raises(ImageSafetyRejectedError) as excinfo:
        illustrate_safely(
            _story(),
            _settings(tmp_path),
            ArtifactCache(tmp_path / "story"),
            transport=images.transport(),
            judge_model=judge,
        )

    assert images.calls_for(SCARY_PAGE) == 1 + IMAGE_SAFETY_MAX_REGENERATIONS
    message = str(excinfo.value)
    assert "story-1" in message
    assert "page p3" in message
    assert "nothing_frightening" in message
    assert "a looming shadow" in message


def test_a_rerun_hits_the_cache_for_images_and_verdicts(tmp_path: Path) -> None:
    """Given a story that passed after one regeneration,
    When illustrate_safely reruns unchanged,
    Then it makes zero image calls and zero judge calls, with the same result.
    """
    settings = _settings(tmp_path)
    cache = ArtifactCache(tmp_path / "story")
    first = illustrate_safely(
        _story(),
        settings,
        cache,
        transport=_Images(scary_calls=1).transport(),
        judge_model=VisionJudge(),
    )

    images, judge = _Images(scary_calls=1), VisionJudge()
    again = illustrate_safely(
        _story(), settings, cache, transport=images.transport(), judge_model=judge
    )

    assert images.prompts == []
    assert judge.calls == 0
    assert again == first


def test_the_judge_sends_the_image_as_a_base64_data_url_through_openrouter(
    tmp_path: Path,
) -> None:
    """Given the real OpenAI-compatible model over a mocked OpenRouter,
    When one image is judged,
    Then the request carries the PNG as a data:image/png;base64 URL.
    """
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        tool = body["tools"][0]["function"]["name"]
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "openai/gpt-4.1-mini",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": tool, "arguments": json.dumps(_report())},
                                }
                            ],
                        },
                    }
                ],
            },
        )

    settings = _settings(tmp_path)
    model = build_model(
        settings.image_safety_model,
        settings,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    png = b"\x89PNG-calm"

    report = judge_image(png, settings, ArtifactCache(tmp_path / "s"), model=model)

    assert report.passed
    sent = json.dumps(bodies[0])
    assert "data:image/png;base64," + base64.b64encode(png).decode() in sent
