"""Tests for Story.cover field (AI-443)."""

import pytest

from src.pipeline.models import Page, Story


def _page(page_id: str, next_page: str | None = None) -> Page:
    return Page(id=page_id, text="La barchetta dondola.", next_page=next_page)


@pytest.fixture
def minimal_story_kwargs() -> dict:
    return {
        "id": "la-barchetta-e-la-luna",
        "language": "it",
        "title": "La barchetta e la luna",
        "theme": "the_sleepy_sea",
        "shape": "linear",
        "pages": [_page("p1", "p2"), _page("p2")],
    }


def test_story_cover_defaults_none(minimal_story_kwargs):
    s = Story(**minimal_story_kwargs)
    assert s.cover is None


def test_story_cover_roundtrips(minimal_story_kwargs):
    s = Story(**{**minimal_story_kwargs, "cover": "cover.png"})
    assert s.model_dump()["cover"] == "cover.png"
