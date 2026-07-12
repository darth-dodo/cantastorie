"""Behavior specs for the assemble step (AI-361).

**Content Rules** are enforced as code here, not left to prompt hope
(docs/architecture.md "Testing"); every referenced asset must resolve to real
bytes. Assembled asset names embed a content hash — immutable, cache-forever
(docs/architecture.md "R2 layout").

Word timings are empty at slice 1 (ADR-004: Voxtral returns no timestamps);
Deepgram STT reconstructs them at slice 6. Assembly passes timings through
unchanged — whatever a page carries, assembled or empty, reaches story.json.
"""

import re
from pathlib import Path

import pytest

from src.pipeline.models import Page, PageAudio, Story, WordTiming
from src.pipeline.steps.assemble import (
    AssembledStory,
    ContentRulesViolation,
    MissingAssetError,
    assemble_story,
)
from src.pipeline.steps.illustrate import IllustrationSet

# Five words a sentence, eight sentences a page, ten pages: a story that
# clears every content limit (30-70 words/page, 250-600 total, 20-word cap).
SENTENCE = "The water sings shh shh."
PAGE_TEXT = " ".join([SENTENCE] * 8)


def _timings() -> list[WordTiming]:
    return [WordTiming(word="the", start_s=0.0, end_s=0.1)]


def _narrated_story_with_illustrations(
    tmp_path: Path,
    *,
    audio_bytes: dict[str, bytes] | None = None,
    image_bytes: dict[str, bytes] | None = None,
    drop_image: str | None = None,
    drop_timings: str | None = None,
    pages_count: int = 10,
    page_text: str = PAGE_TEXT,
) -> tuple[Story, IllustrationSet]:
    """A fully narrated + illustrated story with its artifacts on disk.

    The knobs let a single spec break exactly one invariant — a missing image,
    empty timings, a short page — without rebuilding the whole fixture.
    """
    narrate_dir = tmp_path / "story" / "narrate"
    illustrate_dir = tmp_path / "story" / "illustrate"
    narrate_dir.mkdir(parents=True, exist_ok=True)
    illustrate_dir.mkdir(parents=True, exist_ok=True)

    pages: list[Page] = []
    page_images: dict[str, Path] = {}
    for n in range(1, pages_count + 1):
        pid = f"p{n}"
        audio_path = narrate_dir / f"{pid}.audio.mp3"
        audio_path.write_bytes((audio_bytes or {}).get(pid, f"mp3:{pid}".encode()))
        timings = [] if drop_timings == pid else _timings()
        pages.append(
            Page(
                id=pid,
                text=page_text,
                audio=PageAudio(file=str(audio_path), timings=timings),
                next_page=f"p{n + 1}" if n < pages_count else None,
            )
        )
        if drop_image != pid:
            image_path = illustrate_dir / f"{pid}.image.png"
            image_path.write_bytes((image_bytes or {}).get(pid, f"png:{pid}".encode()))
            page_images[pid] = image_path

    sheet = illustrate_dir / "sheet.png"
    sheet.write_bytes(b"png:sheet")
    cover = illustrate_dir / "cover.png"
    cover.write_bytes(b"png:cover")
    illustrations = IllustrationSet(
        character_sheet=sheet,
        character_sheet_hash="sheethash",
        page_images=page_images,
        cover=cover,
    )
    story = Story(
        id="the-sleepy-sea-it-abc12345",
        language="it",
        title="La barchetta",
        theme="the_sleepy_sea",
        shape="linear",
        pages=pages,
    )
    return story, illustrations


def test_assembly_rewrites_every_asset_to_its_immutable_hashed_name(tmp_path: Path) -> None:
    """Given a narrated, illustrated story that clears the content rules,
    When it is assembled,
    Then every page's audio and image are rewritten to p{n}.{hash8}.mp3 and
    p{n}.{hash8}.webp — the immutable, cache-forever naming — and each name
    maps to the source bytes on disk.
    """
    story, illustrations = _narrated_story_with_illustrations(tmp_path)

    assembled = assemble_story(story, illustrations)

    assert isinstance(assembled, AssembledStory)
    for page in assembled.story.pages:
        assert page.audio is not None
        assert re.fullmatch(rf"{page.id}\.[0-9a-f]{{8}}\.mp3", page.audio.file)
        assert page.image is not None
        assert re.fullmatch(rf"{page.id}\.[0-9a-f]{{8}}\.webp", page.image)
        assert page.audio.file in assembled.assets
        assert page.image in assembled.assets
    # Two assets per page — audio and image — all present on disk.
    assert len(assembled.assets) == 20
    assert all(source.exists() for source in assembled.assets.values())


