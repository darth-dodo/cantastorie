# ADR-002: Narration Provider — Voxtral via OpenRouter to Start

**Date**: 2026-07-07
**Status**: Accepted
**Context**: Choosing the text-to-speech provider for story narration and spoken prompts
**Decider(s)**: Project Owner

---

## Summary

Narration will **start on Voxtral Mini TTS via OpenRouter** (`mistralai/voxtral-mini-tts-2603`), called through OpenRouter's OpenAI-compatible `POST /api/v1/audio/speech` endpoint, using the **existing OpenRouter key only** — no second vendor or key is needed to run the pipeline. This replaces the previously settled choice of ElevenLabs (multilingual_v2). The change buys a single key/single gateway and roughly a **10× lower narration cost** (the 19-story launch library is about $1–2 on Voxtral versus about $15–30 on ElevenLabs), plus a path to a cloned one-storyteller voice. The cost is that Voxtral's `/audio/speech` returns raw audio with **no word or character timestamps**, so the "timestamps from day one" design is **paused**: `story.json` page timings stay empty until reading mode (slice 6) reconstructs them. Warmth for bedtime, cross-language voice consistency, and Greek support are all **unproven** and are validated at issue AI-366 via an ElevenLabs-vs-Voxtral bake-off. ElevenLabs remains under evaluation and is the documented fallback if warmth or timestamps prove decisive.

---

## Problem Statement

### The Challenge

Every story and every spoken prompt in Cantastorie is narrated audio, generated once at authoring time and played back bucket-direct with zero API calls. The product asks for **one warm narrator identity across all five languages** (Italian, Spanish, English, Greek, German) and, for reading mode, **word-level timings** to drive karaoke highlighting. The original design settled on ElevenLabs multilingual_v2, which provides both native timestamps and a single multilingual voice. The owner has since decided to start on a single-key setup and explore ElevenLabs later — which forces a re-decision on the provider.

### Why This Matters

- **Cost dominates the library build**: narration is the most expensive per-character asset. At ElevenLabs' pricing the launch library costs roughly 10× what Voxtral would.
- **Key and gateway simplicity**: the story writer, safety judge, glosses, and images already run through OpenRouter. A narration provider on the same gateway means one key and one billing relationship for the whole pipeline.
- **Reading mode depends on timings**: karaoke highlighting (slice 6) needs word timings. The provider choice determines whether those come free with narration or must be reconstructed later.
- **Warmth is the product**: this is a bedtime app. A narrator that is accurate but not warm fails the core promise even if it meets every technical spec.

### Success Criteria

- [ ] The narrate step runs using only the existing OpenRouter key
- [ ] Narration is generated for all launch languages the provider supports
- [ ] Narration cost for the 19-story launch library stays in single-digit dollars
- [ ] A path to word-level timings for reading mode is identified and recorded
- [ ] Narration warmth is validated on a real Italian story before the library is built (AI-366)
- [ ] Greek support is confirmed before any Greek content is generated

---

## Context

### Current State

The pipeline's `narrate` step and the provider layer were designed around ElevenLabs: the provider requests `with-timestamps` on every call, and `story.json` was to store character-level timings from slice 1 even though karaoke highlighting does not ship until slice 6. The rationale was that discarding timestamps would mean regenerating all narration later at double cost.

This ADR changes that starting point. The narration **code** change (config, the `narrate` step, the provider transport) is tracked separately; this document records the decision, not its implementation.

**Voxtral via OpenRouter, specifically**:

- Model: `mistralai/voxtral-mini-tts-2603`
- Endpoint: OpenRouter's OpenAI-compatible `POST /api/v1/audio/speech`
- Request body: `{ model, input, voice, response_format }` where `voice` is a per-language voice ID (for example `"en_paul_happy"`) and `response_format` is `"mp3"` or `"pcm"`
- Response: raw audio bytes, **no timestamps**

### Requirements

**Functional Requirements**:

- Generate narration audio for story pages and the ten spoken prompts, per language
- Provide (now or later) word-level timings for reading-mode karaoke highlighting
- Sustain a recognizable, warm narrator identity suitable for bedtime

**Non-Functional Requirements**:

- **Cost**: launch-library narration in single-digit dollars
- **Simplicity**: prefer one key / one gateway for the whole pipeline
- **Privacy**: keys stay in the pipeline environment; playback is bucket-direct with zero API calls
- **Language coverage**: Italian and Spanish (Tier 1) confirmed; English, Greek, German (Tier 2) — Greek support **unverified**

