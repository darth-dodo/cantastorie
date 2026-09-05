"""Behavior specs for assembling a branching story's choice assets (AI-428).

Assemble already hashes each page's audio and image into immutable,
cache-forever names (docs/architecture.md "R2 layout"). A branching story adds
two more asset kinds per choice: each option's picture card
(`IllustrationSet.card_images`, keyed `f"{page_id}:{index}"`) and its spoken
label (`ChoiceOption.audio`). Assembly must hash both into the same story.json,
registering the underlying files in `AssembledStory.assets`, and refuse — with a
`MissingAssetError` — when either is absent.
"""

from pathlib import Path

import pytest

from src.pipeline.content_rules import ARM_PAGES, PAGE_COUNT
from src.pipeline.models import PageAudio, Story
from src.pipeline.steps.assemble import MissingAssetError, assemble_story
from src.pipeline.steps.illustrate import IllustrationSet
from src.pipeline.steps.write import BranchingStoryDraft, branching_story_from_draft

SHARED = PAGE_COUNT - ARM_PAGES
# 30 words a page: a heard path (10 pages) clears the 250-600 word range with
# room for the two choice labels, and each page clears the 30-70 floor/ceiling.
PAGE_TEXT = " ".join(["dorme."] * 30)


def _make_draft() -> BranchingStoryDraft:
    return BranchingStoryDraft(
        title="La lanterna e la barchetta",
        shared_pages=[PAGE_TEXT] * SHARED,
        option_labels=("la lanterna", "la barchetta"),
        arm_a=[PAGE_TEXT] * ARM_PAGES,
        arm_b=[PAGE_TEXT] * ARM_PAGES,
    )


def assembled_branching_fixture(tmp_path: Path) -> tuple[Story, IllustrationSet]:
    """A branching story with every asset on disk: page audio/image, each
    option's label audio, and each option's card.

    Stub bytes are written per page and per option (byte-distinct so hashes
    differ), then attached the way narrate/illustrate would leave them.
    """
    narrate_dir = tmp_path / "story" / "narrate"
    illustrate_dir = tmp_path / "story" / "illustrate"
    narrate_dir.mkdir(parents=True, exist_ok=True)
    illustrate_dir.mkdir(parents=True, exist_ok=True)

    story = branching_story_from_draft(
        _make_draft(), story_id="la-lanterna-it-abc12345", theme="the_little_boat", language="it"
    )

    page_images: dict[str, Path] = {}
    card_images: dict[str, Path] = {}
    assembled_pages = []
    for page in story.pages:
        audio_path = narrate_dir / f"{page.id}.audio.wav"
        audio_path.write_bytes(f"wav:{page.id}".encode())
        image_path = illustrate_dir / f"{page.id}.image.png"
        image_path.write_bytes(f"png:{page.id}".encode())
        page_images[page.id] = image_path

        updated: dict[str, object] = {"audio": PageAudio(file=str(audio_path), timings=[])}
        if page.choice is not None:
            new_options = []
            for index, option in enumerate(page.choice.options):
                label_audio_path = narrate_dir / f"{page.id}.opt{index}.wav"
                label_audio_path.write_bytes(f"wav:{page.id}:{index}".encode())
                card_path = illustrate_dir / f"{page.id}.opt{index}.png"
                card_path.write_bytes(f"png:{page.id}:{index}".encode())
                card_images[f"{page.id}:{index}"] = card_path
                new_options.append(
                    option.model_copy(
                        update={"audio": PageAudio(file=str(label_audio_path), timings=[])}
                    )
                )
            updated["choice"] = page.choice.model_copy(
                update={"options": (new_options[0], new_options[1])}
            )
        assembled_pages.append(page.model_copy(update=updated))

    story = story.model_copy(update={"pages": assembled_pages})

    sheet = illustrate_dir / "sheet.png"
    sheet.write_bytes(b"png:sheet")
    cover = illustrate_dir / "cover.png"
    cover.write_bytes(b"png:cover")
    illustrations = IllustrationSet(
        character_sheet=sheet,
        character_sheet_hash="sheethash",
        page_images=page_images,
        cover=cover,
        card_images=card_images,
    )
    return story, illustrations


def test_options_get_hashed_assets(tmp_path: Path) -> None:
    """Given a branching story whose choice options carry cards and label audio,
    When it is assembled,
    Then each option's card_image and audio.file are rewritten to hashed names
    registered in the assembled assets — never left as on-disk paths.
    """
    story, illustrations = assembled_branching_fixture(tmp_path)

    result = assemble_story(story, illustrations)

    choice_page = next(p for p in result.story.pages if p.choice is not None)
    assert choice_page.choice is not None
    for option in choice_page.choice.options:
        assert option.card_image is not None
        assert option.card_image in result.assets
        assert option.audio is not None
        assert option.audio.file in result.assets
        assert "." in option.card_image  # a hashed name, not a filesystem path
        assert "/" not in option.card_image


def test_missing_card_raises(tmp_path: Path) -> None:
    """Given a choice whose option card never reached disk,
    When assembly runs,
    Then it raises a MissingAssetError rather than half-assembling the story.
    """
    story, illustrations = assembled_branching_fixture(tmp_path)
    illustrations.card_images.clear()

    with pytest.raises(MissingAssetError):
        assemble_story(story, illustrations)
