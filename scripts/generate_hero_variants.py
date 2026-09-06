"""Generate hero-image variants through the project's own image pipeline.

Reuses the locked ``STYLE_PROMPT`` and ``ImageClient`` from the illustrate
step so every variant stays in the same watercolor-bedtime family as the
original ``docs/assets/cantastorie-hero.png`` — only the Italian cantastorie
*scene* changes, never the style.
"""

from pathlib import Path

from src.config import get_settings
from src.pipeline.steps.illustrate import STYLE_PROMPT, ImageClient

# STYLE_PROMPT hard-codes "Portrait orientation" for in-app story pages (a
# phone held vertically). A hero banner is wide, so swap that one sentence for
# a landscape instruction while keeping the rest of the locked style verbatim.
_PORTRAIT_LINE = "Portrait orientation — the image is tall, viewed on a phone held in hand. "
_LANDSCAPE_LINE = "Landscape orientation — the image is wide, a horizontal banner scene. "
HERO_STYLE_PROMPT = STYLE_PROMPT.replace(_PORTRAIT_LINE, _LANDSCAPE_LINE)
assert HERO_STYLE_PROMPT != STYLE_PROMPT, "portrait line not found — STYLE_PROMPT changed"

# Each entry is (slug, scene). The Italian cantastorie tradition — a
# storyteller with painted boards, a listening child — is held constant; the
# setting varies to give five distinct-but-cohesive heroes.
VARIANTS: list[tuple[str, str]] = [
    (
        "lakeside-como",
        "an Italian cantastorie storyteller on a stone jetty at the edge of a "
        "calm mountain lake like Lake Como at dusk, pastel villas and forested "
        "hills mirrored in the still water. He gestures at painted watercolor "
        "boards telling a bedtime story while a sleepy child sits wrapped in a "
        "shawl on the wooden dock beside him.",
    ),
    (
        "vineyard-harvest",
        "an Italian cantastorie storyteller among rows of ripe vines during a "
        "warm evening grape harvest, wicker baskets of grapes nearby and a stone "
        "villa glowing on the hill. He points at painted watercolor boards of a "
        "bedtime story to a drowsy child perched on an upturned crate, the sky "
        "turning soft peach and lavender.",
    ),
    (
        "cloister-courtyard",
        "an Italian cantastorie storyteller in a peaceful stone cloister "
        "courtyard at twilight, gentle arches and a central well, potted lemon "
        "trees around the edges. Warm lanterns glow. He shows painted watercolor "
        "boards of a bedtime story to children seated on a worn rug, one already "
        "nodding off against a cushion.",
    ),
    (
        "market-square-evening",
        "an Italian cantastorie storyteller in a cobbled market square at "
        "evening as the last stalls fold away, striped awnings and flower carts "
        "around. Strings of warm bulbs criss-cross overhead. He gestures at "
        "painted watercolor boards telling a bedtime story to a small circle of "
        "children, a sleepy toddler on a parent's lap.",
    ),
    (
        "seaside-cliff-steps",
        "an Italian cantastorie storyteller on wide stone steps winding down a "
        "coastal cliffside at sunset, pastel houses stacked above and a calm sea "
        "below dotted with little boats. He holds up painted watercolor boards of "
        "a bedtime story while children sit on the warm steps, one leaning "
        "sleepily on an older sibling.",
    ),
]

# Numbering offset so this batch writes 11..15 and leaves the earlier ones in place.
START_INDEX = 11

OUTPUT_DIR = Path("docs/assets/hero-variants")


def main() -> None:
    settings = get_settings()
    if not settings.openrouter_api_key.get_secret_value():
        raise SystemExit("OPENROUTER_API_KEY is required in .env")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    client = ImageClient(settings)
    try:
        for i, (slug, scene) in enumerate(VARIANTS, start=START_INDEX):
            prompt = (
                f"{HERO_STYLE_PROMPT} Paint a warm, inviting scene that evokes the "
                f"Italian cantastorie tradition: {scene} The whole scene "
                "radiates warmth, safety, and the quiet magic of bedtime "
                "storytelling."
            )
            print(f"[{i}/{len(VARIANTS)}] generating {slug} ...")
            png = client.generate(prompt)
            out = OUTPUT_DIR / f"cantastorie-hero-{i:02d}-{slug}.png"
            out.write_bytes(png)
            print(f"    wrote {out} ({len(png)} bytes)")
    finally:
        client.close()


if __name__ == "__main__":
    main()
