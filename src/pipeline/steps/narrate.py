"""Narration step (AI-391): Gemini TTS via OpenRouter, no timestamps.

Every page is narrated with the single house voice ("Kore") through OpenRouter's
POST /audio/speech endpoint (ADR-008). Gemini returns raw audio bytes with
no word or character timestamps — page timings stay empty until reading mode
(slice 6) reconstructs them via a Deepgram STT transcription pass.

Cache contract (docs/architecture.md "Content-addressed caching"): narration
is keyed on page text + voice ID + model/settings, so unchanged text costs
zero TTS calls.

Audio format: wav — Gemini TTS emits raw PCM (it rejects response_format="mp3"),
so providers.py requests pcm and wraps the frames into a WAV container here.
WAV decodes everywhere decodeAudioData runs, iOS Safari included, at bedtime-speech
quality.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Literal

from src.pipeline._parallel import parallel_map
from src.pipeline.cache import ArtifactCache, cache_key, run_step
from src.pipeline.models import Language, Page, PageAudio
from src.pipeline.providers import NarrationClient

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from mypy_boto3_s3 import S3Client

    from src.config import Settings
    from src.pipeline.models import ChoiceOption

AUDIO_SUFFIX = ".wav"
CONTENT_HASH_LENGTH = 16

PAGE_STEP = "narrate"
UTTERANCE_STEP = "utterances"

UtteranceName = Literal["shelf_greeting", "story_start", "end_prompt", "audio_retry", "offline"]

# Final Italian copy, verbatim from docs/product.md **Spoken Prompts** —
# the slice-1 set plus the slice-2 failure prompts (AI-367).
IT_UTTERANCES: Mapping[UtteranceName, str] = {
    "shelf_greeting": "Ciao! Quale storia ascoltiamo oggi?",
    "story_start": "Si parte!",
    "end_prompt": "Fine! Ancora, o un'altra storia?",
    "audio_retry": "Oh! La storia fa un pisolino. Tocca l'uccellino per svegliarla.",
    "offline": "Le nuvole hanno preso le storie. Riprova tra poco!",
}

# The spoken prompts for every language in the roster (H6, AI-481): a plain
# mapping keyed by Language. Each non-Italian set matches the Italian lines in
# meaning, tone and length — warm and simple for a young child at bedtime —
# rather than translating them word for word. A test fails CI if any Language
# lacks any UtteranceName.
UTTERANCE_TEXTS: Mapping[Language, Mapping[UtteranceName, str]] = {
    "it": IT_UTTERANCES,
    # Machine-drafted, pending native review. Same lines as the English and
    # Spanish columns of docs/product.md **Spoken Prompts**.
    "en": {
        "shelf_greeting": "Hello! Which story shall we hear today?",
        "story_start": "Here we go!",
        "end_prompt": "The end! Again, or another story?",
        "audio_retry": "Oh! The story is napping. Tap the bird to wake it.",
        "offline": "The clouds took our stories. Try again soon!",
    },
    # Machine-drafted, pending native review.
    "es": {
        "shelf_greeting": "¡Hola! ¿Qué cuento escuchamos hoy?",
        "story_start": "¡Allá vamos!",
        "end_prompt": "¡Fin! ¿Otra vez, u otro cuento?",
        "audio_retry": "¡Oh! El cuento está durmiendo. Toca el pajarito para despertarlo.",
        "offline": "Las nubes se llevaron los cuentos. ¡Inténtalo pronto!",
    },
    # Machine-drafted, pending native review.
    "el": {
        "shelf_greeting": "Γεια σου! Ποια ιστορία θα ακούσουμε σήμερα;",
        "story_start": "Ξεκινάμε!",
        "end_prompt": "Τέλος! Πάλι, ή μια άλλη ιστορία;",
        "audio_retry": "Ω! Η ιστορία παίρνει έναν υπνάκο. Άγγιξε το πουλάκι να την ξυπνήσεις.",
        "offline": "Τα σύννεφα πήραν τις ιστορίες. Δοκίμασε ξανά σε λίγο!",
    },
    # Machine-drafted, pending native review.
    "de": {
        "shelf_greeting": "Hallo! Welche Geschichte hören wir heute?",
        "story_start": "Los geht's!",
        "end_prompt": "Ende! Nochmal, oder eine andere Geschichte?",
        "audio_retry": "Oh! Die Geschichte macht ein Nickerchen. Tipp auf das Vögelchen, um sie zu wecken.",
        "offline": "Die Wolken haben die Geschichten mitgenommen. Versuch es gleich nochmal!",
    },
    # Machine-drafted, pending native review.
    "bg": {
        "shelf_greeting": "Здравей! Коя приказка ще слушаме днес?",
        "story_start": "Тръгваме!",
        "end_prompt": "Край! Отново, или друга приказка?",
        "audio_retry": "О! Приказката си подремва. Докосни птичето, за да я събудиш.",
        "offline": "Облаците взеха приказките. Опитай пак след малко!",
    },
    # Machine-drafted, pending native review.
    "ru": {
        "shelf_greeting": "Привет! Какую сказку послушаем сегодня?",
        "story_start": "Поехали!",
        "end_prompt": "Конец! Ещё раз или другую сказку?",
        "audio_retry": "Ой! Сказка задремала. Коснись птички, чтобы её разбудить.",
        "offline": "Облака унесли сказки. Попробуй ещё раз чуть позже!",
    },
    # Machine-drafted, pending native review.
    "mr": {
        "shelf_greeting": "नमस्कार! आज आपण कोणती गोष्ट ऐकूया?",
        "story_start": "चला, सुरू करूया!",
        "end_prompt": "गोष्ट संपली! पुन्हा ऐकूया, की दुसरी गोष्ट?",
        "audio_retry": "अरे! गोष्ट डुलकी घेतेय. तिला उठवायला छोट्या पक्ष्याला हात लाव.",
        "offline": "ढगांनी गोष्टी नेल्या. थोड्या वेळाने पुन्हा प्रयत्न कर!",
    },
}


def _narration_inputs(
    text: str,
    language: Language,
    settings: Settings,
    extra_inputs: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """The cache-key inputs for one narration: text + voice + model/settings."""
    inputs: dict[str, object] = {
        "text": text,
        "voice": settings.narration_voices.get(language, "alloy"),
        "model_id": settings.narration_model,
        "output_format": settings.narration_response_format,
    }
    if extra_inputs:
        inputs.update(extra_inputs)
    return inputs


def cached_utterance_audio(
    text: str, language: Language, settings: Settings, cache: ArtifactCache
) -> bytes | None:
    """The cached audio for one spoken prompt, or None on a cache miss.

    A pure lookup — never a TTS call — so a dry run can say which prompts a
    real run would pay for.
    """
    key = cache_key(_narration_inputs(text, language, settings))
    return cache.load(UTTERANCE_STEP, key, AUDIO_SUFFIX)


def utterance_filename(name: UtteranceName, audio: bytes) -> str:
    """The immutable published name of a prompt: {name}.{contenthash}.wav."""
    content_hash = hashlib.sha256(audio).hexdigest()[:CONTENT_HASH_LENGTH]
    return f"{name}.{content_hash}{AUDIO_SUFFIX}"


def _synthesize_cached(
    text: str,
    language: Language,
    settings: Settings,
    client: NarrationClient,
    cache: ArtifactCache,
    step: str,
    extra_inputs: Mapping[str, object] | None = None,
) -> Path:
    """Synthesize text once, persisting audio under one key.

    A cold cache costs exactly one TTS call; a warm cache costs zero.
    `extra_inputs` widens the cache key so distinct kinds of narration (e.g.
    a page vs. a spoken choice label) with identical text never collide.
    """
    inputs = _narration_inputs(text, language, settings, extra_inputs)
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

    Returns pages with audio attached: the cached wav path plus empty word
    timings (Gemini returns no timestamps; Deepgram STT reconstructs them
    at slice 6). Unchanged page text is a pure cache lookup — zero TTS calls.
    """
    client = client or NarrationClient(settings)

    def narrate_one(page: Page) -> Page:
        audio_path = _synthesize_cached(page.text, language, settings, client, cache, PAGE_STEP)
        return page.model_copy(update={"audio": PageAudio(file=str(audio_path), timings=[])})

    # Independent per page; fan out through the bounded pool (order preserved).
    return parallel_map(narrate_one, pages, settings.pipeline_media_concurrency)


