"""Tests for the shared language display-name map (AI-446)."""

from src.pipeline.languages import LANGUAGE_NAMES, language_name


def test_all_seven_have_names() -> None:
    for code in ["it", "es", "en", "el", "de", "bg", "ru"]:
        assert LANGUAGE_NAMES[code]


def test_language_name_fallbacks_to_code() -> None:
    assert language_name("zz") == "zz"
