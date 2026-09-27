"""Generate four cohesive watercolor spot images for the landing how-it-works section.

Usage:
    uv run python scripts/gen_landing_spots.py

Each image is generated via the project's ImageClient (same path as the pipeline),
then normalized to 192x344 px (2x retina for the 96x172 slot) via sips and saved
to src/static/img/landing-spot-{taps,choice,narrator,review}.png.

The OPENROUTER_API_KEY is read from the .env file (never hardcoded).
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: load .env so OPENROUTER_API_KEY is available before importing
# Settings (pydantic-settings reads from env vars, not a file by default).
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader — no dependency on python-dotenv."""
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Remove surrounding quotes if any
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(_ROOT / ".env")

# ---------------------------------------------------------------------------
# Now it is safe to import project modules.
# ---------------------------------------------------------------------------

from src.config import get_settings  # noqa: E402
from src.pipeline.steps.illustrate import ImageClient  # noqa: E402

# ---------------------------------------------------------------------------
# Spot prompts — 4 steps, cohesive brand palette.
# ---------------------------------------------------------------------------

_BRAND = (
    "Soft watercolor illustration in the Cantastorie brand style: warm indigo-violet "
    "palette, pale parchment backgrounds, rounded friendly characters with soft edges, "
    "gentle diffuse light, calm bedtime mood. Portrait orientation (tall frame). "
    "No text, no letters, no numbers, no writing anywhere in the image. "
    "Keep the palette muted and cosy — this is for a child's bedtime app."
)

SPOTS: list[tuple[str, str]] = [
    (
        "taps",
        (
            f"{_BRAND} A small child (around 3-5 years old) sitting comfortably, "
            "reaching a chubby finger toward a glowing story-cover tile. The cover "
            "glows softly with warm amber light. Cosy bedroom setting. Full-bleed "
            "portrait composition, child centred in the lower two-thirds."
        ),
    ),
    (
        "choice",
        (
            f"{_BRAND} Two large picture-choice cards float side by side. One card "
            "shows a friendly forest creature, the other a shining star. Both cards "
            "are gently illuminated, pastel watercolor style. A small hand hovers "
            "between them, about to choose. Portrait orientation."
        ),
    ),
    (
        "narrator",
        (
            f"{_BRAND} An abstract audio bloom: soft concentric ripples of warm "
            "violet and amber radiating from a central glow, like a gentle voice "
            "filling a quiet room. No characters — pure warmth and sound visualised "
            "as watercolor washes. Portrait orientation, centred bloom."
        ),
    ),
    (
        "review",
        (
            f"{_BRAND} A grown-up (parent or guardian) sitting in a cosy armchair "
            "at night, reading a story page on a tablet. Their face is lit warmly "
            "by the screen, expression calm and approving. A small child's bedroom "
            "is visible softly in the background. Portrait composition, figure "
            "centred."
        ),
    ),
]

# Target dimensions (2x retina for the 120x214 slot in landing.css)
TARGET_W = 192
TARGET_H = 344
DEST_DIR = _ROOT / "src" / "static" / "img"


def _sips_dims(path: Path) -> tuple[int, int]:
    """Return (width, height) of a PNG via sips."""
    result = subprocess.run(
        ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    pw, ph = None, None
    for raw_line in result.stdout.splitlines():
        if "pixelWidth" in raw_line:
            pw = int(raw_line.split(":")[1].strip())
        elif "pixelHeight" in raw_line:
            ph = int(raw_line.split(":")[1].strip())
    if pw is None or ph is None:
        raise RuntimeError(f"Could not read dims from {path}")
    return pw, ph


def _sips_resize_crop(src: Path, dst: Path, w: int, h: int) -> None:
    """Use macOS sips to resize+crop src to exactly wxh, write to dst."""
    pw, ph = _sips_dims(src)

    # Scale so that both dims >= target (cover-fill strategy)
    scale = max(w / pw, h / ph)
    scaled_w = int(pw * scale)
    scaled_h = int(ph * scale)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        tmp = Path(tf.name)

    # Resize
    subprocess.run(
        ["sips", "-z", str(scaled_h), str(scaled_w), str(src), "--out", str(tmp)],
        check=True,
        capture_output=True,
    )

    # Center-crop to exact target dims
    crop_x = (scaled_w - w) // 2
    crop_y = (scaled_h - h) // 2
    subprocess.run(
        [
            "sips",
            "--cropToHeightWidth",
            str(h),
            str(w),
            "--cropOffset",
            str(crop_y),
            str(crop_x),
            str(tmp),
            "--out",
            str(dst),
        ],
        check=True,
        capture_output=True,
    )
    tmp.unlink(missing_ok=True)


def _generate_spots(client: ImageClient) -> tuple[list[str], list[str]]:
    """Generate all spots; return (generated_slugs, failed_slugs)."""
    generated: list[str] = []
    failed: list[str] = []

    for slug, prompt in SPOTS:
        dest = DEST_DIR / f"landing-spot-{slug}.png"
        print(f"\n[{slug}] Generating...")
        try:
            png_bytes = client.generate(prompt)
        except Exception as exc:
            print(f"  Failed for '{slug}': {exc}")
            failed.append(slug)
            continue

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            raw = Path(tf.name)
        raw.write_bytes(png_bytes)
        print(f"  Raw PNG: {len(png_bytes):,} bytes")

        print(f"  Normalizing to {TARGET_W}x{TARGET_H} -> {dest.name}")
        _sips_resize_crop(raw, dest, TARGET_W, TARGET_H)
        raw.unlink(missing_ok=True)

        result = subprocess.run(
            ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(dest)],
            capture_output=True,
            text=True,
            check=True,
        )
        print(f"  OK {dest.name}: {result.stdout.strip()}")
        generated.append(slug)

    return generated, failed


def _fallback_crop(slug: str) -> None:
    """Center-crop an existing image to TARGET_W x TARGET_H in place."""
    dest = DEST_DIR / f"landing-spot-{slug}.png"
    if not dest.exists():
        print(f"  No existing file for {slug}, skipping fallback.")
        return
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        tmp = Path(tf.name)
    shutil.copy(dest, tmp)
    _sips_resize_crop(tmp, dest, TARGET_W, TARGET_H)
    tmp.unlink(missing_ok=True)
    print(f"  Cropped fallback -> {dest.name}")


def main() -> None:
    settings = get_settings()
    if not settings.openrouter_api_key.get_secret_value():
        sys.exit("OPENROUTER_API_KEY not set — check your .env file.")

    print(f"Using model: {settings.image_model}")
    client = ImageClient(settings)

    DEST_DIR.mkdir(parents=True, exist_ok=True)

    try:
        generated, failed = _generate_spots(client)
    finally:
        client.close()

    print(f"\n{'=' * 50}")
    print(f"Generated:  {generated}")

    if failed:
        print(f"Failed:     {failed}")
        print("\nFalling back for failed slugs: cropping existing images to portrait.")
        for slug in failed:
            _fallback_crop(slug)

    # Safety net: fix the review image if it is still landscape
    review_path = DEST_DIR / "landing-spot-review.png"
    if review_path.exists():
        r = subprocess.run(
            ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(review_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if "1408" in r.stdout or "768" in r.stdout:
            print("\nFixing landscape review image via center-crop...")
            _fallback_crop("review")
            print("  review fixed to portrait")


if __name__ == "__main__":
    main()
