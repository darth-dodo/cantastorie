"""Behavior specs for the narration step (AI-391).

One narrator voice per story, audio synthesized through Gemini TTS via OpenRouter
(ADR-008). Gemini returns raw audio with no timestamps — word timings stay
empty until slice 6 reconstructs them via Deepgram STT. Spoken prompts are
first-class assets (docs/product.md **Spoken Prompts**). Every OpenRouter
interaction is served by httpx.MockTransport — zero network.
"""

import json
import re
import threading
from collections import Counter
from pathlib import Path
from typing import get_args

import httpx
from pydantic import SecretStr

from src.config import Settings
from src.pipeline.cache import ArtifactCache
from src.pipeline.models import Language, Page
from src.pipeline.providers import NarrationClient
from src.pipeline.steps.narrate import (
    IT_UTTERANCES,
    UTTERANCE_TEXTS,
    UtteranceName,
    narrate_pages,
    synthesize_utterances,
)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-test"),
    )


def _fake_openrouter(calls: list[str]) -> httpx.MockTransport:
    """A mock OpenRouter /audio/speech that echoes deterministic audio for
    whatever text arrives, recording every call."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        text = body["input"]
        calls.append(text)
        return httpx.Response(
            200,
            content=f"pcm:{text}".encode(),
            headers={"Content-Type": "audio/pcm;rate=24000;channels=1"},
        )

    return httpx.MockTransport(handler)


def _client(settings: Settings, calls: list[str]) -> NarrationClient:
    return NarrationClient(settings, transport=_fake_openrouter(calls))


# ---------------------------------------------------------------------------
# Per-page narration: one voice, audio stored, cache honoured, no timings
# ---------------------------------------------------------------------------


def test_every_narrated_page_carries_audio_with_empty_timings(tmp_path: Path) -> None:
    """Given a story's pages and the single narrator voice,
    When the narrate step runs,
    Then every page comes back with a wav on disk and empty word timings —
    Gemini returns no timestamps; Deepgram STT reconstructs them at slice 6.
    """
    calls: list[str] = []
    settings = _settings()
    pages = [Page(id="p1", text="Il mare dorme."), Page(id="p2", text="L'onda dice shh.")]

    narrated = narrate_pages(
        pages, "it", settings, ArtifactCache(tmp_path / "story-1"), _client(settings, calls)
    )

    assert len(narrated) == len(pages)
    for page in narrated:
        assert page.audio is not None
        assert page.audio.file.endswith(".wav")
        assert Path(page.audio.file).read_bytes()[:4] == b"RIFF"
        assert page.audio.timings == []


def test_unchanged_page_text_makes_zero_tts_calls(tmp_path: Path) -> None:
    """Given a story already narrated into the cache,
    When the narrate step re-runs with unchanged page text,
    Then no TTS call is made and the same audio comes back from disk
    (docs/architecture.md **Content-addressed caching**).
    """
    calls: list[str] = []
    settings = _settings()
    cache = ArtifactCache(tmp_path / "story-1")
    pages = [Page(id="p1", text="Il mare dorme.")]
    client = _client(settings, calls)

    first = narrate_pages(pages, "it", settings, cache, client)
    second = narrate_pages(pages, "it", settings, cache, client)

    assert calls == ["Il mare dorme."]  # exactly one call across both runs
    assert first[0].audio == second[0].audio


def test_editing_one_page_resynthesizes_only_that_page(tmp_path: Path) -> None:
    """Given a two-page story already narrated,
    When one page's text changes and the step re-runs,
    Then only the edited page costs a TTS call — the other is a pure lookup.
    """
    calls: list[str] = []
    settings = _settings()
    cache = ArtifactCache(tmp_path / "story-1")
    client = _client(settings, calls)
    pages = [Page(id="p1", text="Il mare dorme."), Page(id="p2", text="La luna guarda.")]

    narrate_pages(pages, "it", settings, cache, client)
    edited = [pages[0], pages[1].model_copy(update={"text": "La luna sorride."})]
    narrate_pages(edited, "it", settings, cache, client)

    # Order-independent: pages narrate concurrently, so call order is not
    # deterministic — the contract is which/how-many calls, not their sequence.
    assert Counter(calls) == Counter(["Il mare dorme.", "La luna guarda.", "La luna sorride."])


def test_a_different_voice_or_model_never_reuses_cached_audio(tmp_path: Path) -> None:
    """Given audio cached for one narrator voice,
    When the step runs with a different voice id,
    Then the cache misses — the key is page text + voice ID + model/settings.
    """
    calls: list[str] = []
    cache = ArtifactCache(tmp_path / "story-1")
    pages = [Page(id="p1", text="Il mare dorme.")]

    for voice in ("voice-1", "voice-2"):
        settings = Settings(
            _env_file=None,
            openrouter_api_key=SecretStr("sk-or-test"),
            narration_voices={"it": voice},
        )
        narrate_pages(pages, "it", settings, cache, _client(settings, calls))

    assert len(calls) == 2


def test_pages_are_narrated_concurrently(tmp_path: Path) -> None:
    """Given a multi-page story,
    When the narrate step runs,
    Then pages are synthesized in parallel — proven by a barrier that only trips
    if every page's TTS call is in flight at once. Serial narration would block
    the first call until the barrier times out.
    """
    settings = _settings()  # pipeline_media_concurrency defaults to 4
    pages = [Page(id=f"p{i}", text=f"pagina numero {i}") for i in range(4)]
    barrier = threading.Barrier(len(pages), timeout=5)

    def handler(request: httpx.Request) -> httpx.Response:
        text = json.loads(request.content)["input"]
        barrier.wait()  # BrokenBarrierError (→ test failure) if calls are serial
        return httpx.Response(
            200,
            content=f"pcm:{text}".encode(),
            headers={"Content-Type": "audio/pcm;rate=24000;channels=1"},
        )

    client = NarrationClient(settings, transport=httpx.MockTransport(handler))
    narrated = narrate_pages(pages, "it", settings, ArtifactCache(tmp_path / "story-1"), client)

    assert [p.id for p in narrated] == ["p0", "p1", "p2", "p3"]  # order preserved


# ---------------------------------------------------------------------------
# Utterances: spoken prompts as first-class assets
# ---------------------------------------------------------------------------


def test_the_utterance_set_ships_final_italian_copy() -> None:
    """Given docs/product.md **Spoken Prompts**,
    When the utterance set is read,
    Then it carries the slice-1 prompts plus the slice-2 failure prompts,
    all as final copy, verbatim.
    """
    assert IT_UTTERANCES == {
        "shelf_greeting": "Ciao! Quale storia ascoltiamo oggi?",
        "story_start": "Si parte!",
        "end_prompt": "Fine! Ancora, o un'altra storia?",
        "audio_retry": "Oh! La storia fa un pisolino. Tocca l'uccellino per svegliarla.",
        "offline": "Le nuvole hanno preso le storie. Riprova tra poco!",
    }


def test_utterance_audio_lands_under_prompts_it_with_hashed_filenames(tmp_path: Path) -> None:
    """Given the Italian prompt set,
    When utterances are synthesized into a local output folder,
    Then each lands at prompts/it/{name}.{contenthash}.wav — the immutable,
    cache-forever naming of docs/architecture.md **R2 layout**.
    """
    calls: list[str] = []
    settings = _settings()
    out_dir = tmp_path / "out"

    produced = synthesize_utterances(
        settings,
        ArtifactCache(tmp_path / "prompts-cache"),
        out_dir,
        "it",
        client=_client(settings, calls),
    )

    assert set(produced) == set(IT_UTTERANCES)
    for name, path in produced.items():
        assert path.parent == out_dir / "prompts" / "it"
        assert re.fullmatch(rf"{name}\.[0-9a-f]{{16}}\.wav", path.name)
        assert path.read_bytes()[:4] == b"RIFF"  # type: ignore[index]


def test_rerunning_utterances_makes_zero_tts_calls_and_identical_filenames(tmp_path: Path) -> None:
    """Given prompts already synthesized into the cache,
    When the utterance step re-runs unchanged,
    Then no TTS call is made and every content-hashed filename is identical —
    published prompt assets stay immutable.
    """
    calls: list[str] = []
    settings = _settings()
    cache = ArtifactCache(tmp_path / "prompts-cache")
    client = _client(settings, calls)

    first = synthesize_utterances(settings, cache, tmp_path / "out", "it", client=client)
    second = synthesize_utterances(settings, cache, tmp_path / "out", "it", client=client)

    assert len(calls) == len(IT_UTTERANCES)  # one call per prompt, total
    assert first == second


def test_every_language_in_the_roster_has_every_spoken_prompt() -> None:
    """Given the locked language roster (models.Language) and the prompt names,
    When the per-language prompt texts are read,
    Then every language carries a non-empty line for every prompt — a language
    added to the roster without its prompts fails CI here (H6, AI-481).
    """
    assert set(UTTERANCE_TEXTS) == set(get_args(Language))
    names = set(get_args(UtteranceName))
    for language, texts in UTTERANCE_TEXTS.items():
        assert set(texts) == names, f"{language} is missing {names - set(texts)}"
        for name, text in texts.items():
            assert text.strip(), f"{language}.{name} is empty"


def test_the_italian_lines_are_the_italian_prompt_texts() -> None:
    assert UTTERANCE_TEXTS["it"] == IT_UTTERANCES


def test_each_language_speaks_its_own_prompts(tmp_path: Path) -> None:
    """Given a language other than Italian,
    When its utterances are synthesized,
    Then the TTS hears that language's lines (never the Italian ones) and the
    audio lands under prompts/{lang}/ — the AI-436 latent bug.
    """
    calls: list[str] = []
    settings = _settings()

    produced = synthesize_utterances(
        settings,
        ArtifactCache(tmp_path / "prompts-cache"),
        tmp_path / "out",
        "es",
        client=_client(settings, calls),
    )

    assert sorted(calls) == sorted(UTTERANCE_TEXTS["es"].values())
    assert not set(calls) & set(IT_UTTERANCES.values())
    assert {path.parent for path in produced.values()} == {tmp_path / "out" / "prompts" / "es"}
