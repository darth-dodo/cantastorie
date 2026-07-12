# ADR-004: Narration — Voxtral TTS plus Deepgram, ElevenLabs Retired

**Date**: 2026-07-11
**Status**: Accepted — amended by [ADR-008](ADR-008-narration-gemini-defaults-mistral-cloning.md) (default narration moves to Gemini TTS; Voxtral is scoped to voice cloning via the Mistral API; Deepgram's roles are unchanged)
**Context**: Removing ElevenLabs from the narration picture entirely; assigning its remaining roles to Deepgram alongside Voxtral via OpenRouter
**Decider(s)**: Project Owner

---

## Summary

ElevenLabs is **retired from Cantastorie entirely** — from the code, the configuration, the environment, and the documentation. [ADR-002](ADR-002-narration-provider.md) had already moved narration generation to **Voxtral Mini TTS via OpenRouter**, but it kept ElevenLabs alive in two residual roles: the documented **fallback voice** if Voxtral failed the bedtime-warmth gate, and the **timestamp escape hatch** ("switch to ElevenLabs, re-narrate, get native timings") for reading mode. This ADR hands both roles to **Deepgram**: word-level timings come from a **Deepgram speech-to-text transcription pass** over the narrated audio, and the fallback narrator becomes **Deepgram Aura TTS**. Voxtral remains the narrator; both providers are reached **through OpenRouter** where its audio endpoints carry them, keeping the one-key/one-gateway property — with the explicit, flagged caveat that Deepgram's availability on OpenRouter is **verified at implementation time**, and a direct `DEEPGRAM_API_KEY` (pipeline-only, never deployed) is the documented fallback transport if it is not. The cost of this decision is honest: ElevenLabs was the one option with *proven* warmth and native timestamps, and Deepgram's non-English TTS coverage is **unverified** — so retiring ElevenLabs trades a known-good safety net for a cheaper, simpler one that still has to prove itself at the AI-366 bake-off.

---

## Problem Statement

### The Challenge

ADR-002 chose Voxtral via OpenRouter as the starting narrator but left ElevenLabs half-alive: an optional `ELEVENLABS_API_KEY`, a dedicated httpx transport in `providers.py` requesting `with-timestamps`, config fields and a default voice ID, and documentation describing it as "deferred and under evaluation". The code, in fact, still implements *only* ElevenLabs — the ADR-002 code change never landed. That leaves the project maintaining a second vendor's transport, key surface, and mental model for a provider it does not intend to run, while the two things ElevenLabs was being kept around for — timestamps and a warmth fallback — have a credible home elsewhere.

### Why This Matters

- **Dead code is a liability**: an unexercised ElevenLabs transport with its own auth header, error shapes, and tests rots silently and confuses every future reader about what actually narrates stories.
- **Key surface**: every optional vendor key is one more secret to document, rotate, and explain in setup docs — against a product posture of radical simplicity (one key runs the pipeline).
- **Timestamps still need an owner**: reading mode (slice 6) needs word timings, and ADR-002's primary reconstruction path (Voxtral's own transcription model) is unproven, with ElevenLabs as the backstop. If ElevenLabs goes, the backstop must be replaced, not deleted.
- **The fallback voice needs an owner too**: if Voxtral fails the AI-366 warmth gate, the project needs a named alternative — "we'll figure it out" is not a rollback plan.

### Success Criteria

- [ ] No ElevenLabs code, config field, environment variable, or doc reference remains
- [ ] The pipeline still runs end to end on `OPENROUTER_API_KEY` alone in the default path
- [ ] A named, recorded path to word-level timings exists (Deepgram STT transcription pass)
- [ ] A named, recorded fallback narrator exists (Deepgram Aura TTS)
- [ ] Deepgram's availability through OpenRouter is verified at implementation; the direct-key fallback transport is documented if not
- [ ] The AI-366 bake-off is re-scoped from "ElevenLabs vs Voxtral" to "Voxtral vs Deepgram Aura"

---

## Context

### Current State

- **Decided (ADR-002)**: narration on `mistralai/voxtral-mini-tts-2603` via OpenRouter `POST /api/v1/audio/speech`; timestamps paused; ElevenLabs the documented fallback.
- **Built (code)**: `src/pipeline/providers.py` still implements the ElevenLabs transport (`xi-api-key`, `/v1/text-to-speech/{voice}/with-timestamps`); `src/config.py` still carries `elevenlabs_api_key`, `elevenlabs_base_url`, `elevenlabs_voice_id` (Matilda), `elevenlabs_tts_model`; `steps/narrate.py` still collapses ElevenLabs character alignment into word timings. The ADR-002 provider switch is pending. <!-- pragma: allowlist secret -->

