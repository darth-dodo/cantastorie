# ADR-008: Default Voices on Gemini TTS, Cloning Scoped to Mistral

**Date**: 2026-07-11
**Status**: Accepted
**Context**: Field testing broke ADR-004's assumption that Voxtral via OpenRouter could carry all five launch languages and the cloning path; default narration and voice cloning get separate homes
**Decider(s)**: Project Owner
**Amends**: [ADR-004](ADR-004-narration-deepgram-voxtral.md) (narration: Voxtral TTS plus Deepgram)
**Depends on**: [ADR-006](ADR-006-family-voice-narration.md) (Nonna Narrates)

---

## Summary

Default narration for all shelf content moves to **Gemini 3.1 Flash TTS via OpenRouter**; **voice cloning** (the family voices of ADR-006, and any bespoke narrator identity) runs **exclusively through Voxtral voice profiles on the Mistral API**. Field testing showed that OpenRouter exposes only **English and French** preset voices for Voxtral Mini TTS and **no cloning parameter**, while Mistral's model card supports nine languages via its own API — so the single provider ADR-004 bet on cannot cover both jobs through the gateway. Gemini 3.1 Flash TTS covers **70+ languages** (including, **unverified**, Greek), offers **200+ inline audio tags** for delivery steering, and carries **SynthID watermarking**. One voice is selected from its roster at the AI-366 bake-off and pinned as the house narrator across every language. **Deepgram keeps the roles ADR-004 assigned**: Nova supplies word timestamps for karaoke via the align step, and Aura presets remain the fallback voice bench for `it`, `es`, and `de`. `MISTRAL_API_KEY` exists for the single cloning capability and is used by no other code path. The costs are named honestly: the default narrator and cloned family voices come from **different engines**, a **preview model** sits in the default path, and **token-based audio pricing** is less legible than per-character — verified at T0.

---

## Problem Statement

### The Challenge

The narration plan needs **default voices for five languages** (`it`, `es`, `en`, `el`, `de`) and a **cloning path for family voices** — and no single provider offers both:

- **Voxtral via OpenRouter** (the ADR-004 narrator): field testing found only **English and French** preset voices exposed, and **no cloning parameter** on the endpoint. The current config papers over this by narrating Italian, Spanish, English, and Greek with an English voice (`en_paul_happy`).
- **Voxtral via the Mistral API**: the model card supports **nine languages** and voice profiles — but using it for defaults abandons the one-key/one-gateway property for the entire narration path.
- **Deepgram Aura**: strong presets, but **no cloning and no Greek**, and no path to Greek.
- **Gemini 3.1 Flash TTS on OpenRouter**: **70+ languages** including (**unverified**) Greek, **200+ inline audio tags** for delivery steering, **SynthID watermarking** — but no cloning, and it is a **preview** model.

### Why This Matters

- **The product promise is native voices**: an Italian story narrated by an English preset voice fails the bedtime-warmth bar before warmth is even judged.
- **Greek has no other realistic path**: neither Voxtral-on-OpenRouter nor Aura reaches it; without Gemini, the Greek shelf has no candidate at all.
- **Nonna Narrates depends on cloning existing somewhere**: ADR-006's consent-clip design assumes a voice-profile API; OpenRouter's Voxtral surface does not expose one.
- **Key discipline**: if a second vendor key must exist, its blast radius should be one feature, not the default path.

### Success Criteria

- [ ] All five launch languages have a plausible native default voice under the existing OpenRouter key
- [ ] One house narrator voice is pinned across every language (cross-language consistency is an explicit bake-off criterion)
- [ ] A working cloning path exists for ADR-006, reachable with a key scoped to that single capability
- [ ] Greek ships only if it passes the listening test — a named fallback exists, and deferral beats degradation
- [ ] Word timestamps (Deepgram Nova) and the fallback voice bench (Aura) are unchanged from ADR-004
- [ ] Token-based audio pricing is verified against the launch-library budget at T0

