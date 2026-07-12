"""Generate a hero image for the README using the pipeline's own STYLE_PROMPT."""

from pathlib import Path

from src.config import get_settings
from src.pipeline.steps.illustrate import STYLE_PROMPT, ImageClient

PROMPT = (
    f"{STYLE_PROMPT} Paint a warm, inviting scene that evokes the Italian "
    "cantastorie tradition: a gentle storyteller in a moonlit piazza, "
    "pointing at painted watercolor boards showing a bedtime story unfolding. "
    "A small child listens from a window above, eyes heavy with sleep. Stars "
    "twinkle softly. The whole scene radiates warmth, safety, and the quiet "
    "magic of bedtime storytelling."
)

OUTPUT = Path("docs/assets/cantastorie-hero.png")


def main() -> None:
    settings = get_settings()
    if not settings.openrouter_api_key.get_secret_value():
        raise SystemExit("OPENROUTER_API_KEY is required in .env")
    client = ImageClient(settings)
    try:
        png = client.generate(PROMPT)
    finally:
        client.close()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(png)
    print(f"Wrote {OUTPUT} ({len(png)} bytes)")


if __name__ == "__main__":
    main()
