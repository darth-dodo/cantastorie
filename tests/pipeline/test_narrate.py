"""Behavior specs for the narration step (AI-359).

One narrator voice per story, character timestamps captured with every TTS
call (docs/architecture.md "Timestamps and utterances from day one"), and
spoken prompts as first-class assets (docs/product.md **Spoken Prompts**).
Every ElevenLabs interaction is served by httpx.MockTransport — zero network.
"""

import base64
import json
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
    word_timings_from_alignment,
)


def _settings(voice_id: str = "voice-1") -> Settings:
    return Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-test"),
        elevenlabs_api_key=SecretStr("el-test"),
        elevenlabs_voice_id=voice_id,
    )


def _alignment(text: str) -> dict[str, object]:
    """Character alignment shaped like ElevenLabs': one entry per character,
    0.1s apiece, so expected word timings are easy to compute by eye."""
    chars = list(text)
    return {
        "characters": chars,
        "character_start_times_seconds": [round(i * 0.1, 1) for i in range(len(chars))],
        "character_end_times_seconds": [round((i + 1) * 0.1, 1) for i in range(len(chars))],
    }


def _fake_elevenlabs(calls: list[str]) -> httpx.MockTransport:
    """A mock ElevenLabs that echoes deterministic audio and a real-shaped
    character alignment for whatever text arrives, recording every call."""

    def handler(request: httpx.Request) -> httpx.Response:
        text = json.loads(request.content)["text"]
        calls.append(text)
        return httpx.Response(
            200,
            json={
                "audio_base64": base64.b64encode(f"mp3:{text}".encode()).decode(),
                "alignment": _alignment(text),
            },
        )

    return httpx.MockTransport(handler)


def _client(settings: Settings, calls: list[str]) -> NarrationClient:
    return NarrationClient(settings, transport=_fake_elevenlabs(calls))


# ---------------------------------------------------------------------------
# Character alignment → word timings
# ---------------------------------------------------------------------------


def test_character_alignment_collapses_into_one_timing_per_word() -> None:
    """Given a character alignment for a two-word phrase,
    When it is converted to word timings,
    Then each word spans from its first character's start to its last
    character's end, and the separating space belongs to no word.
    """
    timings = word_timings_from_alignment(_alignment("Il mare"))
    assert [t.word for t in timings] == ["Il", "mare"]
    assert (timings[0].start_s, timings[0].end_s) == (0.0, 0.2)
    assert (timings[1].start_s, timings[1].end_s) == (0.3, 0.7)


def test_runs_of_whitespace_never_produce_empty_words() -> None:
    """Given text with doubled spaces and a newline between words,
    When converted,
    Then only real words come out — no empty or whitespace 'words'.
    """
    timings = word_timings_from_alignment(_alignment("Il  mare\ndorme"))
    assert [t.word for t in timings] == ["Il", "mare", "dorme"]
    assert all(t.word.strip() == t.word and t.word for t in timings)


def test_leading_and_trailing_punctuation_stays_outside_the_word() -> None:
    """Given a word wrapped in quotes and exclamation («Ciao!»),
    When converted,
    Then the word text and its timing cover only the letters, so a karaoke
    highlight never waits on trailing punctuation silence.
    """
    timings = word_timings_from_alignment(_alignment("«Ciao!» disse."))
    assert [t.word for t in timings] == ["Ciao", "disse"]
    ciao = timings[0]
    assert (ciao.start_s, ciao.end_s) == (0.1, 0.5)  # C..o, not «..»


def test_internal_apostrophes_keep_elided_italian_words_whole() -> None:
    """Given the Italian elision "l'acqua",
    When converted,
    Then it stays a single word with its apostrophe — never split in two.
    """
    timings = word_timings_from_alignment(_alignment("l'acqua dice shh"))
    assert [t.word for t in timings] == ["l'acqua", "dice", "shh"]
    assert (timings[0].start_s, timings[0].end_s) == (0.0, 0.7)


def test_punctuation_only_tokens_produce_no_word() -> None:
    """Given a dash standing alone between words,
    When converted,
    Then it yields no word timing of its own.
    """
    timings = word_timings_from_alignment(_alignment("Ciao — mare"))
    assert [t.word for t in timings] == ["Ciao", "mare"]


def test_an_empty_alignment_yields_no_timings() -> None:
    """Given an alignment with no characters (or only whitespace),
    When converted,
    Then the result is simply an empty list — no crash, no ghost words.
    """
    assert word_timings_from_alignment(_alignment("")) == []
    assert word_timings_from_alignment(_alignment("   ")) == []


# ---------------------------------------------------------------------------
# Per-page narration: one voice, timings stored, cache honoured
# ---------------------------------------------------------------------------


def test_every_narrated_page_carries_word_timings(tmp_path: Path) -> None:
    """Given a story's pages and the single narrator voice,
    When the narrate step runs,
    Then every page comes back with an mp3 on disk and word timings derived
    from the character alignment captured on that same call.
    """
    calls: list[str] = []
    settings = _settings()
    pages = [Page(id="p1", text="Il mare dorme."), Page(id="p2", text="L'onda dice shh.")]

    narrated = narrate_pages(
        pages, settings, ArtifactCache(tmp_path / "story-1"), _client(settings, calls)
    )

    assert len(narrated) == len(pages)
    expected_words = [["Il", "mare", "dorme"], ["L'onda", "dice", "shh"]]
    for page, words in zip(narrated, expected_words, strict=True):
        assert page.audio is not None
        assert page.audio.file.endswith(".mp3")
        assert Path(page.audio.file).read_bytes() == f"mp3:{page.text}".encode()
        assert [t.word for t in page.audio.timings] == words


def test_character_timestamps_are_stored_beside_the_audio(tmp_path: Path) -> None:
    """Given a narrated page,
    When the step's artifacts are inspected,
    Then the raw character alignment sits in the cache next to the mp3 —
    regenerating timestamps later would double the narration cost.
    """
    calls: list[str] = []
    settings = _settings()
    cache = ArtifactCache(tmp_path / "story-1")

    narrated = narrate_pages(
        [Page(id="p1", text="Buonanotte mare")], settings, cache, _client(settings, calls)
    )

    audio_path = Path(narrated[0].audio.file)  # type: ignore[union-attr]
    alignment_path = audio_path.with_suffix(".json")
    stored = json.loads(alignment_path.read_bytes())
    assert stored["characters"] == list("Buonanotte mare")
    assert "character_start_times_seconds" in stored
    assert "character_end_times_seconds" in stored


def test_unchanged_page_text_makes_zero_tts_calls(tmp_path: Path) -> None:
    """Given a story already narrated into the cache,
    When the narrate step re-runs with unchanged page text,
    Then no TTS call is made and the same timings come back from disk
    (docs/architecture.md **Content-addressed caching**).
    """
    calls: list[str] = []
    settings = _settings()
    cache = ArtifactCache(tmp_path / "story-1")
    pages = [Page(id="p1", text="Il mare dorme.")]
    client = _client(settings, calls)

    first = narrate_pages(pages, settings, cache, client)
    second = narrate_pages(pages, settings, cache, client)

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

    narrate_pages(pages, settings, cache, client)
    edited = [pages[0], pages[1].model_copy(update={"text": "La luna sorride."})]
    narrate_pages(edited, settings, cache, client)

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
        settings = _settings(voice_id=voice)
        narrate_pages(pages, settings, cache, _client(settings, calls))

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