---

## Options Considered

### Option A: ElevenLabs multilingual_v2 (original decision)

**Description**: The originally settled provider. A single multilingual voice across all five languages, with character-level timestamps returned on every TTS call.

**Pros**:

- **Native timestamps**: character-level timings on every call — reading-mode karaoke works from day one with no reconstruction.
- **Proven warmth**: widely regarded as best-in-class for natural, warm voice synthesis — a strong fit for bedtime narration.
- **One voice identity**: a single multilingual voice directly serves the one-storyteller promise across languages.

**Cons**:

- **Second vendor and key**: adds a provider outside the OpenRouter gateway — a second key, billing relationship, and point of failure.
- **Cost**: roughly $150–220 per million characters — about 10× Voxtral. The launch library runs about $15–30 in narration.

**Risks**:

- **Cost growth**: if the library or per-family packs grow, ElevenLabs' pricing scales the fastest of the options.

**Estimated Effort**: Baseline for the original design (provider transport + `with-timestamps` handling already sketched).

---

### Option B: Voxtral Mini TTS via OpenRouter (Chosen to start)

**Description**: Narrate through `mistralai/voxtral-mini-tts-2603` on OpenRouter's OpenAI-compatible `POST /api/v1/audio/speech`, using the existing OpenRouter key. Per-language voice IDs; `mp3` or `pcm` output; raw audio bytes with no timestamps.

**Pros**:

- **One key, one gateway**: reuses the OpenRouter key that already drives the writer, safety judge, glosses, and images — no second vendor to onboard or secure.
- **Cost**: roughly $16 per million characters — about a tenth of ElevenLabs. The 19-story launch library costs about $1–2 in narration versus about $15–30.
- **Voice cloning**: Voxtral offers zero-shot voice cloning, a concrete path toward a single cloned narrator identity across languages.

**Cons**:

- **No timestamps**: `/audio/speech` returns raw audio with no word or character timings. Reading-mode karaoke cannot be driven from narration output directly.
- **Unproven warmth**: narration warmth for bedtime is not yet validated.
- **Per-language voice IDs**: voices are selected per language (a persona per language), so cross-language consistency for the one-storyteller identity is unproven.
- **Unverified Greek**: Voxtral supports 9 languages but the list is not published; **Greek support is unverified** and must be confirmed before Greek content.

**Risks**:

- **Deferred timestamps become re-narration**: if timings must be reconstructed and reconstruction is unsatisfactory, the fallback is switching to ElevenLabs and accepting possible re-narration of affected stories.
- **Warmth or consistency shortfall**: could force a provider switch after content exists.

**Estimated Effort**: Low to switch the starting provider (config + `narrate` step + transport), tracked as a separate code task.

---

### Option C: Gemini TTS (`gemini-3.1-flash-tts-preview`)

**Description**: Google's Gemini text-to-speech preview model as the narration provider.

**Pros**:

- Modern neural TTS with multilingual reach
- Available through a major provider

**Cons**:

- **No timestamps**: does not emit word or character timings, so it shares Voxtral's reading-mode gap without Voxtral's offsetting advantages.
- **Preview maturity**: a Preview-labeled model carries stability and availability risk unsuitable for a foundational pipeline dependency.
- **Not on the existing gateway** in the same one-key sense as Voxtral-via-OpenRouter.

**Why rejected**: It carries the timestamp gap *and* Preview-maturity risk, with no cost or gateway advantage over Voxtral.

---

### Option D: OpenRouter chat models generally

**Description**: Use general-purpose OpenRouter chat/completion models rather than a dedicated TTS model.

**Pros**:

- Already on the OpenRouter gateway and key

**Cons**:

- **Not TTS-with-timestamps**: general chat models do not produce narration audio with word timings; they do not solve the narration problem at all.

**Why rejected**: Does not meet the basic functional requirement of producing narration audio with timings.

---

## Comparison Matrix

| Criteria | Weight | A: ElevenLabs | B: Voxtral/OpenRouter | C: Gemini TTS | D: OR chat models |
|----------|--------|---------------|-----------------------|---------------|-------------------|
| **One key / one gateway** | High | 2 | 5 | 2 | 5 |
| **Narration cost** | High | 2 | 5 | 3 | 3 |
| **Word/char timestamps** | High | 5 | 1 | 1 | 1 |
| **Proven bedtime warmth** | High | 5 | 3 | 3 | 1 |
| **One-voice identity across languages** | High | 5 | 3 | 3 | 1 |
| **Language coverage (incl. Greek)** | Medium | 5 | 3 | 4 | 2 |
| **Provider/model maturity** | Medium | 5 | 4 | 2 | 3 |
| **Voice cloning path** | Low | 4 | 5 | 2 | 1 |
| **Total Score** | - | 33 | **32** | 20 | 17 |

