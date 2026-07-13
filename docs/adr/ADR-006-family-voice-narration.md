# ADR-006: Nonna Narrates (family voice narration)

**Date**: 2026-07-11
**Status**: Proposed
**Context**: Letting a family member's cloned voice narrate their family's stories, with the strictest data handling in the codebase
**Decider(s)**: Project Owner
**Depends on**: [ADR-002](ADR-002-narration-provider.md) / [ADR-004](ADR-004-narration-deepgram-voxtral.md) (narration provider and voice cloning), as amended by [ADR-008](ADR-008-narration-gemini-defaults-mistral-cloning.md) (cloning runs on Voxtral voice profiles via the Mistral API); a multi-tenant architecture ADR (forthcoming — per-family voice profiles assume the token-keyed per-family shelves described in the product spec)

---

## Context

The narration pipeline already supports zero-shot voice cloning from a short reference clip. Pointing that same machinery at the parent area lets a family member (a grandparent, a parent) become the narrator of their family's stories, in their language, across distance. This introduces the one data class the app has never held: a real person's voice, which sits in biometric-adjacent territory and deserves the strictest handling in the codebase.

## Decision

Family voice narration ships behind the parent area with these rules, all of them load-bearing:

1. **The consent sentence IS the reference clip.** The speaker records one short take: "I agree to lend my voice to our stories" in their own language. That single clip both proves consent and feeds the clone. There is no separate upload path.
2. **Consent is verified by machine, not honor.** Before any profile is created, the clip is transcribed and the consent sentence must be present. A clip without it is rejected and discarded.
3. **The clip is never stored.** It streams through the backend in memory, is sent once to create the provider voice profile, and is gone. Not on R2, not on disk, not in logs, not in exports.
4. **Only metadata persists**: timestamp, voice id, family id, and a hash of the consent sentence. Never audio.
5. **The voice profile lives at the provider** under the platform's key and is referenced in the app by id only. The app stores a pointer, never a voice.
6. **Revocation is one tap and total**: provider profile deleted via API, every audio file generated in that voice purged from storage, manifests rewritten to the default narrator, and the family's asset prefix rotated. Revocation doubles as the erasure obligation (Source: GDPR Art. 17).
7. **Voice-create and voice-delete are the most privileged mutations in the app**: parent session required, rate limited, request bodies excluded from logging on these routes.
8. **Cloned narration is labeled in the parent area**: which stories use which family voice, created when, revocable where.

### Provider note (ties to ADR-004)

Under the settled narration stack, the cloning provider is **Voxtral via OpenRouter** (zero-shot cloning, carried forward from ADR-002 through ADR-004), and the consent-transcription check in rule 2 is exactly the **Deepgram STT pass** ADR-004 introduces for word timings — the feature adds no new provider. Two claims must be **verified before acceptance**: that the provider exposes a persistent voice-profile create/delete API (rules 5 and 6 depend on it), and that zero-shot cloning quality on a short kitchen-table clip is usable. If the provider offers only per-request cloning rather than stored profiles, rule 5 inverts into a harder problem (a reference clip would need to exist somewhere), and this design must be revisited rather than bent.

> **Update (2026-07-12, [ADR-008](ADR-008-narration-gemini-defaults-mistral-cloning.md))**: field testing found OpenRouter's Voxtral endpoint exposes no cloning parameter, so cloning runs on **Voxtral voice profiles via the Mistral API** — `MISTRAL_API_KEY` exists for this single capability and is used by no other code path. This does add one provider key, bounded to this feature. The two verification claims above now apply to the Mistral API's voice-profile surface. Default narration meanwhile moves to Gemini TTS, so switching Nonna on changes vocal character entirely — which is the feature, but should be understood.

## Consequences

**Positive**: the emotionally strongest feature the product can offer, built almost entirely from existing pipeline parts; the revocation flow gives the platform its erasure machinery for free; the consent-in-the-clip design removes the abuse path of cloning a non-consenting third party.

**Negative**: a dependency on the provider's voice-profile API for create and delete; one more privileged surface to defend; support burden when a grandparent's kitchen recording is too noisy to clone well.

## Alternatives considered

- **Storing reference clips for re-cloning on provider change**: rejected; keeping voices is a liability, and a new 5-second recording is cheaper than defending a biometric archive.
- **Fully local cloning (MIT-licensed Chatterbox at authoring time)**: documented as the maximalist privacy variant; viable for self-hosters, not for the hosted platform's latency and ops budget.

---

## Metadata

**ADR Number**: 006 (renumbered 2026-07-12; originally filed as ADR-005 while a second, colliding ADR-004 existed)
**Created**: 2026-07-11
**Last Updated**: 2026-07-12 (renumbered; provider note updated per ADR-008)
**Version**: 1.0

**Authors**: Project Owner (draft), Claude (AI Assistant, formatting and provider note)
**Reviewers**: Project Owner

**Tags**: narration, voice-cloning, family-voice, consent, privacy, gdpr, revocation, voxtral, deepgram, proposed