---

## Context

### Current State

- **Decided (ADR-004)**: Voxtral Mini TTS via OpenRouter narrates; Deepgram Nova reconstructs word timings; Deepgram Aura is the named fallback voice; ElevenLabs retired entirely.
- **Built (code)**: `narrate.py` runs Voxtral through OpenRouter's `/audio/speech` (AI-391); `config.py` maps per-language voices, with `it`/`es`/`en`/`el` all pointed at `en_paul_happy` because no native voices are exposed — the honest gap this ADR closes.
- **Field-tested (the trigger)**: OpenRouter's Voxtral roster is English and French presets only, with no cloning parameter. Mistral's own API is where the nine-language support and voice profiles live.
- **Proposed (ADR-006)**: Nonna Narrates assumes zero-shot cloning behind a voice-profile create/delete API — currently homeless under the gateway-only stack.

### Requirements

**Functional Requirements**:

- Native-sounding default narration for `it`, `es`, `en`, `de`, and (gated) `el`, for story pages and spoken prompts
- One narrator identity across languages — a single pinned voice, not a persona per language
- A voice-cloning path for family voices (ADR-006) and any bespoke narrator identity
- Delivery steering for bedtime (for example `[whispers]` on final pages) where the synthesis model supports it

**Non-Functional Requirements**:

- **Key discipline**: the default path stays on `OPENROUTER_API_KEY`; any second key is scoped to one capability
- **Honesty**: unverified properties (Greek quality, exact OpenRouter model id, token-audio pricing) are labeled, not assumed
- **Resilience to model churn**: synthesized audio is stored, so provider churn threatens regeneration, not playback; the model id lives in env
- **Transparency**: AI-generated audio provenance (SynthID) strengthens the AI-content posture

---

## Options Considered

### Option A: Voxtral presets via OpenRouter for defaults (status quo, ADR-004 as coded)

**Description**: Keep narrating everything through OpenRouter's Voxtral endpoint with its exposed preset voices.

**Pros**:

- Zero change; one key, one gateway, already implemented and cached
- Non-preview model in the default path

**Cons**:

- **The exposed roster is English and French only** — three of five launch languages have no native voice, and the config's `en_paul_happy`-for-everything mapping is the proof
- No cloning parameter, so ADR-006 has no path at all
- No delivery steering

**Risks**:

- Shipping Italian bedtime stories in an English preset voice fails the product's core warmth promise regardless of audio quality.

**Why rejected**: the exposed roster is English and French only.

---

### Option B: Gemini TTS defaults via OpenRouter; Voxtral cloning via Mistral API (Chosen)

**Description**: Default narration for all shelf content moves to Gemini 3.1 Flash TTS via OpenRouter; one voice from its roster is pinned as the house narrator across every language at the bake-off. Voice cloning runs exclusively through Voxtral voice profiles on the Mistral API, behind a `MISTRAL_API_KEY` used by no other code path. Deepgram keeps its ADR-004 roles unchanged.

**Pros**:

- **70+ languages** under the existing OpenRouter key — all five launch languages get a plausible native default, and Greek gains its only realistic path (**unverified**, gated by a listening test)
- **200+ inline audio tags** give bedtime its whisper — delivery steering no other candidate offers
- **SynthID watermarking** strengthens AI-content transparency
- Cloning lands where it actually exists (Mistral voice profiles), and the Mistral key's blast radius shrinks to one feature
- Deepgram's timing pass is narrator-independent (ADR-004), so the narrator swap does not touch karaoke

**Cons**:

- **A preview model sits in the default path** — model churn threatens regeneration (not playback, since synthesized audio is stored)
- **Token-based audio pricing** is less legible than per-character and must be verified at T0
- The default narrator and cloned family voices come from **different engines** — switching Nonna on changes vocal character entirely (which is the feature, but should be understood)
- Two TTS engines to hold in the head, though only one runs the default path