**Scoring**: 1 = Poor, 2 = Below Average, 3 = Acceptable, 4 = Good, 5 = Excellent

The matrix is close between A and B: ElevenLabs scores highest on timestamps, warmth, and one-voice identity, while Voxtral wins decisively on gateway simplicity and cost. The decision below turns on the owner's choice to start single-key and defer the timestamp-dependent feature, with a validation gate before the library is committed.

---

## Decision

### Chosen Option

**Selected**: Option B — Voxtral Mini TTS via OpenRouter, to start

**Rationale**:
Starting on Voxtral keeps the entire pipeline behind one key and one gateway and cuts launch-library narration cost by roughly 10×, which matters most while the library is being built and re-generated. The feature that ElevenLabs uniquely serves for free — word-level timings for reading-mode karaoke — is not needed until slice 6, and slice 1 does not use timings at all. That decoupling makes it acceptable to defer timestamps now and reconstruct them later, while keeping ElevenLabs as a documented fallback if warmth or timestamps prove decisive. Voxtral's zero-shot voice cloning also offers a concrete path to the one-storyteller identity.

**Key Factors**:

- One key / one gateway for the whole pipeline (writer, safety, glosses, images, and now narration)
- About 10× lower narration cost — roughly $1–2 versus $15–30 for the launch library
- Slice 1 does not use timings; reading-mode karaoke (slice 6) is the first consumer of them
- Zero-shot voice cloning as a path to a single narrator identity
- ElevenLabs remains evaluated and available as the fallback

### Trade-offs Accepted

- **No timestamps from day one**: the "timestamps from day one" design is paused. `story.json` page timings stay empty until reading mode reconstructs them — via Voxtral's transcription model (which does emit word timestamps) or by switching to ElevenLabs, accepting possible re-narration of affected stories.
- **Unproven warmth and cross-language consistency**: accepted pending the AI-366 bake-off, before the library is built.
- **Unverified Greek support**: accepted for Tier 1 (Italian, Spanish) work now; must be confirmed before any Greek content.

---

## Consequences

### Positive Outcomes

**Immediate Benefits**:

- The narrate step runs with only the existing OpenRouter key — no ElevenLabs key needed to run the pipeline
- Launch-library narration cost drops to single-digit dollars
- The pipeline keeps a single provider gateway end to end

**Long-term Benefits**:

- Zero-shot voice cloning is a path to one cloned narrator identity across languages
- Per-step model choice via OpenRouter means the narration model can be re-benchmarked with a string swap

### Negative Outcomes

**Immediate Costs**:

- Reading-mode karaoke timings must be reconstructed later rather than captured at narration time
- Warmth and cross-language consistency remain open until validated

**Trade-offs**:

- If reconstruction is unsatisfactory or warmth falls short, switching to ElevenLabs may require re-narrating stories that already exist

### Risks and Mitigation

**Risk 1**: Deferred timestamps cannot be reconstructed acceptably

- **Probability**: Medium
- **Impact**: Medium — reading-mode karaoke (slice 6) degrades to sentence-level highlighting or requires re-narration
- **Mitigation**: Reading mode already downgrades missing timings to sentence-level highlighting rather than failing. If word-level timings are required, reconstruct them via Voxtral's transcription model (which does emit word timestamps) or switch narration to ElevenLabs, accepting possible re-narration. Slice 1 does not use timings, so nothing is blocked now.

**Risk 2**: Narration warmth is inadequate for bedtime

- **Probability**: Unproven
- **Impact**: High — warmth is core to the product
- **Mitigation**: Validate at AI-366 (the first Italian story, the designed "validate narration warmth" gate) via an ElevenLabs-vs-Voxtral bake-off, before the library is built. ElevenLabs remains the fallback.

**Risk 3**: Cross-language voice consistency for the one-storyteller identity is unproven

- **Probability**: Unproven
- **Impact**: Medium — the "one warm narrator across every language" promise weakens if per-language voices diverge
- **Mitigation**: Evaluate per-language voice IDs and zero-shot cloning at AI-366; consistency is an explicit acceptance criterion of the bake-off.

**Risk 4**: Greek is not supported by Voxtral

