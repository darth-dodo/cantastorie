# ADR-009: Language Packs — a Registry for Per-Language Content

**Date**: 2026-09-06
**Status**: Proposed
**Context**: Adding a language today means editing a hardcoded `Literal` and three scattered maps; the spoken UI prompts exist for Italian only, and `narrate.py` serves Italian prompts regardless of the requested language. English — the default child language — has no prompt set.
**Decider(s)**: Project Owner
**Relates to**: [ADR-008](ADR-008-narration-gemini-defaults-mistral-cloning.md) (narration provider — unchanged), [ADR-001](ADR-001-technology-stack.md) (plain-Python pipeline)

---

## Summary

Consolidate a language's **content** — its spoken UI prompts, its English display name, and (later) its glosses and authoring notes — into a single `LanguagePack` value keyed by the `Language` code, held in a small `src/pipeline/languages.py` registry. Today those bits are scattered: the `Language` literal lives in `models.py`, the English names in `write.py` (`LANGUAGE_NAMES`), the voices in `config.py` (`narration_voices`), and the spoken prompts as an Italian-only `IT_UTTERANCES` in `narrate.py` that is served for every language. Populate two packs now — **Italian** (moved verbatim) and **English** (the missing set, from product.md) — and read consumers off the registry so `narrate` finally speaks the requested language.

This is **not a provider adapter**. Narration stays on Gemini 3.1 Flash TTS via OpenRouter with one pinned house voice (ADR-008); the `narration_voices` map is unchanged. The `Language` literal **stays** as the reviewed-set gate: mypy and the CLI still refuse an unlisted language, so shipping an unreviewed language into a kids' app remains impossible.

---

## Problem Statement

### The Challenge

Adding a language, or fixing English, currently touches four unrelated places, and one of them is a latent bug:

- `models.py` — the `Language = Literal["it","es","en","el","de","bg","ru"]` type.
- `write.py` — `LANGUAGE_NAMES: dict[Language, str]` for the author prompt.
- `config.py` — `narration_voices: dict[str, str]` (already data-driven).
- `narrate.py` — `IT_UTTERANCES`, the five spoken UI prompts, **Italian only**, served regardless of the requested language.

There is no single home for "everything that makes a language a language," and English — the default child language — ships without its own prompts.

### Why This Matters

Seven languages are in product scope; the launch library needs each language's spoken prompts, authored natively. A pre-reader app must never surface an unreviewed language, so the set has to stay gated — but the per-language *content* should be easy to add and obviously complete.

### Success Criteria

- [ ] One home per language's content (`LanguagePack`).
- [ ] `narrate` emits the **requested** language's prompts (fixes the Italian-for-everything bug).
- [ ] A complete **English** pack exists.
- [ ] The `Language` literal gate is preserved (mypy + CLI still refuse unlisted languages).
- [ ] No new dependencies; plain Python.
- [ ] Italian output is byte-for-byte unchanged (regression-safe).

---

## Context

### Current State

`UtteranceName` is five prompts (`shelf_greeting`, `story_start`, `end_prompt`, `audio_retry`, `offline`). Voices are already a map with a fallback (`narration_voices.get(language, …)`). Content rules are language-agnostic. So the only scattered, language-specific *content* is the names and the prompts.

### Requirements

- Stay within settled architecture: plain Python, no new deps, no provider/key change ([ADR-008](ADR-008-narration-gemini-defaults-mistral-cloning.md), [ADR-001](ADR-001-technology-stack.md)).
- Keep static (mypy) and runtime (CLI) gating of the language set.
- Allow **partial coverage** — it/en now, the rest as their content is authored — and fail loudly for an unpopulated language rather than silently falling back.

---

## Options Considered

### Option A — `LanguagePack` registry, keep the `Literal` (chosen)

A `LanguagePack` frozen dataclass (`code`, `name`, `utterances`, room to grow) and a `PACKS: dict[Language, LanguagePack]` with a `get_pack(language)` accessor. Consumers read the registry; the literal stays.

- **Pros**: one home per language; fixes the narrate bug; keeps the type gate; trivial to extend a pack's fields; no deps.
- **Cons**: the language set is named in two places (the literal and the registry keys) — mitigated by a coverage test.
- **Risks**: registry/literal drift → caught by a test that asserts populated packs are complete.
- **Estimated Effort**: ~half a day (registry + move Italian + add English + refactor two consumers + tests).

### Option B — Full locale/provider adapter with runtime registration

Drop the literal; register languages dynamically via a plugin interface so a language is a self-contained module discovered at runtime.

- **Pros**: maximal extensibility; a language is fully self-contained.
- **Cons**: loses the compile-time/CLI gate — the single most valuable safety property for a kids' app; over-engineered for adding a dict entry; more surface to test.
- **Risks**: an unreviewed language could reach a child through a stray registration.
- **Estimated Effort**: 2–3 days, plus ongoing guardrails.

