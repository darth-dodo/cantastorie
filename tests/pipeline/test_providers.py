"""Behavior specs for provider wiring: OpenRouter via pydantic-ai, narration via OpenRouter.

docs/architecture.md: one API key total, living only in the pipeline
environment — never in the browser, never in logs.
"""

import json

import httpx
import pytest
from pydantic import SecretStr

from src.config import Settings
from src.pipeline.providers import NarrationClient, build_model


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-test"),
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


def test_narration_posts_to_openrouter_audio_speech_and_returns_raw_audio() -> None:
    """Given Gemini configured in settings,
    When text is synthesized,
    Then the client POSTs to /audio/speech with the OpenRouter bearer key and
    the narration model id, and returns the raw audio bytes — no timestamps
    (ADR-008: Gemini returns raw audio; Deepgram STT reconstructs timings later).
    """
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"mp3-bytes", headers={"Content-Type": "audio/mpeg"})

    client = NarrationClient(_settings(), transport=httpx.MockTransport(handler))
    result = client.synthesize("shh, shh", "it")

    assert result.audio == b"mp3-bytes"
    assert "/audio/speech" in str(seen["url"])
    assert seen["auth"] == "Bearer sk-or-test"
    assert seen["body"] == {
        "model": "google/gemini-3.1-flash-tts-preview",
        "input": "shh, shh",
        "voice": "Kore",
        "response_format": "pcm",
    }


def test_pcm_from_openrouter_is_wrapped_into_a_wav_container() -> None:
    """Given Gemini returns raw PCM (it rejects response_format="mp3"),
    When the client synthesizes,
    Then the raw frames are wrapped into a WAV container — the bytes start with
    the RIFF magic and the client still returns them as NarrationResult.audio,
    so decodeAudioData can play them without a separate container step.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"\x00\x01" * 100,
            headers={"Content-Type": "audio/pcm;rate=24000;channels=1"},
        )

    client = NarrationClient(_settings(), transport=httpx.MockTransport(handler))
    result = client.synthesize("Ciao!", "it")

    assert result.audio[:4] == b"RIFF"
    assert len(result.audio) > 200  # WAV header + frames


def test_a_400_surfaces_openrouter_error_body_not_just_the_status() -> None:
    """Given OpenRouter rejects the request (e.g. bad model, wrong format),
    When the client synthesizes,
    Then the raised HTTPStatusError carries OpenRouter's reason in its message
    so the failure is self-diagnosing instead of a bare '400 Bad Request'.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            content=b'{"error":{"message":"Gemini TTS only supports response_format=\\"pcm\\". Got \\"mp3\\".","code":400}}',
            headers={"Content-Type": "application/json"},
        )

    client = NarrationClient(_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        client.synthesize("Ciao!", "it")
    assert "400" in str(excinfo.value)
    assert "only supports response_format" in str(excinfo.value)


def test_the_narration_client_never_leaks_its_key_when_rendered() -> None:
    """Given a narration client holding a real key,
    When the client is rendered via repr (as a log line would),
    Then the key's secret value does not appear — keys only in env, never logged.
    """
    client = NarrationClient(_settings())
    assert "sk-or-test" not in repr(client)