def narrate_choice_labels(
    pages: list[Page],
    language: Language,
    settings: Settings,
    cache: ArtifactCache,
    *,
    client: NarrationClient | None = None,
) -> list[Page]:
    """Narrate the two spoken option labels on every choice page.

    Returns pages with each `option.audio` set to the cached wav plus empty
    word timings — same contract as page audio (Gemini returns no timestamps;
    Deepgram STT reconstructs them at slice 6). The narration cache key adds
    ``kind="choice_label"`` so a label and a page with identical text never
    collide. Non-choice pages pass through untouched (identity).
    """
    client = client or NarrationClient(settings)
    narrated: list[Page] = []
    for page in pages:
        if page.choice is None:
            narrated.append(page)
            continue
        options: list[ChoiceOption] = []
        for index, option in enumerate(page.choice.options):
            audio_path = _synthesize_cached(
                option.label,
                language,
                settings,
                client,
                cache,
                PAGE_STEP,
                extra_inputs={"kind": "choice_label"},
            )
            label_path = audio_path.with_name(f"{page.id}.opt{index}{AUDIO_SUFFIX}")
            label_path.write_bytes(audio_path.read_bytes())
            audio = PageAudio(file=str(label_path), timings=[])
            options.append(option.model_copy(update={"audio": audio}))
        choice = page.choice.model_copy(update={"options": tuple(options)})
        narrated.append(page.model_copy(update={"choice": choice}))
    return narrated


def synthesize_utterances(
    settings: Settings,
    cache: ArtifactCache,
    out_dir: Path,
    language: Language,
    utterances: Mapping[UtteranceName, str] | None = None,
    client: NarrationClient | None = None,
    *,
    s3_client: S3Client | None = None,
) -> dict[UtteranceName, Path]:
    """Produce the spoken-prompt assets: prompts/{lang}/{name}.{hash}.wav.

    ``utterances`` defaults to the language's own lines in UTTERANCE_TEXTS —
    never another language's.

    Filenames embed a hash of the audio content, so published prompt assets
    are immutable and cache-forever (docs/architecture.md "R2 layout").
    Synthesis goes through the same content-addressed cache as pages. When
    s3_client is provided, staged prompts are uploaded to
    pending/staged/prompts/{lang}/ for the workshop to read from anywhere.
    """
    client = client or NarrationClient(settings)
    if utterances is None:
        utterances = UTTERANCE_TEXTS[language]
    produced: dict[UtteranceName, Path] = {}
    for name, text in utterances.items():
        audio_path = _synthesize_cached(text, language, settings, client, cache, UTTERANCE_STEP)
        audio = audio_path.read_bytes()
        destination = out_dir / "prompts" / language / utterance_filename(name, audio)
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
