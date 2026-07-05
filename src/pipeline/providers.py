"""Provider access: Pydantic AI over OpenRouter, ElevenLabs over httpx.

The whole system has exactly two keys; both arrive as SecretStr and are
only unwrapped at the transport boundary — never logged, never repr'd.
"""

import base64
from typing import Any

import httpx
from pydantic import BaseModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from src.config import Settings


def build_model(model_id: str, settings: Settings) -> OpenAIChatModel:
    provider = OpenAIProvider(
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key.get_secret_value(),
    )
    return OpenAIChatModel(model_id, provider=provider)


class NarrationResult(BaseModel):
    audio: bytes
    alignment: dict[str, Any]


class NarrationClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self._settings = settings
        self._client = httpx.Client(
            base_url=settings.elevenlabs_base_url,
            headers={"xi-api-key": settings.elevenlabs_api_key.get_secret_value()},
            transport=transport,
            timeout=120.0,
        )

    def __repr__(self) -> str:
        return f"NarrationClient(voice_id={self._settings.elevenlabs_voice_id!r})"

    def synthesize(self, text: str) -> NarrationResult:
        response = self._client.post(
            f"/v1/text-to-speech/{self._settings.elevenlabs_voice_id}/with-timestamps",
            params={"output_format": "mp3_44100_128"},
            json={"text": text, "model_id": self._settings.elevenlabs_tts_model},
        )
        response.raise_for_status()
        payload = response.json()
        return NarrationResult(
            audio=base64.b64decode(payload["audio_base64"]),
            alignment=payload["alignment"],
        )
