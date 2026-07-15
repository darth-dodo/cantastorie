# AGENTS.md — Cantastorie

> Bedtime stories a child steers, told aloud in the languages the family speaks; authored offline, approved by a parent.

## What This Project Is

Cantastorie is one FastAPI application with three faces: a child player (vanilla ES modules + Web Audio API), a parent area (Jinja2 + HTMX + Tailwind), and a plain-Python authoring pipeline (Pydantic AI + OpenRouter). Stories are precomputed — narration, images, and glosses generated at authoring time; a played story costs zero API calls.

## Commands

| Task | Command |
|------|---------|
| Install deps | `make install` |
| Install git hooks | `make install-hooks` |
| Run dev server | `make dev` (FastAPI at :8000) |
| Watch CSS | `make dev-css` |
| Build CSS | `make build-css` |
| Run all tests | `make test` |
| Python tests | `uv run pytest` |
| JS tests | `npx vitest run` |
| E2E tests | `npx playwright test` |
| Test coverage | `make test-cov` |
| Lint | `make lint` |
| Lint + fix | `make lint-fix` |
| Format | `make format` |
| Format check | `make format-check` |
| Typecheck | `make typecheck` (mypy src/) |
| All checks | `make check` |
| Pre-commit | `make pre-commit` |

**Always run `make check` before committing.**

## Project Structure

```
src/
  api/          # FastAPI app, routes
  pipeline/     # Authoring pipeline: generate, publish, cache, CLI, steps
  static/       # CSS, JS (player), content (generated stories)
  templates/    # Jinja2 templates
  config.py     # Pydantic settings
tests/
  js/           # Vitest tests
  e2e/          # Playwright tests
  test_*.py     # pytest tests
docs/
  architecture.md    # Settled technical design (authority)
  product.md         # Product spec with bold-named behaviors
  system-overview.md # Code as-built map
  adr/               # Architecture Decision Records
  plans/             # Slice implementation plans
  design/            # Feature design docs
scripts/             # Dev story generation helpers
deploy/              # Deployment config
```

## Settled Architecture — Read Before Proposing

The stack is **settled** in `docs/architecture.md` and ADRs in `docs/adr/`. Read them before proposing any dependency or structural change. Key decisions:

- **No frontend framework** — vanilla ES modules, FSM-managed player. No React/Vue/Svelte.
- **No bundler** — Tailwind CLI only for parent UI. No Vite/webpack/esbuild/TypeScript.
- **Web Audio API** — decoded buffers + gain nodes, not `<audio>` tags (iOS volume constraint).
- **Bucket-direct playback** — story assets fetched from Cloudflare R2, never through the app server.
- **Plain Python pipeline** — filesystem checkpoints, no LangGraph or graph framework.
- **OpenRouter** — one gateway for all LLM/image/narration. No direct provider SDKs.
- **Gemini 3.1 Flash TTS** via OpenRouter (ADR-008) for narration. Voxtral on the Mistral API is reserved for family-voice cloning (Phase 4). ElevenLabs is retired.
- **Render hosting** via Docker (`render.yaml`).
- **IndexedDB** for child state. No cookies, no server-side child state.

Proposing a framework, bundler, direct provider SDK, or a second narration key contradicts settled decisions. To change one, edit `docs/architecture.md` and add/supersede an ADR — never install around it.

## Code Style

- Python 3.12+, Ruff for linting and formatting, mypy for types.
- Line length: 100 chars.
- No comments unless explicitly requested.
- Follow existing patterns in `src/` — look at neighboring files first.

## Testing

- **pytest** for Python (`tests/`). Providers mocked; no network in unit tests. moto for S3.
- **Vitest** for JS (`tests/js/`), jsdom environment.
- **Playwright** for E2E (`tests/e2e/`).
- Run `make test` before declaring work done.

## Git Conventions

- Conventional commits enforced via commitizen pre-commit hook.
- Pre-commit runs: trailing-whitespace, end-of-file-fixer, ruff, ruff-format, mypy, detect-secrets.
- Never commit secrets — `.env` is gitignored. `detect-secrets` baseline at `.secrets.baseline`.

## Environment

- `.env` holds `OPENROUTER_API_KEY` (required for pipeline) and optionally `ELEVENLABS_API_KEY`.
- Copy `.env.example` to `.env` and fill in.
- Keys are pipeline-only — never in the browser, never at story time.

## Docs Authority

| Doc | Authority |
|-----|-----------|
| `docs/architecture.md` + ADRs | Settled technical decisions |
| `docs/product.md` | Product spec; bold-named behaviors are code-affecting |
| `docs/system-overview.md` | Code as-built; where it and code disagree, fix the doc |
| `docs/adr/` | Architecture Decision Records (ADR-NNN-kebab-title.md) |

## Sibling Project

`../habla-hermano/` is the pattern source for docs structure and conventions. Cantastorie's own `docs/architecture.md` overrides any sibling-project guidance.
