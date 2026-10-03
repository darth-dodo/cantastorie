"""Spoken prompts per language, as an operator step (H6, AI-481).

The five spoken prompts (narrate.py UTTERANCE_TEXTS) reach a child two ways:

**publish_prompts** narrates a language's lines through the narrate step,
uploads the WAVs under ``published/prompts/{lang}/{name}.{hash}.wav`` (the
immutable hashed scheme publish uses), and merges their URLs into the live
``published/{lang}/manifest.json`` ``prompts`` map. The manifest write goes
through publish.py's ``_write_manifest`` (IfMatch + retry), never a bare PUT.

**write_dev_prompts** writes the same audio as the same-origin fixtures under
``src/static/content/{lang}/prompts/`` with fixed names. The player fetches
``offline.wav`` from there in production too, because the offline screen
shows exactly when R2 can't be reached (main.js).

Both share one content-addressed cache (``{content_dir}/_prompts``), so a
rerun, or the second of the two, costs zero TTS calls. Uploads skip objects
that already hold the same bytes, and an unchanged manifest isn't rewritten,
so a repeat run writes nothing. ``dry_run`` makes no TTS call and writes
nothing; it reports which lines a real run would pay for.

A language whose live manifest already lists all five prompts is **skipped
(complete)** unless ``force`` is set. Italian's prompts arrive with every
story publish (publish_story copies ``pending/staged/prompts/it/``), so
re-narrating them here would overwrite reviewed audio only for the next
story publish to flip it back.
"""

from __future__ import annotations

