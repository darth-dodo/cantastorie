"""Generate the dev story fixture for the playback loop (AI-364).

Produces "La barchetta e la luna" as a story.json matching EXACTLY the
schema AI-357 pins (docs/plans/2026-07-05-pipeline-skeleton.md, Task 2),
with a tiny generated WAV per page (soft sine tones, seconds long), a 1x1
WebP per page, and the story-start / end prompt chimes. Everything is
committed under src/static/content/it/; rerun this script to regenerate.

Filenames embed a short content hash, mirroring the immutable
`p1.{hash}.mp3` naming from docs/architecture.md -> Content Storage.
"""

import base64
import hashlib
import io
import json
import math
import struct
import wave
from pathlib import Path

RATE = 8000  # dev fixture: tiny files matter more than fidelity
CONTENT = Path(__file__).resolve().parent.parent / "src" / "static" / "content" / "it"
STORY_DIR = CONTENT / "stories" / "la-barchetta-e-la-luna"
PROMPTS_DIR = CONTENT / "prompts"

# Page text follows the docs/product.md worked example outline.
PAGES = [
    ("p1", "la barchetta Nina dondola nel porto della sera"),
    ("p2", "l'acqua fa shh, shh"),
    ("p3", "un gabbiano assonnato si posa sulla prua"),
    ("p4", "il faro fa buonanotte: uno, due"),
    ("p5", "Nina vuole un'ultima piccola onda"),
    ("p6", "la luna posa un sentiero d'argento sul mare"),
    ("p7", "Nina lo percorre, piano piano, fino al suo posto"),
    ("p8", "il gabbiano nasconde la testa; l'acqua dice shh"),
]

# A slow pentatonic walk down toward sleep, one note per page.
PAGE_FREQS = [523.25, 493.88, 440.0, 392.0, 349.23, 329.63, 293.66, 261.63]
PAGE_SECONDS = 1.2
LAST_PAGE_SECONDS = 1.6

# Smallest well-known valid WebP: 1x1, lossy. The player washes stay
# visible behind it; real watercolor boards arrive with the pipeline.
WEBP_1PX = base64.b64decode("UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEADsD+JaQAA3AAAAAA")


def tone(freq: float, seconds: float, volume: float = 0.3) -> list[float]:
    samples = []
    for i in range(int(RATE * seconds)):
        t = i / RATE
        # Gentle attack and long release - nothing snaps at bedtime.
        envelope = min(t / 0.08, 1.0) * math.exp(-2.2 * t / seconds)
        samples.append(volume * envelope * math.sin(2 * math.pi * freq * t))
    return samples


def wav_bytes(samples: list[float]) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(
            b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples)
        )
    return buf.getvalue()


def hashed_name(stem: str, data: bytes, suffix: str) -> str:
    return f"{stem}.{hashlib.sha256(data).hexdigest()[:8]}{suffix}"


def word_timings(text: str, seconds: float) -> list[dict[str, object]]:
    words = text.split()
    step = seconds / len(words)
    return [
        {"word": word, "start_s": round(i * step, 3), "end_s": round((i + 1) * step, 3)}
        for i, word in enumerate(words)
    ]


def chime(notes: list[tuple[float, float]]) -> bytes:
    mixed: list[float] = []
    for freq, seconds in notes:
        mixed.extend(tone(freq, seconds))
    return wav_bytes(mixed)


def main() -> None:
    STORY_DIR.mkdir(parents=True, exist_ok=True)
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)

    pages = []
    for index, (page_id, text) in enumerate(PAGES):
        seconds = LAST_PAGE_SECONDS if index == len(PAGES) - 1 else PAGE_SECONDS
        audio = wav_bytes(tone(PAGE_FREQS[index], seconds))
        audio_name = hashed_name(page_id, audio, ".wav")
        (STORY_DIR / audio_name).write_bytes(audio)

        image_name = hashed_name(page_id, WEBP_1PX, ".webp")
        (STORY_DIR / image_name).write_bytes(WEBP_1PX)

        next_id = PAGES[index + 1][0] if index < len(PAGES) - 1 else None
        pages.append(
            {
                "id": page_id,
                "text": text,
                "audio": {"file": audio_name, "timings": word_timings(text, seconds)},
                "image": image_name,
                "next_page": next_id,
                "choice": None,
            }
        )

    story = {
        "schema_version": 1,
        "id": "la-barchetta-e-la-luna",
        "language": "it",
        "title": "La barchetta e la luna",
        "theme": "the_sleepy_sea",
        "shape": "linear",
        "pages": pages,
        "gloss": {"barchetta": "little boat", "luna": "moon", "piano": "gently"},
    }
    (STORY_DIR / "story.json").write_text(json.dumps(story, ensure_ascii=False, indent=2) + "\n")

    # Story start ("Si parte!"): a quick upward flourish.
    (PROMPTS_DIR / "story-start.wav").write_bytes(chime([(659.25, 0.25), (880.0, 0.35)]))
    # End prompt ("Fine! Ancora, o un'altra storia?"): a settling third.
    (PROMPTS_DIR / "end.wav").write_bytes(chime([(587.33, 0.3), (493.88, 0.45)]))
    # Slice-2 failure prompts (AI-367): a sleepy falling third for the
    # napping story, a soft low pair for the clouds. Chime stand-ins until
    # the pipeline produces real utterance audio.
    (PROMPTS_DIR / "audio-retry.wav").write_bytes(chime([(659.25, 0.3), (523.25, 0.5)]))
    (PROMPTS_DIR / "offline.wav").write_bytes(chime([(440.0, 0.35), (392.0, 0.5)]))


if __name__ == "__main__":
    main()
