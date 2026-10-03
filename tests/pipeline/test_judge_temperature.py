"""Wire-level spec: both safety judges really send temperature 0 (B4 review).

A FunctionModel sees the settings Pydantic AI *intends* to send, not what
reaches OpenRouter. pydantic-ai 2.5 profiles an ``openai/...`` id behind a
plain OpenAIProvider as a reasoning model and silently strips
``temperature``; behind OpenRouterProvider it does not. These specs read the
outgoing request body itself, on both the plain and the traced client path.
"""

import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from src.config import Settings
from src.pipeline.cache import ArtifactCache
from src.pipeline.models import IMAGE_SAFETY_CRITERIA, SAFETY_RULES, Page, Story
from src.pipeline.providers import build_model
from src.pipeline.steps.image_safety import IMAGE_SAFETY_MODEL_SETTINGS, judge_image
from src.pipeline.steps.safety import SAFETY_MODEL_SETTINGS, safety_gate


def _settings(*, tracing: bool) -> Settings:
    return Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-test"),
        langsmith_tracing=tracing,
    )


class _OpenRouter:
    """MockTransport answering a structured-output call; records every body."""

    def __init__(self, args: dict[str, object]) -> None:
        self.args = args
        self.bodies: list[dict[str, object]] = []

    def client(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.bodies.append(body)
            tool = body["tools"][0]["function"]["name"]
            call = {
                "id": "call-1",
                "type": "function",
                "function": {"name": tool, "arguments": json.dumps(self.args)},
            }
            message = {"role": "assistant", "content": None, "tool_calls": [call]}
            return httpx.Response(
                200,
                json={
                    "id": "x",
                    "object": "chat.completion",
                    "created": 0,
                    "model": body["model"],
                    "choices": [{"index": 0, "finish_reason": "tool_calls", "message": message}],
                },
            )

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _story() -> Story:
    return Story(
        id="story-1",
        language="it",
        title="La barchetta",
        theme="the_little_boat",
        shape="linear",
        pages=[Page(id="p1", text="The little boat rocks.")],
    )


@pytest.fixture(autouse=True)
def _langsmith_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    # The traced client path is exercised, but nothing may leave for LangSmith.
    for key in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_API_KEY"):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("tracing", [False, True], ids=["plain", "traced"])
def test_the_text_safety_gate_sends_temperature_zero(tmp_path: Path, tracing: bool) -> None:
    settings = _settings(tracing=tracing)
    wire = _OpenRouter(
        {"verdicts": [{"rule": r, "passed": True, "reason": "ok"} for r in SAFETY_RULES]}
    )
    model = build_model(settings.safety_model, settings, http_client=wire.client())

    safety_gate(_story(), settings, ArtifactCache(tmp_path), model=model)

    assert len(wire.bodies) == 1
    assert wire.bodies[0]["model"] == settings.safety_model
    assert wire.bodies[0].get("temperature") == 0


@pytest.mark.parametrize("tracing", [False, True], ids=["plain", "traced"])
def test_the_image_safety_judge_sends_temperature_zero(tmp_path: Path, tracing: bool) -> None:
    settings = _settings(tracing=tracing)
    wire = _OpenRouter(
        {
            "verdicts": [
                {"criterion": c, "passed": True, "reason": "ok"} for c in IMAGE_SAFETY_CRITERIA
            ]
        }
    )
    model = build_model(settings.image_safety_model, settings, http_client=wire.client())

    judge_image(b"\x89PNG-calm", settings, ArtifactCache(tmp_path), model=model)

    assert len(wire.bodies) == 1
    assert wire.bodies[0]["model"] == settings.image_safety_model
    assert wire.bodies[0].get("temperature") == 0


def test_every_model_targets_the_configured_openrouter_base_url() -> None:
    settings = Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-test"),
        openrouter_base_url="https://openrouter.example/api/v1",
    )
    model = build_model("anthropic/claude-sonnet-4.5", settings)
    assert str(model.base_url).startswith("https://openrouter.example/api/v1")
    assert model.system == "openrouter"


def test_a_reasoning_model_run_cannot_strip_temperature_from_later_runs(tmp_path: Path) -> None:
    """pydantic-ai pops sampling params from the settings dict *in place* when
    it profiles a model as reasoning. Given one such run, the shared judge
    settings must survive, so the next judge call still sends temperature 0.
    """
    settings = _settings(tracing=False)
    verdicts = [{"criterion": c, "passed": True, "reason": "ok"} for c in IMAGE_SAFETY_CRITERIA]
    reasoning = OpenAIChatModel(
        "openai/o3",
        provider=OpenAIProvider(
            base_url="https://openrouter.test/api/v1",
            api_key="sk-or-test",
            http_client=_OpenRouter({"verdicts": verdicts}).client(),
        ),
    )
    with pytest.warns(UserWarning, match="Sampling parameters"):
        judge_image(b"png-a", settings, ArtifactCache(tmp_path / "a"), model=reasoning)

    assert IMAGE_SAFETY_MODEL_SETTINGS.get("temperature") == 0
    assert SAFETY_MODEL_SETTINGS.get("temperature") == 0
    wire = _OpenRouter({"verdicts": verdicts})
    model = build_model(settings.image_safety_model, settings, http_client=wire.client())
    judge_image(b"png-b", settings, ArtifactCache(tmp_path / "b"), model=model)
    assert wire.bodies[0].get("temperature") == 0