import json
import tempfile
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from src.pipeline.cache import ArtifactCache
from src.pipeline.publish import (
    MANIFEST_PROMPT_KEYS,
    PUBLISHED_PREFIX,
    _build_client,
    _load_manifest,
    _upload_if_new,
    _write_manifest,
)
from src.pipeline.steps.narrate import (
    UTTERANCE_TEXTS,
    UtteranceName,
    cached_utterance_audio,
    synthesize_utterances,
    utterance_filename,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from mypy_boto3_s3 import S3Client

    from src.config import Settings
    from src.pipeline.models import Language
    from src.pipeline.providers import NarrationClient

# Not a story id: derive_story_id slugs never start with an underscore.
PROMPTS_CACHE_DIR = "_prompts"
IMMUTABLE = "public, max-age=31536000, immutable"

DEV_CONTENT_DIR = Path(__file__).resolve().parent.parent / "static" / "content"
DEV_URL_BASE = "/static/content"
# The fixed dev fixture names (scripts/generate_dev_story.py). offline.wav is
# load-bearing: main.js fetches /static/content/{lang}/prompts/offline.wav.
DEV_PROMPT_FILES: dict[UtteranceName, str] = {
    "shelf_greeting": "greeting.wav",
    "story_start": "story-start.wav",
    "end_prompt": "end.wav",
    "audio_retry": "audio-retry.wav",
    "offline": "offline.wav",
}


class PromptLine(BaseModel):
    name: str
    manifest_key: str
    text: str
    cached: bool  # True: the audio is already in the cache, so no TTS call
    url: str | None  # where it is (or would be) served; None if not yet narrated


class PromptPublishResult(BaseModel):
    language: str
    dry_run: bool
    target: str  # the manifest the run updates
    lines: list[PromptLine]
    uploaded: list[str]
    skipped: list[str]
    manifest_changed: bool  # on a dry run, whether a real run would change it
    # Why the run did nothing: "complete" (the live manifest already lists all
    # five prompts) or "no manifest" (refused: the language has no live
    # manifest to merge into). None when the run went ahead.
    skip_reason: str | None = None

    @property
    def tts_calls(self) -> int:
        if self.skip_reason is not None:
            return 0
        return sum(not line.cached for line in self.lines)


def has_every_prompt(manifest: Mapping[str, Any]) -> bool:
    """Whether a manifest's prompts map already lists all five spoken prompts."""
    prompts = manifest.get("prompts") or {}
    return all(prompts.get(key) for key in MANIFEST_PROMPT_KEYS.values())


def prompt_cache(settings: Settings) -> ArtifactCache:
    return ArtifactCache(settings.content_dir / PROMPTS_CACHE_DIR)


def _plan_lines(
    language: Language, settings: Settings, url_for: Mapping[UtteranceName, str | None]
) -> list[PromptLine]:
    cache = prompt_cache(settings)
    return [
        PromptLine(
            name=name,
            manifest_key=MANIFEST_PROMPT_KEYS[name],
            text=text,
            cached=cached_utterance_audio(text, language, settings, cache) is not None,
            url=url_for.get(name),
        )
        for name, text in UTTERANCE_TEXTS[language].items()
    ]


def _merged_prompts(manifest: dict[str, Any], prompt_urls: dict[str, str]) -> dict[str, Any]:
    return {**manifest.get("prompts", {}), **prompt_urls}


def _narrate(
    language: Language, settings: Settings, client: NarrationClient | None
) -> dict[UtteranceName, tuple[str, bytes]]:
    """Each prompt's hashed file name and audio, via the narrate step's cache.

    synthesize_utterances also copies the audio into an output folder; a
    throwaway one keeps the working tree clean (the cache is the keeper).
    """
    with tempfile.TemporaryDirectory() as scratch:
        produced = synthesize_utterances(
            settings, prompt_cache(settings), Path(scratch), language, client=client
        )
        return {name: (path.name, path.read_bytes()) for name, path in produced.items()}


def publish_prompts(
    language: Language,
    settings: Settings,
    *,
    client: S3Client | None = None,
    narration_client: NarrationClient | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> PromptPublishResult:
    """Narrate, upload, and list one language's spoken prompts on the shared shelf.

    A language whose live manifest already lists all five prompts is skipped
    (``skip_reason="complete"``, no TTS, no write) unless ``force`` is set.
    A language with no live manifest is refused (``skip_reason="no
    manifest"``) unless ``force`` is set: creating one would publish a shelf
    with no stories. ``write_dev_prompts`` never creates one either.
    """
    if not settings.r2_public_base:
        raise ValueError(
            "R2_PUBLIC_BASE must be set before publishing prompts — manifest URLs would be relative"
        )
    client = client or _build_client(settings)
    bucket = settings.r2_bucket
    public_base = settings.r2_public_base.rstrip("/")
    manifest_key = f"{PUBLISHED_PREFIX}/{language}/manifest.json"
    load = partial(_load_manifest, client, bucket, language)

    def url(file_name: str) -> str:
        return f"{public_base}/prompts/{language}/{file_name}"

    live, etag = load()
    if not force and etag is None:
        return PromptPublishResult(
            language=language,
            dry_run=dry_run,
            target=manifest_key,
            lines=_plan_lines(language, settings, {}),
            uploaded=[],
            skipped=[],
            manifest_changed=False,
            skip_reason="no manifest",
        )
    if not force and has_every_prompt(live):
        live_prompts = live["prompts"]
        listed: dict[UtteranceName, str | None] = {
            name: live_prompts.get(MANIFEST_PROMPT_KEYS[name]) for name in UTTERANCE_TEXTS[language]
        }
        return PromptPublishResult(
            language=language,
            dry_run=dry_run,
            target=manifest_key,
            lines=_plan_lines(language, settings, listed),
            uploaded=[],
            skipped=[],
            manifest_changed=False,
            skip_reason="complete",
        )

    if dry_run:
        cache = prompt_cache(settings)
        url_for: dict[UtteranceName, str | None] = {}
        for name, text in UTTERANCE_TEXTS[language].items():
            audio = cached_utterance_audio(text, language, settings, cache)
            url_for[name] = None if audio is None else url(utterance_filename(name, audio))
        lines = _plan_lines(language, settings, url_for)
        known = {line.manifest_key: line.url for line in lines if line.url is not None}
        changed = len(known) < len(lines) or _merged_prompts(live, known) != live.get("prompts", {})
        return PromptPublishResult(
            language=language,
            dry_run=True,
            target=manifest_key,
            lines=lines,
            uploaded=[],
            skipped=[],
            manifest_changed=changed,
        )

    before = _plan_lines(language, settings, {})
    produced = _narrate(language, settings, narration_client)

    # Audio first, manifest last: a listed prompt is always fetchable.
    uploaded: list[str] = []
    skipped: list[str] = []
    prompt_urls: dict[str, str] = {}
    published_at: dict[str, str] = {}
    for name, (file_name, audio) in produced.items():
        key = f"{PUBLISHED_PREFIX}/prompts/{language}/{file_name}"
        wrote = _upload_if_new(client, bucket, key, audio, "audio/wav", IMMUTABLE)
        (uploaded if wrote else skipped).append(key)
        published_at[name] = url(file_name)
        prompt_urls[MANIFEST_PROMPT_KEYS[name]] = url(file_name)

    def mutate(manifest: dict[str, Any]) -> None:
        manifest["prompts"] = _merged_prompts(manifest, prompt_urls)

    _, manifest_written = _write_manifest(client, bucket, manifest_key, load=load, mutate=mutate)
    lines = [line.model_copy(update={"url": published_at.get(line.name)}) for line in before]
    return PromptPublishResult(
        language=language,
        dry_run=False,
        target=manifest_key,
        lines=lines,
        uploaded=uploaded,
        skipped=skipped,
        manifest_changed=manifest_written,
    )


def write_dev_prompts(
    language: Language,
    settings: Settings,
    *,
    content_dir: Path = DEV_CONTENT_DIR,
    narration_client: NarrationClient | None = None,
    dry_run: bool = False,
) -> PromptPublishResult:
    """Write one language's spoken prompts as the same-origin dev fixtures.

    Audio lands at ``{content_dir}/{lang}/prompts/{fixed name}``. An existing
    ``{content_dir}/{lang}/manifest.json`` gets its prompts map pointed at
    them; a missing one is never created.
    """
    lang_dir = content_dir / language
    manifest_path = lang_dir / "manifest.json"
    dev_urls = {
        name: f"{DEV_URL_BASE}/{language}/prompts/{file_name}"
        for name, file_name in DEV_PROMPT_FILES.items()
    }
    lines = _plan_lines(language, settings, dev_urls)
    prompt_urls = {MANIFEST_PROMPT_KEYS[name]: url for name, url in dev_urls.items()}
    manifest: dict[str, Any] | None = None
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    changed = manifest is not None and _merged_prompts(manifest, prompt_urls) != manifest.get(
        "prompts", {}
    )

    if dry_run:
        return PromptPublishResult(
            language=language,
            dry_run=True,
            target=str(manifest_path),
            lines=lines,
            uploaded=[],
            skipped=[],
            manifest_changed=changed,
        )

    produced = _narrate(language, settings, narration_client)
    uploaded: list[str] = []
    skipped: list[str] = []
    for name, (_file_name, audio) in produced.items():
        destination = lang_dir / "prompts" / DEV_PROMPT_FILES[name]
        if destination.exists() and destination.read_bytes() == audio:
            skipped.append(str(destination))
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(audio)
        uploaded.append(str(destination))

    if manifest is not None and changed:
        manifest["prompts"] = _merged_prompts(manifest, prompt_urls)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return PromptPublishResult(
        language=language,
        dry_run=False,
        target=str(manifest_path),
        lines=lines,
        uploaded=uploaded,
        skipped=skipped,
        manifest_changed=changed,
    )
