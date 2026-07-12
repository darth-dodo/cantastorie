"""Narration step (AI-391): Voxtral TTS via OpenRouter, no timestamps.

Every page is narrated with the single narrator voice through OpenRouter's
POST /audio/speech endpoint (ADR-004). Voxtral returns raw audio bytes with
no word or character timestamps — page timings stay empty until reading mode
(slice 6) reconstructs them via a Deepgram STT transcription pass.

Cache contract (docs/architecture.md "Content-addressed caching"): narration
is keyed on page text + voice ID + model/settings, so unchanged text costs
zero TTS calls.

Audio format: mp3 (the format providers.py requests) — mp3 decodes everywhere
iOS Safari included, at bedtime-speech quality; opus saves bytes but its Safari
support is too patchy for a player that must never stall.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Literal

from src.pipeline.cache import ArtifactCache, cache_key, run_step
from src.pipeline.models import Language, Page, PageAudio
from src.pipeline.providers import NarrationClient

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from mypy_boto3_s3 import S3Client

    from src.config import Settings

AUDIO_SUFFIX = ".mp3"
CONTENT_HASH_LENGTH = 16

PAGE_STEP = "narrate"
UTTERANCE_STEP = "utterances"

UtteranceName = Literal["shelf_greeting", "story_start", "end_prompt"]

# Final Italian copy, verbatim from docs/product.md **Spoken Prompts** —
# the slice-1 set: **Shelf greeting**, **Story start**, **End prompt**.
IT_UTTERANCES: Mapping[UtteranceName, str] = {
    "shelf_greeting": "Ciao! Quale storia ascoltiamo oggi?",
    "story_start": "Si parte!",
    "end_prompt": "Fine! Ancora, o un'altra storia?",
}


def _synthesize_cached(
    text: str,
    language: Language,
    settings: Settings,
    client: NarrationClient,
    cache: ArtifactCache,
    step: str,
) -> Path:
    """Synthesize text once, persisting audio under one key.

    A cold cache costs exactly one TTS call; a warm cache costs zero.
    """
    voice = settings.narration_voices.get(language, "alloy")
    inputs: dict[str, object] = {
        "text": text,
        "voice": voice,
        "model_id": settings.narration_model,
        "output_format": settings.narration_response_format,
    }
    key = cache_key(inputs)

    def synthesize() -> bytes:
        return client.synthesize(text, language).audio

    run_step(cache, step, inputs, synthesize, suffix=AUDIO_SUFFIX)

    return cache.story_dir / step / f"{key}{AUDIO_SUFFIX}"


def narrate_pages(
    pages: list[Page],
    language: Language,
    settings: Settings,
    cache: ArtifactCache,
    client: NarrationClient | None = None,
) -> list[Page]:
    """Narrate every page with the single narrator voice.

    Returns pages with audio attached: the cached mp3 path plus empty word
    timings (Voxtral returns no timestamps; Deepgram STT reconstructs them
    at slice 6). Unchanged page text is a pure cache lookup — zero TTS calls.
    """
    client = client or NarrationClient(settings)
    narrated: list[Page] = []
    for page in pages:
        audio_path = _synthesize_cached(page.text, language, settings, client, cache, PAGE_STEP)
        audio = PageAudio(file=str(audio_path), timings=[])
        narrated.append(page.model_copy(update={"audio": audio}))
    return narrated


def synthesize_utterances(
    settings: Settings,
    cache: ArtifactCache,
    out_dir: Path,
    language: Language = "it",
    utterances: Mapping[UtteranceName, str] = IT_UTTERANCES,
    client: NarrationClient | None = None,
    *,
    s3_client: S3Client | None = None,
) -> dict[UtteranceName, Path]:
    """Produce the spoken-prompt assets: prompts/{lang}/{name}.{hash}.mp3.

    Filenames embed a hash of the audio content, so published prompt assets
    are immutable and cache-forever (docs/architecture.md "R2 layout").
    Synthesis goes through the same content-addressed cache as pages. When
    s3_client is provided, staged prompts are uploaded to
    pending/staged/prompts/{lang}/ for the workshop to read from anywhere.
    """
    client = client or NarrationClient(settings)
    produced: dict[UtteranceName, Path] = {}
    for name, text in utterances.items():
        audio_path = _synthesize_cached(text, language, settings, client, cache, UTTERANCE_STEP)
        audio = audio_path.read_bytes()
        content_hash = hashlib.sha256(audio).hexdigest()[:CONTENT_HASH_LENGTH]
        destination = out_dir / "prompts" / language / f"{name}.{content_hash}{AUDIO_SUFFIX}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(audio)
        produced[name] = destination

    if s3_client is not None:
        from src.pipeline.publish import CONTENT_TYPES, STAGED_PREFIX  # noqa: PLC0415

        bucket = settings.pending_bucket
        for _name, path in produced.items():
            s3_client.put_object(
                Bucket=bucket,
                Key=f"{STAGED_PREFIX}/prompts/{language}/{path.name}",
                Body=path.read_bytes(),
                ContentType=CONTENT_TYPES.get(path.suffix, "application/octet-stream"),
            )

    return produced
