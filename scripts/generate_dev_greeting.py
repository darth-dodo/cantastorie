"""Generate the dev greeting chime fixture.

A soft two-note chime (~0.9 s) standing in for the recorded shelf greeting
until the pipeline produces real utterance audio. Committed output lives at
static/content/it/prompts/greeting.wav; rerun this script to regenerate.
"""

import math
import struct
import wave
from pathlib import Path

RATE = 22050
OUT = Path(__file__).resolve().parent.parent / "static" / "content" / "it" / "prompts"


def tone(freq: float, seconds: float, volume: float) -> list[float]:
    samples = []
    total = int(RATE * seconds)
    for i in range(total):
        t = i / RATE
        # Gentle attack and long release — nothing snaps at bedtime.
        envelope = min(t / 0.05, 1.0) * math.exp(-3.0 * t / seconds)
        samples.append(volume * envelope * math.sin(2 * math.pi * freq * t))
    return samples


def main() -> None:
    # E5 then A5: a small upward "hello".
    first, second = tone(659.25, 0.45, 0.35), tone(880.0, 0.55, 0.3)
    overlap = int(RATE * 0.12)
    mixed = first[:-overlap]
    for i in range(overlap):
        mixed.append(first[len(first) - overlap + i] + second[i])
    mixed.extend(second[overlap:])

    OUT.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / "greeting.wav"), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        frames = b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in mixed)
        out.writeframes(frames)


if __name__ == "__main__":
    main()