### Option C — Do nothing; add English inline

Leave the scatter, add an `EN_UTTERANCES` next to `IT_UTTERANCES`, and branch in `narrate`.

- **Pros**: lowest immediate effort.
- **Cons**: perpetuates the scatter and the "which map do I edit?" tax; the narrate language-selection fix is ad hoc; the third language repeats the pain.
- **Risks**: the Italian-for-everything bug recurs the next time someone adds content.
- **Estimated Effort**: ~1 hour, paid back with interest later.

## Comparison Matrix

| Criterion (weight) | A — Registry | B — Runtime adapter | C — Inline |
|---|---|---|---|
| Maintainability (0.3) | 9 | 7 | 4 |
| Safety-gate preserved (0.3) | 9 | 3 | 9 |
| Effort / YAGNI fit (0.2) | 8 | 3 | 9 |
| Extensibility (0.2) | 8 | 9 | 3 |
| **Weighted total** | **8.6** | **5.2** | **6.1** |

---

## Decision

### Chosen Option

**Option A** — a `LanguagePack` registry, with the `Language` literal retained as the gate.

**Rationale**: it removes the real pain (scatter + the narrate bug + missing English) at the lowest honest cost, without trading away the type/CLI gate that keeps an unreviewed language away from children. Option B buys extensibility the product does not need at seven curated languages, and pays for it with exactly the safety property this audience most requires. Option C leaves the bug in place.

**Key Factors**: the reviewed-set gate is non-negotiable for a pre-reader app; voices are already a map; content rules are already language-agnostic — so the registry only has to hold names and prompts today.

**Trade-offs Accepted**: the language set is named twice (literal + registry keys). A coverage test converts that from a latent drift risk into a caught error.

---

## Consequences

### Positive Outcomes

- Per-language content has one obvious home; adding a language is a pack, not a scavenger hunt.
- `narrate` speaks the requested language (bug fixed).
- English ships with a complete prompt set.

### Negative Outcomes

- A second listing of languages (registry keys alongside the literal).

### Risks and Mitigation

- **Registry drifts from the literal** → a test asserts each *populated* pack has all `UtteranceName` prompts and a non-empty name; `get_pack` raises a clear error for an unpopulated language (never a silent Italian fallback).
- **Scope creep into a provider adapter** → explicitly out of scope; narration provider and `narration_voices` are unchanged (ADR-008).

---

## Implementation Plan

1. **Registry** — `LanguagePack` + `PACKS` + `get_pack` in `src/pipeline/languages.py`; move `UtteranceName` there.
2. **Italian pack** — move `IT_UTTERANCES` verbatim (regression-safe).
3. **English pack** — add the five prompts from product.md's English column.
4. **Consumers** — `narrate.py` reads `get_pack(language).utterances`; `write.py` reads `get_pack(language).name`.
5. **Tests** — pack completeness for it/en, narrate language selection (English for `en`, Italian for `it`), loud failure for an unpopulated language.

**Rollback**: the change is additive and local; reverting to `IT_UTTERANCES` restores prior behaviour with no data migration.

---

## Validation

- `uv run pytest tests/pipeline/` green, including new language-pack and narrate-selection tests.
- `make check` (ruff + mypy strict) clean — the literal still types every `Language` parameter.
- Italian prompt bytes unchanged (existing narrate tests pass untouched).

---

## Related Decisions

- [ADR-008](ADR-008-narration-gemini-defaults-mistral-cloning.md) — narration provider and the pinned house voice; unchanged by this ADR.
- [ADR-001](ADR-001-technology-stack.md) — plain-Python pipeline, no new dependencies.
- `docs/product.md` — **Spoken Prompts** (the English/Italian/Spanish copy) and **Languages & Localization** (native authoring, the seven-language scope).

## References

### Code

- `src/pipeline/models.py` — `Language` literal (the gate).
- `src/pipeline/steps/narrate.py` — `UtteranceName`, `IT_UTTERANCES`.
- `src/pipeline/steps/write.py` — `LANGUAGE_NAMES`.
- `src/config.py` — `narration_voices`.

### External Resources

- [Gemini TTS — language coverage](https://ai.google.dev/gemini-api/docs/speech-generation) — the provider (ADR-008) already covers all seven scoped languages, so the constraint is content, not TTS.

---

## Metadata

**ADR Number**: 009
**Created**: 2026-09-06
**Version**: 1.0

**Authors**: Project Owner (decision), Claude (AI Assistant, template expansion)
**Reviewers**: Project Owner

**Tags**: languages, localization, i18n, spoken-prompts, narrate, registry, structural-pattern, english-pack, literal-gate, yagni