- **Probability**: Unverified (Voxtral lists 9 languages but does not publish the list)
- **Impact**: Medium — Greek is a Tier 2 launch language
- **Mitigation**: Confirm Greek support before generating any Greek content. If unsupported, narrate Greek via ElevenLabs (or another provider) while Tier 1 stays on Voxtral, or defer Greek content.

---

## Implementation Plan

The narration code change is tracked as a separate task; this plan records the intended sequence, not work done in this docs change.

### Phase 1: Switch the starting provider

- [ ] Point the `narrate` step at `mistralai/voxtral-mini-tts-2603` via OpenRouter `POST /api/v1/audio/speech`
- [ ] Use per-language voice IDs; request `mp3` (or `pcm`) output; store raw audio bytes
- [ ] Leave `story.json` page timings empty (timestamps paused)
- [ ] Confirm the pipeline runs with only `OPENROUTER_API_KEY` set

### Phase 2: Validate warmth and consistency (AI-366)

- [ ] Generate the first Italian story on both Voxtral and ElevenLabs
- [ ] Compare narration warmth for bedtime and cross-language voice consistency
- [ ] Confirm Greek support before any Greek content
- [ ] Record the outcome; if Voxtral fails the warmth/consistency gate, adopt ElevenLabs

### Phase 3: Reading-mode timings (slice 6)

- [ ] Reconstruct word timings via Voxtral's transcription model, or switch narration to ElevenLabs
- [ ] Accept possible re-narration of affected stories if switching providers

### Rollback Plan

**Trigger Conditions**:

- Voxtral fails the AI-366 warmth or consistency gate
- Reading-mode timing reconstruction proves unacceptable
- Greek (or another needed language) is unsupported

**Rollback Steps**:

1. Point the `narrate` step at ElevenLabs multilingual_v2 (Option A) and add `ELEVENLABS_API_KEY` to the pipeline environment
2. Regenerate affected narration (native timestamps return with ElevenLabs)
3. Record the switch in a superseding ADR

---

## Validation

### Pre-Implementation Checklist

- [x] Decision keeps the pipeline on a single key/gateway
- [x] Cost impact is quantified (about 10× lower for the launch library)
- [x] The timestamp trade-off is explicit and has a reconstruction/fallback path
- [x] A warmth/consistency validation gate exists before the library is built (AI-366)
- [x] Unverified items (Greek, warmth, cross-language consistency) are labeled as such

### Post-Implementation Validation

**Success Metrics**:

- Pipeline runs narration with only `OPENROUTER_API_KEY`
- Launch-library narration cost lands in single-digit dollars
- AI-366 bake-off produces a recorded warmth/consistency verdict
- Greek support confirmed before Greek content

**Review Date**: AI-366 (the first Italian story — the designed narration-warmth gate)

---

## Related Decisions

**Supersedes**:

- The prior settled choice of ElevenLabs multilingual_v2 with character-level timestamps as the starting narration provider (ElevenLabs is now deferred and under evaluation, not the default)

**Related To**:

- [ADR-001](ADR-001-technology-stack.md) — Foundational stack (narration runs through the OpenRouter gateway chosen there; playback stays bucket-direct with zero API calls)

**Informs**:

- Reading mode (slice 6) — how word timings are obtained
- The `settled-architecture` project skill — the narration row now reads "Voxtral Mini TTS via OpenRouter to start; ElevenLabs under evaluation"

---

## References

### Code References

- `src/pipeline/steps/narrate.py` — the narrate step (provider switch target; code change tracked separately)
- `src/pipeline/providers.py` — provider transports; the OpenRouter transport gains `/audio/speech`
- `src/config.py` — per-step model choices and provider keys
- `docs/architecture.md` — Technology Stack table and the Narration / Audio section

### External Resources

- [OpenRouter — Audio / Speech](https://openrouter.ai/docs) — OpenAI-compatible `POST /api/v1/audio/speech`
- [Mistral Voxtral](https://mistral.ai/) — Voxtral model family (TTS and transcription)
- [ElevenLabs multilingual_v2](https://elevenlabs.io/docs) — the deferred fallback with native timestamps

---

## Metadata

**ADR Number**: 002
**Created**: 2026-07-07
**Last Updated**: 2026-07-07
**Version**: 1.0

**Authors**: Claude (AI Assistant)
**Reviewers**: Project Owner

**Tags**: narration, tts, voxtral, openrouter, elevenlabs, timestamps, reading-mode, cost, one-key, ai-366, deferred-timestamps
