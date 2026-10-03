"""Provider access: Pydantic AI over OpenRouter, narration via OpenRouter audio.

The whole system has exactly one key; it arrives as SecretStr and is only
unwrapped at the transport boundary — never logged, never repr'd.

Narration uses Gemini 3.1 Flash TTS (google/gemini-3.1-flash-tts-preview) through
OpenRouter's OpenAI-compatible POST /audio/speech endpoint. Gemini emits raw
PCM (it rejects response_format="mp3"), so the client requests pcm and wraps
the returned frames into a WAV container here — WAV decodes everywhere
decodeAudioData runs, iOS Safari included. Word timings are reconstructed by
a Deepgram STT pass at slice 6, not here.
"""

import io
import wave

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from src.config import Settings
from src.observability import build_traced_openai_client, typed_traceable
from src.pipeline.models import Language


def build_model(
    model_id: str, settings: Settings, *, http_client: httpx.AsyncClient | None = None
) -> OpenAIChatModel:
    """A Pydantic AI chat model on OpenRouter, for every LLM step.

    OpenRouterProvider, not a bare OpenAIProvider: with the plain provider,
    pydantic-ai 2.5 profiles an ``openai/...`` id as a reasoning model and
    silently strips ``temperature`` — both safety judges would then sample at
    the provider default instead of 0. The client is built here (traced or
    plain) so ``openrouter_base_url`` stays configurable.
    """
    if settings.langsmith_tracing:
        client = build_traced_openai_client(settings, http_client=http_client)
    else:
        client = AsyncOpenAI(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key.get_secret_value(),
            http_client=http_client,
        )
    return OpenAIChatModel(model_id, provider=OpenRouterProvider(openai_client=client))


class NarrationResult(BaseModel):
    audio: bytes


def _parse_pcm_params(content_type: str) -> tuple[int, int]:
    rate, channels = 24000, 1
    for token in content_type.split(";"):
        t = token.strip().lower()
        if t.startswith("rate="):
            rate = int(t.split("=", 1)[1])
        elif t.startswith("channels="):
            channels = int(t.split("=", 1)[1])
    return rate, channels


def _wrap_pcm_as_wav(pcm: bytes, content_type: str) -> bytes:
    rate, channels = _parse_pcm_params(content_type)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


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
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{response.status_code} {response.reason_phrase} from OpenRouter "
                f"/audio/speech: {response.text}",
                request=response.request,
                response=response,
            )
        content_type = response.headers.get("Content-Type", "")
        audio = response.content
        if content_type.startswith("audio/pcm"):
            audio = _wrap_pcm_as_wav(audio, content_type)
        return NarrationResult(audio=audio)
