"""Behavior specs for provider wiring: OpenRouter via pydantic-ai, ElevenLabs via httpx.

docs/architecture.md: two API keys total, living only in the pipeline
environment — never in the browser, never in logs.
"""

import base64
import json

import httpx
from pydantic import SecretStr

from src.config import Settings
from src.pipeline.providers import NarrationClient, build_model


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-test"),
        elevenlabs_api_key=SecretStr("el-test"),
        elevenlabs_voice_id="voice-1",
    )


def test_every_llm_model_is_bound_to_openrouter_not_a_vendor_api() -> None:
    """Given a per-step model id from settings,
    When the pydantic-ai model is built,
    Then it carries that model id and targets the OpenRouter base URL —
    no direct vendor SDKs anywhere (docs/architecture.md **Factory**).
    """
    model = build_model("anthropic/claude-sonnet-4.5", _settings())
    assert model.model_name == "anthropic/claude-sonnet-4.5"
    assert "openrouter.ai" in str(model.base_url)


def test_narration_requests_timestamps_and_decodes_the_returned_audio() -> None:
    """Given an ElevenLabs voice configured in settings,
    When text is synthesized,
    Then the client POSTs to /with-timestamps with the xi-api-key header and
    model id, and returns decoded audio plus the raw character alignment
    (timestamps captured on every call — word timings arrive with AI-359).
    """
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["api_key"] = request.headers.get("xi-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "audio_base64": base64.b64encode(b"mp3-bytes").decode(),
                "alignment": {
                    "characters": ["s", "h"],
                    "character_start_times_seconds": [0.0, 0.1],
                },
            },
        )

    client = NarrationClient(_settings(), transport=httpx.MockTransport(handler))
    result = client.synthesize("shh, shh")

    assert result.audio == b"mp3-bytes"
    assert "characters" in result.alignment
    assert "/v1/text-to-speech/voice-1/with-timestamps" in str(seen["url"])
    assert seen["api_key"] == "el-test"
    assert seen["body"] == {"text": "shh, shh", "model_id": "eleven_multilingual_v2"}


def test_the_narration_client_never_leaks_its_key_when_rendered() -> None:
    """Given a narration client holding a real key,
    When the client is rendered via repr (as a log line would),
    Then the key's secret value does not appear — keys only in env, never logged.
    """
    client = NarrationClient(_settings())
    assert "el-test" not in repr(client)
