"""Tests for the shared language display-name map (AI-446)."""

from typing import get_args

from src.pipeline.languages import LANGUAGE_NAMES, language_name
from src.pipeline.models import Language


def test_every_language_code_has_a_name() -> None:
    # Iterate the Language literal so this never goes stale when a code is added
    # (e.g. mr/Marathi came in via origin/main).
    for code in get_args(Language):
        assert LANGUAGE_NAMES[code], f"missing display name for {code}"


def test_language_name_fallbacks_to_code() -> None:
    assert language_name("zz") == "zz"
