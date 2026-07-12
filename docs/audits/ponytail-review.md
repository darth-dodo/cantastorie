# Ponytail-Review Audit

**Date**: 2026-07-12
**Branch**: `ponytail-audit`
**Base**: `main` @ `8d042d5` (post-Voxtral migration)
**Reviewer**: Sisyphus (ponytail-review skill, `full` intensity)
**Scope**: `src/` (Python + JS), `pyproject.toml`
**Out of scope**: correctness bugs, security holes, performance. This audit hunts complexity only.

---

## Findings

### delete: `audit` CLI command is a scaffold

```
src/pipeline/cli.py:22-24: delete: _not_yet() helper. Only caller is audit(). Nothing replaces it.
src/pipeline/cli.py:66-69: delete: audit() command. Scaffold pointing at AI-378 — not a working feature. Re-add when AI-378 lands.
```

### yagni: `mockView` default in the design shell

```
src/static/js/screens.js:11-16: delete: mockView constant. Hardcoded mock captions/beadColors for covers without a published story.
src/static/js/screens.js:100: shrink: buildPlayer default param view=mockView. Pass undefined, handle null view inline.
src/static/js/screens.js:148: shrink: updatePlayer default param view=mockView. Same.
```

`main.js` already loads real stories from the manifest; the mock only backs covers whose pipeline hasn't produced yet. During early build this is a real use case — **defer until at least one language shelf is fully populated**, then delete. Not urgent.

---

## Non-findings (checked, not over-engineered)

| Area | Why it's fine |
|------|---------------|
| `Publisher` Protocol (`workshop.py:49`) | One-method Protocol, but used for FastAPI DI in `test_routes.py:87`. Standard pattern. |
| `get_publisher()` wrapper (`workshop.py:63`) | FastAPI DI seam. Tests override it. Not YAGNI. |
| `build_model()` (`providers.py:18`) | Called by write, revise, safety steps. Real reuse. |
| `_generate_pack()` (`manager.py:33`) | Injectable testing seam in `RunManager`. Standard pattern. |
| `PROMPT_VERSION = 1` (write/revise/safety) | Cache invalidation key for prompt text changes. Correct, not scaffolding. |
| Revise retry loop (`revise.py:127`) | A plain `while` with `MAX_REVISIONS = 2`. ADR-004 explicitly chose this over a framework. Already ponytail. |
| Empty `__init__.py` files | Python package markers. Not YAGNI. |
| `lru_cache` on `get_settings()` (`config.py`) | Stdlib singleton. Correct. |
| Audio engine epoch/pendingStart (`audio-engine.js`) | Guards against stale decode callbacks during async load. Real edge case for Web Audio. |
| FSM (`fsm.js`) | 44 lines, no deps, handles invalid transitions as warn-and-ignore. Not overbuilt. |
| `NarrationClient` (`providers.py`) | Thin transport around OpenRouter `/audio/speech`. One endpoint, one responsibility. |
| `_synthesize_cached` (`narrate.py`) | Content-addressed cache memoizer. Correct pattern, already ponytail. |

---

## Resolved since audit was written

### ~~delete: ElevenLabs dead code~~ — RESOLVED

ADR-004 (Accepted, 2026-07-11) retired ElevenLabs entirely. PR #30 (AI-391) landed the migration: Voxtral TTS via OpenRouter, all ElevenLabs config fields and transport removed, character-alignment collapse logic deleted. The codebase now runs on `OPENROUTER_API_KEY` alone. **No action needed.**

---

## Score

```
net: ~30 lines possible.
```

~22 from the `audit()` scaffold + `_not_yet` helper. ~6 from `mockView` (deferred). The codebase is otherwise lean.

The settled architecture (plain Python, no graph framework, stdlib-first, Pydantic models, vanilla JS FSM) is already ponytail-philosophy. The Voxtral migration cleaned the one big piece of dead flexibility.

---

## Recommended action

1. **Delete `audit()` scaffold** — re-add when AI-378 is ready.
2. **Defer `mockView`** — delete once a language shelf is fully populated.

Everything else: ship.
