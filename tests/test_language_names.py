"""Tests for the shared language display-name map (AI-446)."""

import re
from pathlib import Path
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


MAIN_JS = Path(__file__).resolve().parent.parent / "src" / "static" / "js" / "main.js"


def test_every_selector_language_is_a_pipeline_language() -> None:
    """The player's language selector (main.js LANGS) and the pipeline's
    Language roster must list the same codes — a selector entry the pipeline
    can't author or voice would be a silent, empty shelf (H6, AI-481)."""
    source = MAIN_JS.read_text(encoding="utf-8")
    block = re.search(r"export const LANGS = \[(.*?)\];", source, re.DOTALL)
    assert block is not None, "LANGS array not found in main.js"
    codes = re.findall(r'code:\s*"([a-z]{2})"', block.group(1))
    assert codes, "no language codes parsed from LANGS"
    assert set(codes) <= set(get_args(Language)), set(codes) - set(get_args(Language))
    assert set(codes) == set(get_args(Language)), "a Language is missing from the selector"
