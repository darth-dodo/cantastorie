"""Behavior specs for the narration step (AI-391).

One narrator voice per story, audio synthesized through Voxtral via OpenRouter
(ADR-004). Voxtral returns raw audio with no timestamps — word timings stay
empty until slice 6 reconstructs them via Deepgram STT. Spoken prompts are
first-class assets (docs/product.md **Spoken Prompts**). Every OpenRouter
interaction is served by httpx.MockTransport — zero network.
"""

import re
from pathlib import Path

import httpx
from pydantic import SecretStr

from src.config import Settings
from src.pipeline.cache import ArtifactCache
from src.pipeline.models import Page
from src.pipeline.providers import NarrationClient
from src.pipeline.steps.narrate import (
    IT_UTTERANCES,
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
            200, content=f"mp3:{text}".encode(), headers={"Content-Type": "audio/mpeg"}
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
    Then every page comes back with an mp3 on disk and empty word timings —
    Voxtral returns no timestamps; Deepgram STT reconstructs them at slice 6.
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
        assert page.audio.file.endswith(".mp3")
        assert Path(page.audio.file).read_bytes() == f"mp3:{page.text}".encode()
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

    assert calls == ["Il mare dorme.", "La luna guarda.", "La luna sorride."]


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


# ---------------------------------------------------------------------------
# Utterances: spoken prompts as first-class assets
# ---------------------------------------------------------------------------


def test_slice_one_ships_the_three_italian_prompts_as_final_copy() -> None:
    """Given the locked copy in docs/product.md **Spoken Prompts**,
    When the slice-1 utterance set is read,
    Then it contains exactly the shelf greeting, story start, and end prompt
    with their final Italian strings.
    """
    assert IT_UTTERANCES == {
        "shelf_greeting": "Ciao! Quale storia ascoltiamo oggi?",
        "story_start": "Si parte!",
        "end_prompt": "Fine! Ancora, o un'altra storia?",
    }


def test_utterance_audio_lands_under_prompts_it_with_hashed_filenames(tmp_path: Path) -> None:
    """Given the Italian prompt set,
    When utterances are synthesized into a local output folder,
    Then each lands at prompts/it/{name}.{contenthash}.mp3 — the immutable,
    cache-forever naming of docs/architecture.md **R2 layout**.
    """
    calls: list[str] = []
    settings = _settings()
    out_dir = tmp_path / "out"

    produced = synthesize_utterances(
        settings,
        ArtifactCache(tmp_path / "prompts-cache"),
        out_dir,
        client=_client(settings, calls),
    )

    assert set(produced) == {"shelf_greeting", "story_start", "end_prompt"}
    for name, path in produced.items():
        assert path.parent == out_dir / "prompts" / "it"
        assert re.fullmatch(rf"{name}\.[0-9a-f]{{16}}\.mp3", path.name)
        assert path.read_bytes() == f"mp3:{IT_UTTERANCES[name]}".encode()  # type: ignore[index]


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

    first = synthesize_utterances(settings, cache, tmp_path / "out", client=client)
    second = synthesize_utterances(settings, cache, tmp_path / "out", client=client)

    assert len(calls) == len(IT_UTTERANCES)  # one call per prompt, total
    assert first == second
