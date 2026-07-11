# Skill: story-generation

## Overview

Cantastorie stories are precomputed at authoring time via the pipeline CLI (`src/pipeline/cli.py`). A played story costs zero API calls — narration, images, and glosses are all generated before publish.

## When to Use

- Generating a new story for development or the library
- Debugging a pipeline step failure
- Working on pipeline step functions in `src/pipeline/steps/`

## Pipeline Architecture

The pipeline is **plain Python + Pydantic AI** with **filesystem checkpoints** — no graph framework. Each step writes to a working folder; the folder is the checkpoint store.

```
src/pipeline/
  cli.py          # Typer CLI entry point
  generate.py     # Orchestrates the full pipeline
  steps/          # Individual step functions
  publish.py      # Uploads to Cloudflare R2
  cache.py        # Asset caching
  providers.py    # OpenRouter integration
  content_rules.py # Content validation rules
  models.py       # Pydantic models for structured outputs
```

## Key Constraints

- **OpenRouter only** — all LLM, image, and narration access goes through OpenRouter. Never suggest direct provider SDKs.
- **Voxtral Mini TTS** (`mistralai/voxtral-mini-tts-2603`) for narration via OpenRouter. ElevenLabs is a deferred fallback (see ADR-002).
- **One key** — `OPENROUTER_API_KEY` runs the entire pipeline end to end.
- **Safety gate** — every story passes a safety verdict (Pydantic model) before publish.
- **Bucket-direct** — published assets go to Cloudflare R2; the player fetches them directly, never through the app server.

## Dev Story Scripts

Quick helpers in `scripts/`:
- `generate_dev_story.py` — generate a minimal story for development
- `generate_dev_greeting.py` — generate a shelf greeting

## Running the Pipeline

```bash
# Full pipeline
uv run cantastorie generate --story <config>

# Publish to R2
uv run cantastorie publish --story <id>

# Or use the dev scripts
python scripts/generate_dev_story.py
```

## Testing Pipeline Code

- Providers are mocked in unit tests — no network calls.
- `moto[s3]` is used for S3/R2 publishing tests.
- Run: `uv run pytest tests/pipeline/`