def test_the_hash_is_the_asset_content_so_identical_bytes_share_it(tmp_path: Path) -> None:
    """Given two pages whose audio bytes are byte-for-byte identical,
    When the story is assembled,
    Then both audio names carry the same hash segment — the name is a function
    of content, not of the page — while the page prefixes still differ.
    """
    same = b"mp3:identical"
    story, illustrations = _narrated_story_with_illustrations(
        tmp_path, audio_bytes={"p1": same, "p2": same}
    )

    assembled = assemble_story(story, illustrations)
    by_id = {page.id: page for page in assembled.story.pages}

    p1_hash = by_id["p1"].audio.file.split(".")[1]  # type: ignore[union-attr]
    p2_hash = by_id["p2"].audio.file.split(".")[1]  # type: ignore[union-attr]
    assert p1_hash == p2_hash
    assert by_id["p1"].audio.file != by_id["p2"].audio.file  # type: ignore[union-attr]


def test_word_timings_survive_assembly_untouched(tmp_path: Path) -> None:
    """Given pages narrated with word timings,
    When the story is assembled,
    Then every page keeps its timings verbatim — assembly never modifies timing
    data (ADR-004: timings are empty at slice 1, reconstructed at slice 6).
    """
    story, illustrations = _narrated_story_with_illustrations(tmp_path)

    assembled = assemble_story(story, illustrations)

    for page in assembled.story.pages:
        assert page.audio is not None
        assert page.audio.timings == _timings()


def test_empty_timings_survive_assembly_untouched(tmp_path: Path) -> None:
    """Given pages narrated without word timings (the Voxtral/ADR-004 default),
    When the story is assembled,
    Then every page keeps its empty timings — assembly does not require or
    synthesize timings; they arrive empty from narrate and stay that way.
    """
    story, illustrations = _narrated_story_with_illustrations(tmp_path, drop_timings="p1")

    assembled = assemble_story(story, illustrations)

    page = next(p for p in assembled.story.pages if p.id == "p1")
    assert page.audio is not None
    assert page.audio.timings == []


def test_a_content_rule_violation_fails_hard_with_the_typed_violation(tmp_path: Path) -> None:
    """Given a story with only seven pages (product.md "Content Rules": exactly
    ten),
    When assembly runs,
    Then it raises before touching any asset, and the error carries the typed
    page_count ContentViolation — precise enough to have driven a revise.
    """
    story, illustrations = _narrated_story_with_illustrations(tmp_path, pages_count=7)

    with pytest.raises(ContentRulesViolation) as excinfo:
        assemble_story(story, illustrations)

    rules = {violation.rule for violation in excinfo.value.violations}
    assert "page_count" in rules


def test_a_short_page_fails_hard_naming_the_page(tmp_path: Path) -> None:
    """Given one page far under the 30-word floor,
    When assembly runs,
    Then it raises a page_words violation naming that page.
    """
    story, illustrations = _narrated_story_with_illustrations(tmp_path)
    story.pages[3].text = "too short"  # 2 words, well under the floor

    with pytest.raises(ContentRulesViolation) as excinfo:
        assemble_story(story, illustrations)

    page_words = [v for v in excinfo.value.violations if v.rule == "page_words"]
    assert page_words and page_words[0].page_id == "p4"


def test_a_missing_image_fails_hard_naming_the_page_and_asset(tmp_path: Path) -> None:
    """Given a page whose illustration never made it to disk,
    When assembly runs,
    Then it raises a MissingAssetError naming the page and that it is the image
    — nothing half-assembled reaches staging.
    """
    story, illustrations = _narrated_story_with_illustrations(tmp_path, drop_image="p5")

    with pytest.raises(MissingAssetError) as excinfo:
        assemble_story(story, illustrations)

    assert excinfo.value.page_id == "p5"
    assert excinfo.value.kind == "image"


def test_a_missing_audio_file_fails_hard(tmp_path: Path) -> None:
    """Given a page whose narration mp3 was deleted from the cache,
    When assembly runs,
    Then it raises a MissingAssetError for that page's audio.
    """
    story, illustrations = _narrated_story_with_illustrations(tmp_path)
    Path(story.pages[0].audio.file).unlink()  # type: ignore[union-attr]

    with pytest.raises(MissingAssetError) as excinfo:
        assemble_story(story, illustrations)

    assert excinfo.value.page_id == "p1"
    assert excinfo.value.kind == "audio"


def test_assembled_story_json_matches_the_player_fixture_shape(tmp_path: Path) -> None:
    """Given the dev fixture the player already plays,
    When a freshly assembled story is dumped to JSON,
    Then its top-level keys and per-page shape match the fixture exactly — the
    player plays a published story unchanged.
    """
    fixture = Path("src/static/content/it/stories/la-barchetta-e-la-luna/story.json")
    fixture_story = Story.model_validate_json(fixture.read_bytes())

    story, illustrations = _narrated_story_with_illustrations(tmp_path)
    assembled = assemble_story(story, illustrations)

    dumped = assembled.story.model_dump()
    expected = fixture_story.model_dump()
    assert dumped.keys() == expected.keys()
    assert dumped["pages"][0].keys() == expected["pages"][0].keys()
    assert dumped["pages"][0]["audio"].keys() == expected["pages"][0]["audio"].keys()
