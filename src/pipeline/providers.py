"""Provider access: Pydantic AI over OpenRouter, narration via OpenRouter audio.

The whole system has exactly one key; it arrives as SecretStr and is only
unwrapped at the transport boundary — never logged, never repr'd.

Narration uses Gemini 3.1 Flash TTS (google/gemini-3.1-flash-tts-preview) through
OpenRouter's OpenAI-compatible POST /audio/speech endpoint, returning raw
audio bytes with no timestamps (ADR-008). Word timings are reconstructed by
a Deepgram STT pass at slice 6, not here.
"""

import httpx
from pydantic import BaseModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from src.config import Settings
from src.observability import build_traced_openai_client, typed_traceable
from src.pipeline.models import Language


def build_model(model_id: str, settings: Settings) -> OpenAIChatModel:
    if settings.langsmith_tracing:
        provider = OpenAIProvider(openai_client=build_traced_openai_client(settings))
    else:
        provider = OpenAIProvider(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key.get_secret_value(),
        )
    return OpenAIChatModel(model_id, provider=provider)


class NarrationResult(BaseModel):
    audio: bytes


class NarrationClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self._settings = settings
        self._client = httpx.Client(
            base_url=settings.openrouter_base_url,
            headers={"Authorization": f"Bearer {settings.openrouter_api_key.get_secret_value()}"},
            transport=transport,
            timeout=120.0,
        )

    def __repr__(self) -> str:
        return f"NarrationClient(model={self._settings.narration_model!r})"

    @typed_traceable(name="narration.synthesize")
    def synthesize(self, text: str, language: Language = "it") -> NarrationResult:
        voice = self._settings.narration_voices.get(language, "alloy")
        response = self._client.post(
            "/audio/speech",
            json={
                "model": self._settings.narration_model,
                "input": text,
                "voice": voice,
                "response_format": self._settings.narration_response_format,
            },
        )
        response.raise_for_status()
        return NarrationResult(audio=response.content)