This ADR therefore folds two moves into one code change: implement the Voxtral switch ADR-002 already decided, and remove ElevenLabs rather than demote it.

### Deepgram, specifically

- **Speech-to-text (Nova family)**: word-level timestamps are a core, first-class output of Deepgram transcription — every word arrives with start/end times. Pre-recorded transcription is priced per minute of audio (order of half a cent per minute), so timing the entire launch library is expected to cost **well under a dollar**.
- **Text-to-speech (Aura family)**: priced in the same order of magnitude as Voxtral (roughly $15 per million characters — about 10× cheaper than ElevenLabs), making it a credible fallback narrator on cost. Its bedtime warmth and its **non-English language coverage are unverified** and must be checked before it is relied on for Tier 1 languages.
- **Transport**: the intent is to reach Deepgram **through OpenRouter's audio endpoints** so the one-key property holds. Whether OpenRouter carries the needed Deepgram models is **unverified at the time of this decision** and is the first implementation checkpoint. If unavailable, Deepgram is called directly with a `DEEPGRAM_API_KEY` that exists only in the pipeline environment — accepted as a bounded exception to one-key, used only by the timing pass and only at authoring time.

### Requirements

**Functional Requirements**:

- Generate narration audio for story pages and the ten spoken prompts, per language (unchanged: Voxtral)
- Produce word-level timings for reading-mode karaoke by slice 6
- Name a fallback narrator for the AI-366 warmth gate

**Non-Functional Requirements**:

- **Simplicity**: no dormant vendor code; the default path runs on one key
- **Cost**: launch-library narration plus timing reconstruction stays in single-digit dollars
- **Privacy**: keys stay in the pipeline environment; playback remains bucket-direct with zero API calls
- **Honesty**: unverified properties (OpenRouter carriage of Deepgram, Aura's non-English coverage, Nova's Greek support) are labeled, not assumed

---

## Options Considered

### Option A: Status quo — keep ElevenLabs as the dormant fallback (ADR-002 as written)

**Description**: Implement the Voxtral switch but retain the ElevenLabs transport, config fields, and optional key as the documented fallback and timestamp escape hatch.

**Pros**:

- **Proven safety net**: ElevenLabs' warmth and native character timestamps are the one *known-good* answer to both open risks.
- **Zero re-decision cost**: nothing to evaluate; the fallback is already coded.

**Cons**:

- **Dormant second vendor**: transport code, key documentation, and tests maintained for a provider the project intends never to run.
- **Expensive if activated**: the fallback costs roughly 10× per character; invoking it also means re-narrating existing stories.
- **Split mental model**: every narration discussion carries an "…or ElevenLabs" asterisk indefinitely.

**Risks**:

- The fallback that is never exercised quietly breaks (API changes, voice deprecations) and fails exactly when needed.

---

### Option B: Voxtral narrates, Deepgram times and backs up — ElevenLabs removed (Chosen)

**Description**: Voxtral Mini TTS via OpenRouter remains the narrator (unchanged from ADR-002). Deepgram takes both roles ElevenLabs vacates: a Deepgram STT transcription pass over narrated audio produces word-level timings for reading mode, and Deepgram Aura TTS becomes the named fallback narrator for the AI-366 warmth gate. Both reached through OpenRouter where carried; direct Deepgram key as the flagged fallback transport.

**Pros**:

- **Timestamps from a timestamps specialist**: word timings are Deepgram STT's core competence, not a side effect — a stronger reconstruction path than Voxtral's unproven transcription model alone.
- **Decouples timing from narration**: any narrator's audio can be timed by the same pass; a future voice change never orphans the timing pipeline.
- **Trivial timing cost**: per-minute STT pricing times the launch library lands well under a dollar.
- **One vendor exits entirely**: no ElevenLabs code, key, or docs; the fallback narrator (Aura) is in the same cost class as Voxtral, so activating the fallback does not 10× the budget.

**Cons**:

- **Unverified OpenRouter carriage**: Deepgram-through-OpenRouter is intent, not fact, until implementation verifies it; the direct-key fallback dents (boundedly) the one-key property.
- **Aura's warmth and non-English coverage unproven**: the fallback narrator has the same open questions as the primary — retiring the one *proven* option means both horses in the AI-366 race are unproven.
- **Alignment quality unknown**: STT-reconstructed timings on synthetic speech must be validated against karaoke-highlighting quality (reading mode already degrades to sentence-level highlighting if they disappoint).

