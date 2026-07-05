"""Narration step (AI-359): one voice, timestamps, utterance assets.

Every page is narrated with the single narrator voice, and the character
alignment is captured and persisted with every TTS call — regenerating
timestamps later would mean re-buying all narration at double cost
(docs/architecture.md "Timestamps and utterances from day one").

Cache contract (docs/architecture.md "Content-addressed caching"): narration
is keyed on page text + voice ID + model/settings, so unchanged text costs
zero TTS calls. Each synthesis persists two artifacts under one key — the
mp3 audio and the raw character alignment JSON — and word timings are always
derived from the stored alignment, so a better word algorithm never needs a
re-synthesis.

Audio format: mp3_44100_128 (the format providers.py requests) — mp3 decodes
everywhere iOS Safari included, at bedtime-speech quality; opus saves bytes
but its Safari support is too patchy for a player that must never stall.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from src.config import Settings
from src.pipeline.cache import ArtifactCache, cache_key, run_step
from src.pipeline.models import Language, Page, PageAudio, WordTiming
from src.pipeline.providers import NarrationClient, NarrationResult

AUDIO_SUFFIX = ".mp3"
ALIGNMENT_SUFFIX = ".json"
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


def word_timings_from_alignment(alignment: Mapping[str, Any]) -> list[WordTiming]:
    """Collapse ElevenLabs' character alignment into one timing per word.

    A word is a whitespace-delimited run containing at least one alphanumeric
    character; leading/trailing punctuation («, !, », ...) stays outside both
    the word text and its timing, so highlighting never waits on punctuation
    silence. Internal apostrophes survive — "l'acqua" is one word, never two.
    """
    chars: list[str] = list(alignment["characters"])
    starts: list[float] = list(alignment["character_start_times_seconds"])
    ends: list[float] = list(alignment["character_end_times_seconds"])

    timings: list[WordTiming] = []
    i, n = 0, len(chars)
    while i < n:
        if chars[i].isspace():
            i += 1
            continue
        j = i
        while j < n and not chars[j].isspace():
            j += 1
        # Token spans [i, j); trim to its alphanumeric core so punctuation
        # stays out. A token with no alphanumeric core ("—") is not a word.
        first = next((k for k in range(i, j) if chars[k].isalnum()), None)
        if first is not None:
            last = next(k for k in range(j - 1, i - 1, -1) if chars[k].isalnum())
            timings.append(
                WordTiming(
                    word="".join(chars[first : last + 1]),
                    start_s=starts[first],
                    end_s=ends[last],
                )
            )
        i = j
    return timings


def _synthesize_cached(
    text: str,
    settings: Settings,
    client: NarrationClient,
    cache: ArtifactCache,
    step: str,
) -> tuple[Path, list[WordTiming]]:
    """Synthesize text once, persisting audio and alignment under one key.

    The producer is memoized so a cold cache costs exactly one TTS call even
    though two artifacts are stored; a warm cache costs zero (the sharp edge:
    load and store must use the same suffix per artifact, and they do).
    """
    inputs: dict[str, object] = {
        "text": text,
        "voice_id": settings.elevenlabs_voice_id,
        "model_id": settings.elevenlabs_tts_model,
        "output_format": "mp3_44100_128",  # the format providers.py requests
    }
    key = cache_key(inputs)

    fresh: NarrationResult | None = None

    def synthesize() -> NarrationResult:
        nonlocal fresh
        if fresh is None:
            fresh = client.synthesize(text)
        return fresh

    def alignment_bytes() -> bytes:
        return json.dumps(synthesize().alignment, ensure_ascii=False).encode("utf-8")

    run_step(cache, step, inputs, lambda: synthesize().audio, suffix=AUDIO_SUFFIX)
    alignment_raw = run_step(cache, step, inputs, alignment_bytes, suffix=ALIGNMENT_SUFFIX)

    audio_path = cache.story_dir / step / f"{key}{AUDIO_SUFFIX}"
    return audio_path, word_timings_from_alignment(json.loads(alignment_raw))


def narrate_pages(
    pages: Sequence[Page],
    settings: Settings,
    cache: ArtifactCache,
    client: NarrationClient | None = None,
) -> list[Page]:
    """Narrate every page with the single narrator voice.

    Returns pages with audio attached: the cached mp3 path plus word timings
    derived from the alignment captured on the same call. Unchanged page text
    is a pure cache lookup — zero TTS calls.
    """
    client = client or NarrationClient(settings)
    narrated: list[Page] = []
    for page in pages:
        audio_path, timings = _synthesize_cached(page.text, settings, client, cache, PAGE_STEP)
        audio = PageAudio(file=str(audio_path), timings=timings)
        narrated.append(page.model_copy(update={"audio": audio}))
    return narrated


def synthesize_utterances(
    settings: Settings,
    cache: ArtifactCache,
    out_dir: Path,
    language: Language = "it",
    utterances: Mapping[UtteranceName, str] = IT_UTTERANCES,
    client: NarrationClient | None = None,
) -> dict[UtteranceName, Path]:
    """Produce the spoken-prompt assets: prompts/{lang}/{name}.{hash}.mp3.

    Filenames embed a hash of the audio content, so published prompt assets
    are immutable and cache-forever (docs/architecture.md "R2 layout").
    Synthesis goes through the same content-addressed cache as pages; the
    actual R2 upload belongs to publish (AI-361), not this step.
    """
    client = client or NarrationClient(settings)
    produced: dict[UtteranceName, Path] = {}
    for name, text in utterances.items():
        audio_path, _ = _synthesize_cached(text, settings, client, cache, UTTERANCE_STEP)
        audio = audio_path.read_bytes()
        content_hash = hashlib.sha256(audio).hexdigest()[:CONTENT_HASH_LENGTH]
        destination = out_dir / "prompts" / language / f"{name}.{content_hash}{AUDIO_SUFFIX}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(audio)
        produced[name] = destination
    return produced
