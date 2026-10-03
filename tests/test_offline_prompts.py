"""Every selector language ships its same-origin offline prompt (H6, AI-481).

The offline clouds speak `/static/content/{lang}/prompts/offline.wav`, served
by the app itself, never from R2: when the bucket is unreachable the line still
has to play. A language without the file silently regresses to silent clouds.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest

from src.pipeline.models import Language

CONTENT = Path(__file__).resolve().parents[1] / "src" / "static" / "content"
WAV_MAGIC = b"RIFF"


@pytest.mark.parametrize("language", get_args(Language))
def test_every_language_ships_a_spoken_offline_prompt(language: str) -> None:
    offline = CONTENT / language / "prompts" / "offline.wav"
    assert offline.is_file(), f"{language}: missing {offline.relative_to(CONTENT)}"
    assert offline.read_bytes()[:4] == WAV_MAGIC, f"{language}: offline.wav is not a WAV file"