**Risks**:

- If both Voxtral and Aura fail the warmth gate, the project re-opens the provider decision with content already generated (mitigated: nothing library-scale is generated before AI-366).

**Estimated Effort**: One code change — point `narrate` at Voxtral (already decided), delete the ElevenLabs transport and config, add the transcription pass when slice 6 needs it.

---

### Option C: Deepgram Aura narrates, Voxtral times

**Description**: Invert the roles — Aura becomes the narrator, Voxtral's transcription model reconstructs timings.

**Pros**:

- Consolidates *generation* on the provider with the stronger STT story, if Aura's voices suit bedtime.

**Cons**:

- **Re-opens a settled decision for no gain**: ADR-002 chose Voxtral on grounds (cost, gateway, cloning path) that still hold; Aura is not cheaper, not more proven for bedtime, and its non-English coverage is the *weaker* of the two claims.
- **Wrong-way-around timing**: Voxtral transcription is the unproven timing path; Deepgram STT is the proven one. This option assigns each provider its weaker role.

**Why rejected**: It swaps both providers into the roles they are worse at.

---

### Option D: Voxtral only — its own transcription for timings, no named fallback

**Description**: Pure one-vendor play: Voxtral narrates, Voxtral's transcription model times, and no fallback narrator is named until one is needed.

**Pros**:

- **Purest one-key story**: nothing but OpenRouter and Mistral models end to end.
- Least documentation and config surface.

**Cons**:

- **All eggs, one unproven basket**: warmth, cross-language consistency, Greek support, *and* timing quality would all ride on a single provider none of which is validated yet.
- **No rollback plan**: "we'll pick a fallback when Voxtral fails" is exactly the unnamed-fallback state this ADR exists to prevent.

**Why rejected**: Simplicity bought by deleting the safety net rather than replacing it.

---

## Comparison Matrix

| Criteria | Weight | A: Keep ElevenLabs dormant | B: Deepgram takes both roles | C: Roles inverted | D: Voxtral only |
|----------|--------|---------------------------|------------------------------|-------------------|-----------------|
| **No dormant vendor code** | High | 1 | 5 | 4 | 5 |
| **One key / one gateway (default path)** | High | 3 | 4 | 4 | 5 |
| **Credible timestamp path** | High | 5 | 5 | 2 | 2 |
| **Credible warmth fallback** | High | 5 | 3 | 3 | 1 |
| **Cost if fallback activated** | Medium | 1 | 4 | 4 | — |
| **Total (weighted feel)** | - | 15 | **21** | 17 | 13 |

**Scoring**: 1 = Poor, 2 = Below Average, 3 = Acceptable, 4 = Good, 5 = Excellent

Option A wins only on the axes where "proven" beats "credible" — at the price of permanent dormant-vendor drag. Option B is the only option that scores at least Acceptable on every axis.

---

## Decision

### Chosen Option

**Selected**: Option B — Voxtral narrates; Deepgram provides word timings (STT transcription pass) and the fallback voice (Aura TTS); ElevenLabs is removed entirely.

**Rationale**:
ElevenLabs was being kept for two jobs it was never going to be asked to do cheaply: a 10×-cost fallback voice, and a timestamp source whose activation implied re-narrating the library. Deepgram covers both jobs at the same cost class as the rest of the pipeline — timings become a sub-dollar transcription pass that works on *any* narrator's audio (decoupling timing from voice choice permanently), and the fallback voice no longer multiplies the budget if invoked. The one thing genuinely given up is ElevenLabs' *proven-ness*; that is accepted because the AI-366 gate exists precisely to validate warmth before any library-scale content is generated, and because git history makes re-adding ElevenLabs a bounded, mechanical rollback if both Deepgram and Voxtral disappoint.

**Key Factors**:

- Word timings from a provider whose core product is word timings
- Timing pass is narrator-independent — no future voice change orphans reading mode
- Fallback narrator in the same cost class as the primary
- One vendor's code, key, and documentation surface removed outright
- AI-366 remains the gate before any of this is bet on at library scale

### Trade-offs Accepted