**Risks**:

- Gemini's Greek fails the listening test (mitigated: MAI-Voice-2 via OpenRouter is the named fallback; failing both, the Greek shelf launches later rather than worse)
- A forced model rename on OpenRouter (mitigated: model id in env; any rename lands as a SPEC-DEVIATION note)

**Estimated Effort**: a model-id and voice-map change in config plus the bake-off; the delivery-tag vocabulary is a bounded writer-prompt addition; the Mistral cloning transport lands with Nonna Narrates, not before.

---

### Option C: Deepgram Aura for defaults

**Description**: Promote the ADR-004 fallback bench to primary — Aura presets narrate the shelf.

**Pros**:

- Strong preset voices, including English–Spanish codeswitching voices for mixed-language households
- Per-character pricing, legible cost

**Cons**:

- **No Greek and no path to it** — the language that needs rescue stays stranded
- **No cloning**, so ADR-006 still needs a second provider anyway
- Non-English coverage beyond its bench languages remains unverified (ADR-004's own caveat)

**Why rejected as primary**: no Greek and no path to it. **Retained as the fallback voice bench** for `it`, `es`, and `de`, including the codeswitching voices.

---

### Option D: ElevenLabs return

**Description**: Un-retire ElevenLabs (ADR-004) for proven multilingual presets and cloning in one engine.

**Pros**:

- One engine for both defaults and cloning; proven warmth (the ADR-002/ADR-004 record)

**Cons**:

- Roughly 10× the cost class (the reason ADR-002 left)
- A third vendor key with a *wide* blast radius — the opposite of the scoping this ADR does
- Re-adding it requires walking back an explicit retirement for a need two cheaper providers now cover

**Why rejected**: not needed at current requirements. **It remains the documented un-retirement option** if cloning on Mistral disappoints — alongside **Cartesia (unevaluated)** and **local Chatterbox**.

---

## Comparison Matrix

| Criteria | Weight | A: Voxtral/OpenRouter | B: Gemini + Mistral cloning | C: Aura defaults | D: ElevenLabs return |
|----------|--------|----------------------|----------------------------|------------------|----------------------|
| **Five-language native defaults** | High | 1 | 5 | 3 | 5 |
| **Greek path** | High | 1 | 4 (unverified, gated) | 1 | 4 |
| **Cloning path (ADR-006)** | High | 1 | 5 | 1 | 5 |
| **Key discipline / default on one key** | High | 5 | 5 | 3 | 2 |
| **Delivery steering (bedtime)** | Medium | 1 | 5 | 2 | 3 |
| **Model stability (non-preview)** | Medium | 4 | 2 | 4 | 4 |
| **Price legibility** | Low | 4 | 2 | 5 | 3 |
| **Total (weighted feel)** | - | 14 | **26** | 15 | 23 |

**Scoring**: 1 = Poor, 2 = Below Average, 3 = Acceptable, 4 = Good, 5 = Excellent

Option D scores close behind B on capability — it loses on cost class, key blast radius, and the fact that it must *walk back* a deliberate retirement for a need B covers with providers already in (or adjacent to) the stack. A and C each fail a High-weight axis outright.

---

## Decision

### Chosen Option

**Selected**: Option B — defaults on Gemini 3.1 Flash TTS via OpenRouter; cloning scoped to Voxtral voice profiles on the Mistral API; Deepgram unchanged.

1. **Default narration for all shelf content moves to Gemini 3.1 Flash TTS via OpenRouter.** One voice is selected from its roster at the bake-off and pinned as the house narrator across every language; **cross-language voice consistency is an explicit bake-off criterion**.
2. **Voice cloning** (the family voices of ADR-006, and any bespoke narrator identity) **runs exclusively through Voxtral voice profiles on the Mistral API**. `MISTRAL_API_KEY` exists for this single capability and is used by no other code path.
3. **Deepgram keeps the roles ADR-004 assigned**: Nova supplies word timestamps for karaoke via the align step, and Aura presets remain the fallback voice bench for `it`, `es`, and `de`, including the English–Spanish codeswitching voices for mixed-language households.
4. **Greek gate**: `el` ships only if Gemini's Greek passes the listening test; **MAI-Voice-2 via OpenRouter** is the named fallback, and failing both, the Greek shelf launches later rather than worse.
5. **The writer prompt gains an optional delivery-tag vocabulary** (for example `[whispers]` on final pages), emitted only when the target synthesis model is Gemini, stripped otherwise.
6. **Preview risk is accepted with eyes open**: synthesized audio is stored, so model churn threatens regeneration, not playback. The model id lives in env, and any forced rename lands as a **SPEC-DEVIATION** note.

**Rationale**:
ADR-004 bet that one provider through one gateway could narrate five languages and eventually clone family voices. Field testing falsified both halves at once: the gateway exposes two languages and no cloning. Rather than abandon the gateway (defaults straight to Mistral) or the languages (stay on English presets), this ADR splits the jobs along the line where each provider is actually strong — breadth on Gemini through the existing key, cloning on the API where voice profiles actually exist, with the second key's blast radius shrunk to exactly that one feature. Deepgram's roles survive untouched because ADR-004 deliberately made timing narrator-independent — this swap is the first dividend of that decision.

**Key Factors**:

- All five languages get a plausible native default voice under the existing OpenRouter key
- Greek gains its only realistic path
- Delivery tags give bedtime its whisper
- SynthID strengthens AI-content transparency
- The Mistral key's blast radius shrinks to one feature

### Trade-offs Accepted

- **Two engines, two vocal characters**: the default narrator and cloned family voices come from different engines, so switching Nonna on changes vocal character entirely — which is the feature, but should be understood.
- **Token-based audio pricing is less legible than per-character** — verified at T0 before any library-scale generation.
- **A preview model sits in the default path** — accepted because stored audio keeps playing regardless; churn is a regeneration cost, not an outage.

---

## Consequences

### Positive Outcomes

- All five languages get a plausible native default voice under the existing OpenRouter key
- Greek gains its only realistic path
- Delivery tags give bedtime its whisper
- SynthID strengthens AI-content transparency
- The Mistral key's blast radius shrinks to one feature
- The `en_paul_happy`-for-four-languages config gap closes with a real answer instead of a placeholder

### Negative Outcomes

- The default narrator and cloned family voices come from different engines, so switching Nonna on changes vocal character entirely — the feature, but it should be understood
- Token-based audio pricing is less legible than per-character and gets verified at T0
- A preview model sits in the default path
- A second vendor key (`MISTRAL_API_KEY`) exists at all, however bounded

### Risks and Mitigation

**Risk 1**: Gemini's Greek fails the listening test

- **Probability**: Unverified — Greek is on the language list but unheard
- **Impact**: Medium — the Greek shelf (Tier 2)
- **Mitigation**: MAI-Voice-2 via OpenRouter is the named fallback (its carriage and quality likewise unverified until tested); failing both, the Greek shelf launches later rather than worse.

**Risk 2**: Model churn on a preview model (rename, deprecation, roster change)

- **Probability**: Real for any preview model
- **Impact**: Low for playback (synthesized audio is stored); medium for regeneration
- **Mitigation**: the model id lives in env; a forced rename lands as a SPEC-DEVIATION note; the Aura bench and the un-retirement options are the deeper fallbacks.

**Risk 3**: Token-based audio pricing surprises the budget

- **Probability**: Unverified
- **Impact**: Medium — library-scale generation cost
- **Mitigation**: verified at T0 against the launch-library budget before any library-scale generation; Aura (per-character) is the cost-legible bench.

**Risk 4**: Cloning on the Mistral API disappoints (no persistent profile API, or kitchen-table clip quality unusable)

- **Probability**: Unverified — exactly ADR-006's two pre-acceptance claims, now pointed at the Mistral API
- **Impact**: High for Nonna Narrates; zero for default narration
- **Mitigation**: ElevenLabs remains the documented un-retirement option (superseding ADR required), alongside Cartesia (unevaluated) and local Chatterbox.

**Risk 5**: One pinned voice sounds native in some languages and accented in others

- **Probability**: Unknown until the bake-off
- **Impact**: Medium — cross-language identity is the product's "one storyteller" promise
- **Mitigation**: cross-language voice consistency is an explicit bake-off criterion; the per-language Aura bench exists if a single pinned voice proves worse than a consistent bench.

---

## Implementation Plan

### Phase 0: T0 verification (before any code change)

- [ ] Confirm the exact Gemini 3.1 Flash TTS model id on OpenRouter's `/audio/speech` (the public `/models` listing does not carry speech models; the id is **unverified** as of 2026-07-12)
- [ ] Pull the voice roster and confirm inline audio-tag support through the gateway
- [ ] Verify token-based audio pricing against the launch-library budget
- [ ] Spot-check MAI-Voice-2 carriage on OpenRouter (the named Greek fallback)

### Phase 1: Re-scoped AI-366 bake-off

- [ ] Narrate the first Italian story on Gemini roster candidates and on the Deepgram Aura bench
- [ ] Judge bedtime warmth and **cross-language voice consistency** (explicit criterion); pin one house voice
- [ ] Run the Greek listening test; record a go / MAI-Voice-2 / defer verdict
- [ ] Run the Deepgram Nova timing pass over the winning narration; confirm alignment quality (unchanged mechanism from ADR-004)

### Phase 2: Provider switch (code)

- [ ] Point `narration_model` (env-driven) at the verified Gemini TTS id; collapse `narration_voices` to the single pinned house voice
- [ ] Confirm the content-addressed narration cache keys on the new model/voice (unchanged text re-synthesizes once, then never again)
- [ ] Update `system-overview.md` when the code lands (it describes as-built and stays on Voxtral until then)

### Phase 3: Delivery-tag vocabulary

- [ ] Add the optional delivery-tag vocabulary to the writer prompt (for example `[whispers]` on final pages)
- [ ] Emit tags only when the target synthesis model is Gemini; strip them otherwise

### Phase 4: Cloning transport (lands with Nonna Narrates, ADR-006)

- [ ] Add the Mistral API transport for Voxtral voice profiles, gated behind `MISTRAL_API_KEY` — used by no other code path
- [ ] Verify ADR-006's two claims against the Mistral API: persistent voice-profile create/delete, and kitchen-table clip quality

### Rollback Plan

**Trigger Conditions**:

- The pinned Gemini voice fails the warmth or consistency gate at AI-366
- Pricing verification at T0 breaks the budget
- Preview-model churn makes the default path unreliable

**Rollback Steps**:

1. Default narration falls back to the Deepgram Aura bench per language (`it`, `es`, `de`; English natively; Greek defers) — a config change in the same cost class
2. Stored audio keeps playing throughout — rollback is a regeneration decision, not an outage
3. If cloning on Mistral also disappoints, the documented un-retirement options are ElevenLabs (superseding ADR), Cartesia (unevaluated), and local Chatterbox
4. Record any forced model rename or provider reversal as a SPEC-DEVIATION note

---

## Validation

### Pre-Implementation Checklist

- [x] Every unverified claim is labeled (Gemini Greek quality, exact OpenRouter model id, token-audio pricing, MAI-Voice-2 carriage, Mistral profile API)
- [x] The Greek gate has a named fallback and an explicit defer-not-degrade posture
- [x] The second key's blast radius is bounded to one capability and documented as such
- [x] Deepgram's ADR-004 roles are explicitly unchanged
- [x] ADR-006's dependency is updated to point at the Mistral cloning path
- [x] Preview-model risk is stated with its actual blast radius (regeneration, not playback)

### Post-Implementation Validation

**Success Metrics**:

- AI-366 produces a recorded voice-pinning verdict with cross-language consistency judged explicitly
- All five languages (or four plus a recorded Greek deferral) narrate on the pinned house voice under `OPENROUTER_API_KEY`
- T0 pricing verification recorded against the launch-library budget
- Delivery tags appear in Gemini-targeted synthesis and never reach any other model
- `MISTRAL_API_KEY` is referenced by exactly one code path (the cloning transport)

**Review Date**: AI-366 (the re-scoped bake-off: Gemini roster vs the Aura bench, plus the Greek listening test)

---

## Token Economics and Voice Support

### Gemini TTS (default narration path)

**Pricing** (verified 2026-07-13):

| Component | Rate | Unit |
|-----------|------|------|
| Text input | $0.50 | per 1M tokens |
| Audio output | $10.00 | per 1M audio tokens |
| Audio token rate | 32 tokens | per 1 second of audio |

**Cost per story page** (typical 50-word page, ~250 characters, ~15 seconds of audio):

- Text input: ~60 tokens → ~$0.00003
- Audio output: 15s × 32 tokens/s = 480 tokens → ~$0.0048
- **Per page: ~$0.005**
- **Per 8-page story: ~$0.04**
- **Per 19-story launch library: ~$0.76**

**Voice roster**: 30+ preset voices (e.g. Kore, Puck, Charon, Aoede, Leda). One voice is pinned at the AI-366 bake-off as the house narrator across all languages.

**Language coverage**: 70+ languages including Italian, Spanish, English, German, and Greek (Greek quality unverified — gated by listening test).

**Delivery steering**: 200+ inline audio tags (e.g. `[whispers]`, `[cheerfully]`, `[calmly]`) for bedtime-appropriate narration.

**Watermarking**: SynthID-embedded audio for AI-content provenance.

**Model**: `google/gemini-3.1-flash-tts-preview` (preview model; model id on OpenRouter verified at T0).

### Voxtral TTS via Mistral API (cloning path only)

**Pricing** (verified 2026-07-13):

| Component | Rate | Unit |
|-----------|------|------|
| Text + audio | $0.016 | per 1,000 characters |
| Equivalent | $16.00 | per 1M characters |

**Cost per story page** (typical 250 characters):

- **Per page: ~$0.004**
- **Per 8-page story: ~$0.032**
- **Per 19-story launch library: ~$0.61** (if all stories used cloned voices — they don't; cloning is per-family, not per-library)

**Cloning requirements**:

- Minimum reference clip: 3 seconds (recommended 5–25 seconds)
- Voice profiles: create/manage reusable profiles via `client.audio.voices.create()`
- Zero-shot: no fine-tuning needed; the reference clip IS the voice
- Cross-lingual: a voice cloned from an Italian speaker can narrate in all 9 supported languages

**Language coverage**: 9 languages — English, French, German, Spanish, Dutch, Portuguese, Italian, Hindi, Arabic. **No Greek.**

**Preset voices**: 20 built-in presets available on both the API and open-weight release. Custom voice cloning requires the API (open weights are limited to presets).

**Latency**: ~70ms model latency for a 10-second input with 500 characters; real-time factor ≈9.7x.

**Model**: `voxtral-mini-tts-latest` via Mistral API (`/v1/audio/speech`).

### Deepgram (unchanged from ADR-004)

**Nova** (word timings): STT transcription pass over narrated audio — produces per-word start/end timestamps for karaoke. Cost is per-minute of audio transcribed.

**Aura** (fallback voice bench): per-character pricing, legible cost. Strong presets for `it`, `es`, `de`, including English–Spanish codeswitching voices. No Greek, no cloning.

### Cost Comparison Summary

| Provider | Per 8-page story | Per 19-story library | Pricing model |
|----------|------------------|---------------------|---------------|
| Gemini TTS (defaults) | ~$0.04 | ~$0.76 | Token-based (text + audio) |
| Voxtral/Mistral (cloning) | ~$0.032 | ~$0.61 | Per-character |
| Deepgram Aura (fallback) | ~$0.03 | ~$0.57 | Per-character |
| ElevenLabs (un-retirement) | ~$0.10–$0.44 | ~$1.90–$8.36 | Per-character, varies by plan |

**Budget posture**: The launch library (19 stories × 8 pages × ~250 chars) costs under $1 on Gemini for defaults. Cloning is per-family (not per-library), so Nonna Narrates adds negligible cost for the default shelf. The content-addressed cache means re-runs cost nothing — unchanged text re-synthesizes zero times.

---

## Related Decisions

**Amends**:

- [ADR-004](ADR-004-narration-deepgram-voxtral.md) — Narration: Voxtral TTS plus Deepgram. Deepgram's two roles carry forward unchanged; what this ADR changes is the default narrator (Voxtral → Gemini) and Voxtral's remit (default narration → cloning only, via the Mistral API).

**Depends on**:

- [ADR-006](ADR-006-family-voice-narration.md) — Nonna Narrates. Its cloning provider is rescoped here to Voxtral voice profiles on the Mistral API; its two pre-acceptance verification claims now apply to that API.

**Related To**:

- [ADR-002](ADR-002-narration-provider.md) — the original Voxtral-via-OpenRouter choice; the gateway-breadth assumption it rested on is what field testing falsified
- [ADR-001](ADR-001-technology-stack.md) — one gateway, per-step model choice: the *default* narration path still honors it; the Mistral key joins the pipeline-only Deepgram key as a second bounded, flagged exception

**Informs**:

- The AI-366 bake-off — re-scoped from Voxtral-vs-Aura to Gemini-roster-vs-Aura-bench, plus the Greek listening test
- The writer step — gains the Gemini-gated delivery-tag vocabulary
- The `settled-architecture` project skill — the narration row now reads "Gemini 3.1 Flash TTS via OpenRouter (defaults); Voxtral voice profiles via Mistral API (cloning only); Deepgram for word timings and the fallback bench"

---

## References

### Code References

- `src/config.py` — `narration_model` (env-driven model id) and `narration_voices` (collapses to one pinned voice); future `mistral_api_key` field scoped to cloning
- `src/pipeline/providers.py` — `NarrationClient` stays on OpenRouter `/audio/speech` for defaults; the Mistral cloning transport lands with ADR-006
- `src/pipeline/steps/narrate.py` — unchanged mechanics; content-addressed cache keys on model + voice
- `src/pipeline/steps/write.py` — the delivery-tag vocabulary, emitted only for Gemini targets
- `docs/architecture.md` — Technology Stack table, Narration / Audio section, Risks table

### External Resources

- [OpenRouter — Audio](https://openrouter.ai/docs) — `/audio/speech` carriage of Gemini 3.1 Flash TTS and MAI-Voice-2: **verified at T0** (speech models are absent from the public `/models` listing)
- [Gemini TTS](https://ai.google.dev/gemini-api/docs/speech-generation) — language coverage, audio tags, SynthID
- [Mistral — Voxtral](https://mistral.ai/) — voice profiles and nine-language support via the native API (the cloning home)
- [Deepgram — Aura TTS](https://developers.deepgram.com/) — the fallback bench, including English–Spanish codeswitching voices

---

## Metadata

**ADR Number**: 008
**Created**: 2026-07-11
**Last Updated**: 2026-07-12
**Version**: 1.0

**Authors**: Project Owner (decision draft), Claude (AI Assistant, template expansion)
**Reviewers**: Project Owner

**Tags**: narration, tts, gemini, voxtral, mistral-api, voice-cloning, deepgram, openrouter, greek-gate, delivery-tags, synthid, preview-risk, ai-366
