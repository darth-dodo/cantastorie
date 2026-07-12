# Architecture Decision Records

This directory holds Cantastorie's **Architecture Decision Records (ADRs)** — short documents that capture a significant technical decision, the options weighed, and the reasoning behind the choice. The format follows the sibling project [habla-hermano](https://github.com/darth-dodo/habla-hermano/tree/main/docs/adr), adapted to Cantastorie.

An ADR is the durable answer to "why is it built this way?" Code shows *what* the system does; ADRs record *why* a settled decision was made, so the reasoning survives after the discussion is forgotten.

## When to Write an ADR

Write an ADR when a decision:

- Changes or establishes a **settled architectural decision** (a stack choice, a provider, a structural pattern) — see the `settled-architecture` project skill
- Is **costly or awkward to reverse** later
- Has **plausible alternatives** worth recording so the trade-off is legible
- Affects **more than one part** of the system, or a defining product constraint (audio, privacy, safety)

Small, local, easily-reversed choices do not need an ADR. When in doubt, prefer writing one — it is cheap now and valuable later.

`docs/architecture.md` and the ADRs are the **authority for settled decisions**. Where a design change is proposed, it is recorded here (or in `docs/architecture.md`) with the owner's agreement — not implemented around.

## Naming and Status

- **Filename**: `ADR-NNN-kebab-title.md`, with `NNN` zero-padded to three digits and incrementing (`ADR-001`, `ADR-002`, …)
- **Status**: one of `Proposed`, `Accepted`, `Superseded`, or `Deprecated`. A superseded ADR stays in place and links to the ADR that replaced it.
- **Structure**: each ADR follows the full template — Summary, Problem Statement, Context, Options Considered (with pros/cons/risks per option), Comparison Matrix, Decision, Consequences, Implementation Plan, Validation, Related Decisions, References, and Metadata.

## Index

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [ADR-001](ADR-001-technology-stack.md) | Foundational Technology Stack | Accepted | 2026-07-07 |
| [ADR-002](ADR-002-narration-provider.md) | Narration Provider — Voxtral via OpenRouter to Start | Superseded by ADR-004 | 2026-07-07 |
| [ADR-003](ADR-003-parent-authentication-clerk.md) | Parent Authentication via Clerk | Accepted | 2026-07-11 |
| [ADR-004](ADR-004-narration-deepgram-voxtral.md) | Narration — Voxtral TTS plus Deepgram, ElevenLabs Retired | Accepted | 2026-07-11 |
| [ADR-005](ADR-005-family-voice-narration.md) | Nonna Narrates (family voice narration) | Proposed | 2026-07-11 |
| [ADR-004](ADR-004-workshop-area.md) | The Workshop Area — In-App Authoring Surface with In-Process Pipeline Runs | Accepted | 2026-07-11 |
| [ADR-006](ADR-006-langsmith-observability.md) | LangSmith App-Wide Observability | Accepted | 2026-07-12 |

## Related Documentation

| Doc | Content |
|-----|---------|
| [Architecture](../architecture.md) | The settled design: stack choices and their rationale |
| [System Overview](../system-overview.md) | The code as built: module map, state machines, and seams |
| [Product Specification](../product.md) | Vision, behaviors, content rules, decision log |
| [Setup & Deploy](../setup.md) | R2 bucket, CORS, and the Render blueprint |