- **The proven option leaves the building**: both remaining voices (Voxtral, Aura) are unproven for bedtime warmth until AI-366.
- **OpenRouter carriage of Deepgram is unverified**: if absent, a `DEEPGRAM_API_KEY` (pipeline-only, authoring-time only) is a bounded exception to one-key — accepted and documented rather than hidden.
- **STT-reconstructed timings may underperform native ones**: accepted because reading mode degrades gracefully to sentence-level highlighting, and slice 6 is the first consumer.
- **Aura's non-English coverage and Nova's Greek support are unverified**: checked before reliance, same discipline as ADR-002's Greek caveat.

---

## Consequences

### Positive Outcomes

**Immediate Benefits**:

- `providers.py` drops to a single gateway transport in the default path; `config.py` loses four ElevenLabs fields; setup docs describe one required key
- The pending ADR-002 code change and the ElevenLabs removal land as one coherent change instead of two
- The timestamp story gains a concrete, costed mechanism instead of an escape hatch

**Long-term Benefits**:

- Narrator choice and timing generation are permanently decoupled — re-benchmarking voices never threatens reading mode
- Fallback activation is a config change in the same cost class, not a budget event

### Negative Outcomes

**Immediate Costs**:

- The AI-366 bake-off loses its proven reference voice; it now compares two unproven candidates (Voxtral vs Aura) on warmth and consistency
- A possible second transport (direct Deepgram) if OpenRouter does not carry it

**Trade-offs**:

- If both candidates fail the warmth gate, re-adding ElevenLabs is a superseding ADR plus a mechanical restore from git history — slower than flipping a dormant switch, by design

### Risks and Mitigation

**Risk 1**: OpenRouter does not carry the needed Deepgram models

- **Probability**: Unverified — this is the first implementation checkpoint
- **Impact**: Low — transport-level only
- **Mitigation**: Call Deepgram directly with a pipeline-only `DEEPGRAM_API_KEY`, used at authoring time by the timing pass (and the fallback voice if ever activated). The default narration path still runs on `OPENROUTER_API_KEY` alone.

**Risk 2**: STT-reconstructed word timings misalign with narrated audio

