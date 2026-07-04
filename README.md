# Cantastorie

[![CI](https://github.com/darth-dodo/cantastorie/actions/workflows/ci.yml/badge.svg)](https://github.com/darth-dodo/cantastorie/actions/workflows/ci.yml)

> Bedtime stories your child steers, in the languages your family speaks. Told aloud, painted in watercolor, and approved by you before a single word reaches little ears.

The Italian *cantastorie* stood in the piazza, sang a tale, and pointed at painted boards. This app is that craft, revived carefully: one warm narrator voice, soft watercolor pages, and a child's finger choosing the path — with a parent as the piazza's gatekeeper.

---

## Why

Pre-readers can't use story apps built on text, and screen apps built on taps train the wrong appetite at bedtime. Multilingual families juggle one-language apps with robotic voices in the smaller languages. And parents have no way to fully preview generated content before their child meets it.

Cantastorie is built on one belief: **a bedtime app should wind a child down.** Voice carries the story, pictures carry the choices, and nothing on screen asks a pre-reader to read.

## How a Story Night Works

1. A tap wakes the shelf, which greets the child aloud: *"Ciao! Quale storia ascoltiamo oggi?"*
2. Tap a cover — *"Si parte!"* — and the story begins. Two taps, four seconds, no reading.
3. Watercolor pages turn themselves when the narration ends. One big play-pause button is the only control.
4. At a branch point, two picture cards appear with spoken labels. The child taps one; the story follows. A child who drifts off mid-choice still gets a complete, gentle ending.
5. Every story lands on comfort or sleepiness — and if no one taps after the end, a soft *"Buonanotte, tesoro."*

## What Makes It Different

| | |
|---|---|
| **Voice-first** | Every prompt is spoken in the child's language; zero required text in child mode |
| **One narrator** | The same warm voice tells every story in Italian, Spanish, English, Greek, and German |
| **Child-steered** | Branching stories with picture-tap choices — agency without reading |
| **Watercolor, always** | Soft palette, rounded characters, nothing frightening — bedtime, not Saturday cartoons |
| **Parent-approved** | Every word, picture, and sound is seen and heard by an adult before any child meets it |
| **Reading mode** | Optional karaoke word highlighting with tap-word glosses, for reading-along families |
| **Truly private** | No accounts, no tracking, no analytics. Progress lives in the browser and exports to a file. Nothing about the child ever leaves the device. |

## Languages

Italian and Spanish are the flagships — deepest content, first through every quality gate. English, Greek, and German ride along. Stories are authored natively per language, never translated: an Italian story reaches for biscotti della nonna, a Spanish one for magdalenas.

## Safety, Stated Plainly

No violence beyond the mildest peril. No monsters, darkness-as-threat, or abandonment. No brands, no romance, no real people. Kind characters who resolve things through help, never punishment. Every story passes a machine safety gate *and* a parent's eyes and ears before it reaches a shelf — a model mistake needs a human mistake on top of it to reach a child.

## Status

In design and early build. The product is fully specified and the architecture is settled; the first vertical slice — one Italian story, told aloud with watercolor pages — is next.

## Development

Requires [uv](https://docs.astral.sh/uv/), Node.js 20+, and Python 3.12 (uv installs it automatically).

```bash
make install        # uv sync + npm install
make install-hooks  # pre-commit hooks (lint, format, types, secrets, commit style)
make dev            # run the FastAPI app at http://localhost:8000
make dev-css        # watch and compile Tailwind CSS (run alongside make dev)
make test           # all tests (pytest + Vitest)
make check          # lint + format check + strict mypy
make help           # list every target
```

Copy `.env.example` to `.env` for pipeline work (OpenRouter + ElevenLabs keys); the player needs no keys at story time. Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/), enforced by commitizen via pre-commit.

Every PR runs lint, format check, strict mypy, pytest, Vitest, a Bandit security scan, a Tailwind compile, and a Docker build ([ci.yml](.github/workflows/ci.yml)). Deployment targets Render via [render.yaml](render.yaml), following habla-hermano's pattern.

## Documentation

- [Product Specification](docs/product.md) — vision, behaviors, content rules, decision log
- [Architecture](docs/architecture.md) — the FastAPI app, the Web Audio player, and the authoring pipeline

## License

MIT