- **Probability**: Low-to-medium (aligning a transcript to its own source audio is STT's favorable case)
- **Impact**: Medium — karaoke highlighting quality in reading mode (slice 6)
- **Mitigation**: Validate alignment on the AI-366 story; reading mode already downgrades missing/poor timings to sentence-level highlighting rather than failing.

**Risk 3**: Neither Voxtral nor Aura passes the bedtime-warmth gate

- **Probability**: Unproven
- **Impact**: High — warmth is core to the product
- **Mitigation**: AI-366 runs before any library-scale generation. If both fail, re-open the provider decision (superseding ADR); ElevenLabs' transport is one `git revert` away and no content beyond the bake-off story would need re-narration.

**Risk 4**: Greek (or German) unsupported by the chosen paths

- **Probability**: Unverified — Voxtral's language list is unpublished (ADR-002); Aura's non-English coverage and Nova's Greek STT support are likewise unverified
- **Impact**: Medium — Tier 2 launch languages
- **Mitigation**: Confirm per-language support (narration *and* timing) before generating any content in that language; defer the language rather than improvise a provider.

---

## Implementation Plan

The code change is tracked separately; this plan records the intended sequence.

### Phase 1: One coherent provider change

- [ ] Point `narrate` at `mistralai/voxtral-mini-tts-2603` via OpenRouter `POST /api/v1/audio/speech` (implements ADR-002's pending switch)
- [ ] Delete the ElevenLabs transport from `providers.py`, the four `elevenlabs_*` fields from `config.py`, and the word-timing collapse of ElevenLabs character alignment from `steps/narrate.py`
- [ ] Remove `ELEVENLABS_API_KEY` from `.env.example` and all docs
- [ ] Confirm the pipeline runs end to end with only `OPENROUTER_API_KEY`

### Phase 2: Verify Deepgram carriage (first implementation checkpoint)

- [ ] Check whether OpenRouter's audio endpoints carry the needed Deepgram STT (and Aura TTS) models
- [ ] If not: add a minimal direct Deepgram transport gated behind `DEEPGRAM_API_KEY`, pipeline-only, and record the bounded one-key exception in `docs/architecture.md`

### Phase 3: Re-scoped AI-366 bake-off

- [ ] Generate the first Italian story; narrate on Voxtral and on Deepgram Aura
- [ ] Judge bedtime warmth and cross-language consistency; record the verdict
- [ ] Run the Deepgram STT timing pass over the winning narration; validate word-timing alignment quality
- [ ] Confirm per-language support (Greek especially) for both narration and timing before any content in that language

### Phase 4: Reading-mode timings (slice 6)

- [ ] Run the transcription pass over the published library's narration; populate `story.json` page timings
- [ ] Accept sentence-level highlighting for any story whose timings fail validation

### Rollback Plan

**Trigger Conditions**:

- Both Voxtral and Aura fail the AI-366 warmth/consistency gate
- Deepgram timing alignment is unacceptable *and* sentence-level highlighting is judged insufficient for reading mode

**Rollback Steps**:

1. Restore the ElevenLabs transport and config from git history; add `ELEVENLABS_API_KEY` back to the pipeline environment
2. Narrate (or re-narrate) affected stories on ElevenLabs — native character timestamps return with it
3. Record the reversal in a superseding ADR

---

## Validation

### Pre-Implementation Checklist

- [x] Both of ElevenLabs' residual roles have named successors (Deepgram STT for timings, Aura for fallback voice)
- [x] The default path still runs on one key; the possible exception is bounded, flagged, and authoring-time only
- [x] Every unverified claim is labeled (OpenRouter carriage, Aura non-English coverage, Nova Greek support, warmth)
- [x] The AI-366 gate still precedes any library-scale bet
- [x] Rollback to ElevenLabs is mechanical (git history), not a redesign

### Post-Implementation Validation

**Success Metrics**:

- Zero ElevenLabs references in code, config, environment, or docs
- Pipeline runs narration with only `OPENROUTER_API_KEY` (plus, at most, the flagged Deepgram key for the timing pass)
- AI-366 produces a recorded Voxtral-vs-Aura verdict and a timing-alignment verdict
- Launch-library narration plus timing stays in single-digit dollars

**Review Date**: AI-366 (the first Italian story — the narration-warmth and timing-alignment gate)

---

## Related Decisions

**Supersedes**:

- [ADR-002](ADR-002-narration-provider.md) — Narration Provider — Voxtral via OpenRouter to Start. The Voxtral choice carries forward unchanged; what this ADR replaces is ElevenLabs' residual standing as the documented fallback voice and the timestamp escape hatch.

**Related To**:

- [ADR-001](ADR-001-technology-stack.md) — Foundational stack (narration and timing both run through the OpenRouter gateway chosen there, subject to the Phase 2 carriage check; playback stays bucket-direct with zero API calls)

**Informs**:

- Reading mode (slice 6) — word timings now come from the Deepgram STT transcription pass
- The AI-366 bake-off — re-scoped from ElevenLabs-vs-Voxtral to Voxtral-vs-Aura
- [ADR-006](ADR-006-family-voice-narration.md) — family voice narration ("Nonna Narrates", Proposed) builds on exactly the two capabilities this ADR settles: the narrator's zero-shot voice cloning (Voxtral, carried forward from ADR-002; rescoped to the Mistral API by ADR-008) and a transcription pass (Deepgram STT), which doubles as that feature's machine-verified consent check
- The `settled-architecture` project skill — the narration row now reads "Voxtral Mini TTS via OpenRouter; Deepgram for word timings and fallback voice; ElevenLabs retired"

---

## References

### Code References

- `src/pipeline/providers.py` — ElevenLabs transport to delete; OpenRouter transport gains `/audio/speech` (and possibly a Deepgram transport, Phase 2)
- `src/config.py` — four `elevenlabs_*` fields to remove; Deepgram model/key settings if the direct transport is needed
- `src/pipeline/steps/narrate.py` — provider switch target; ElevenLabs alignment-collapse logic to remove
- `docs/architecture.md` — Technology Stack table, Narration / Audio section, Risks table

### External Resources

- [OpenRouter — Audio](https://openrouter.ai/docs) — OpenAI-compatible audio endpoints; carriage of Deepgram models to be verified at implementation
- [Deepgram — Speech-to-Text](https://developers.deepgram.com/) — Nova models; word-level timestamps on every transcript
- [Deepgram — Aura TTS](https://developers.deepgram.com/) — the named fallback voice; non-English coverage to be verified
- [Mistral Voxtral](https://mistral.ai/) — the narrator, unchanged from ADR-002

---

## Metadata

**ADR Number**: 004
**Created**: 2026-07-11
**Last Updated**: 2026-07-12 (marked amended by ADR-008)
**Version**: 1.0

**Authors**: Claude (AI Assistant)
**Reviewers**: Project Owner

**Tags**: narration, tts, stt, voxtral, deepgram, aura, openrouter, elevenlabs-retired, timestamps, reading-mode, fallback, ai-366
