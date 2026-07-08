# ADR-001: Foundational Technology Stack

**Date**: 2026-07-07
**Status**: Accepted
**Context**: Phase 1 foundation — choosing the stack for the player, the parent area, and the authoring pipeline
**Decider(s)**: Project Owner

---

## Summary

Cantastorie is built as **one FastAPI application** that serves a vanilla-JavaScript child player and a server-rendered parent area, alongside a **plain-Python authoring pipeline** in the same repository. The player uses **ES modules and the Web Audio API** with no bundler and no TypeScript. The pipeline is **plain Python with Pydantic AI over OpenRouter**, using the filesystem as its checkpoint store rather than a graph framework. Story assets are served **bucket-direct from Cloudflare R2**; child state lives only in **IndexedDB**; the app is hosted on **Render** via Docker. This mirrors the sibling project habla-hermano's proven shape, deliberately minimizing moving parts for a bedtime app whose defining constraints are iOS audio behavior and a hard privacy posture.

---

## Problem Statement

### The Challenge

Cantastorie must deliver a voice-first, full-screen storytelling experience to pre-readers on phones and tablets — with gentle audio crossfades, hands-free page turns, and no required text — while guaranteeing that nothing about the child ever leaves the browser. It must also generate the story library (text, narration, watercolor images, glosses) through a repeatable, reviewable pipeline. The foundational question is what stack serves both faces without introducing complexity that fights the product's constraints.

### Why This Matters

- **iOS audio**: media-element volume is read-only on iOS, which would break the mandated gentle crossfades. The audio layer choice is not cosmetic — it is a correctness constraint.
- **Privacy posture**: the product forbids cookies, accounts, and server-side child state. The stack must make "nothing leaves the browser" the default, not a feature to bolt on.
- **Operational simplicity**: this is a small project maintained by one owner. Fewer frameworks, keys, and services means less to run, secure, and debug.
- **Consistency with a proven sibling**: habla-hermano already validated FastAPI + vanilla ES modules + Render for a similar audience. Reusing that shape lowers risk and reuses working patterns (the `fsm.js` state machine, the `render.yaml` deploy).

### Success Criteria

- [ ] A single deployment serves both the child player and the parent area
- [ ] Audio crossfades and exact-position resume work on iOS Safari
- [ ] Story-time traffic is bucket-direct asset fetches only — no cookies, no server calls carrying child data
- [ ] The authoring pipeline is crash-safe and resumable without a graph runtime
- [ ] The player ships with no build step beyond a Tailwind compile for the parent UI
- [ ] Provider API keys never reach the browser

---

## Context

### Current State

Cantastorie is greenfield. There is no legacy stack to migrate; the decision is what to adopt from the start. The sibling project habla-hermano is the reference implementation: FastAPI backend, server-rendered Jinja2 + HTMX, vanilla ES modules with a generic finite state machine, Render deployment. Cantastorie borrows that shape and adapts it — the child player is audio-driven rather than chat-driven, and an authoring pipeline is added that habla-hermano does not have.

### Requirements

**Functional Requirements**:

- Full-screen, audio-driven player with crossfades, auto page turns, and picture-tap choices
- Server-rendered parent area (gate, settings, export/import) behind a parent gate
- An authoring pipeline that generates and validates story packs for human approval
- Per-language shelves assembled from static manifests

**Non-Functional Requirements**:

- **Privacy**: no cookies, no accounts, no server-side child state; provider keys server-side only
- **iOS correctness**: gentle crossfades must work where media-element volume is read-only
- **Resilience**: mid-story network failures must be avoidable via prefetch; the pipeline must resume after a crash without re-buying completed steps
- **Simplicity**: minimize frameworks, build steps, and external services

---

## Options Considered

### Option A: Hermano-derived minimalist stack (Chosen)

**Description**: One FastAPI app serving a vanilla-ES-module player (Web Audio, no bundler, no TypeScript) and a Jinja2 + HTMX parent area; a plain-Python pipeline with Pydantic AI over OpenRouter using the filesystem as checkpoint store; bucket-direct R2 for assets; IndexedDB for child state; Render via Docker for hosting.

**Pros**:

- **iOS-correct audio**: Web Audio decoded buffers + gain nodes give crossfades and exact-position resume everywhere, including where media-element volume is read-only.
- **Privacy by default**: bucket-direct asset fetches plus no cookies means there is no child-data path to accidentally introduce.
- **Minimal surface**: no SPA framework, no bundler, no TypeScript toolchain; the only build step is a Tailwind compile for the parent UI.
- **Crash-safe pipeline for free**: persisting each step's artifact to the story's working folder *is* checkpointing, with the filesystem as the state store — a failure at `illustrate` never re-buys `narrate`.
- **Proven precedent**: reuses habla-hermano's `fsm.js`, `render.yaml`, and testing patterns (providers mocked in unit tests, real-browser child flows in Playwright).
- **One gateway for models**: OpenRouter gives per-step model choice (including a different family for the safety judge) with a single key and a string swap to re-benchmark.

**Cons**:

- Vanilla ES modules mean hand-written DOM and state wiring where a framework would provide structure.
- No bundler means no tree-shaking or module graph optimization (acceptable — the player is small).
- Plain-Python orchestration means retry/branch logic is hand-written rather than declared in a graph.

**Risks**:

- **Player complexity growth**: if the player grows well beyond its current scope, the absence of a framework could bite. Mitigate by keeping the FSM-driven module boundaries clean and small.
- **iOS regressions**: browser audio behavior shifts between OS versions. Mitigate with Playwright coverage of the two-tap start and playback loop.

**Estimated Effort**: Baseline (this is the foundation; effort is the project itself)

---

### Option B: SPA framework + bundler + graph pipeline framework

**Description**: Build the player as a React/Vue/Svelte SPA with a bundler (Vite) and TypeScript; orchestrate the pipeline with a graph framework such as LangGraph.

**Pros**:

- Framework structure for component state and rendering
- TypeScript type safety across the player
- Declarative graph orchestration with built-in checkpointing abstractions

**Cons**:

- **Audio constraint unchanged**: a framework does not lift the iOS media-element limitation — Web Audio is still required underneath, so the framework adds a layer without solving the core problem.
- **Bundler/toolchain overhead**: build config, dependency churn, and a compile step for a player that is intentionally small.
- **Graph framework is ceremony here**: the pipeline is a linear batch job with one bounded retry loop; a graph runtime adds structure around what is honestly a `for` loop, whereas LangGraph earns its keep in habla-hermano because that graph runs per chat message with conversational state and token streaming — a different problem shape.
- **Larger dependency and attack surface** for a privacy-sensitive app.

**Risks**:

- Framework and bundler version churn becomes ongoing maintenance for a one-owner project.

**Estimated Effort**: Higher — additional toolchain setup and framework learning curve with no offsetting benefit for this problem.

---

### Option C: Static-only site or third-party story platform

**Description**: Ship the player as a fully static site (no app server), or build on a third-party interactive-story or e-learning platform.

**Pros**:

- Minimal or no hosting cost for a static site
- A platform could provide authoring and delivery out of the box

**Cons**:

- **No parent area**: the gate, settings, and export/import need server-rendered pages and, in Phase 2, pipeline routes — a static-only site cannot host them.
- **Loss of control over privacy**: a third-party platform introduces its own cookies, analytics, and data handling, directly violating the non-negotiable privacy posture.
- **No home for the pipeline**: the authoring pipeline needs to live somewhere version-controlled and reviewable; a platform hides it.

**Risks**:

- Vendor lock-in and opaque data handling — unacceptable for a children's product.

**Estimated Effort**: Lower upfront, but blocks Phase 2 and forfeits privacy control.

---

## Comparison Matrix

| Criteria | Weight | A: Hermano-derived | B: SPA + graph | C: Static/platform |
|----------|--------|--------------------|----------------|--------------------|
| **iOS audio correctness** | High | 5 | 4 | 3 |
| **Privacy control** | High | 5 | 4 | 1 |
| **Operational simplicity** | High | 5 | 2 | 4 |
| **Pipeline fit** | High | 5 | 3 | 2 |
| **Phase 2 parent/factory support** | High | 5 | 5 | 1 |
| **Proven precedent** | Medium | 5 | 3 | 2 |
| **Player structure/DX** | Medium | 3 | 5 | 3 |
| **Dependency surface** | Medium | 5 | 2 | 3 |
| **Total Score** | - | **38** | 28 | 19 |

**Scoring**: 1 = Poor, 2 = Below Average, 3 = Acceptable, 4 = Good, 5 = Excellent

---

## Decision

### Chosen Option

**Selected**: Option A — the hermano-derived minimalist stack

**Rationale**:
The product's two defining constraints — iOS-correct gentle audio and a hard privacy posture — are best served by Web Audio and bucket-direct delivery, neither of which a framework or platform improves upon. The authoring pipeline is a linear batch job whose checkpoint needs are met by writing artifacts to disk, making a graph runtime unnecessary ceremony. Reusing habla-hermano's proven shape (FastAPI, vanilla ES modules, `fsm.js`, Render) lowers risk and reuses working patterns. The result is a small, legible surface that a single owner can run and secure.

**Key Factors**:

- Web Audio is required by the iOS constraint regardless of any higher-layer framework
- Bucket-direct R2 makes "nothing leaves the browser" the default delivery path
- The filesystem-as-checkpoint pipeline is crash-safe without a graph framework
- One FastAPI app serves both faces and hosts the Phase 2 pipeline routes
- OpenRouter provides per-step model choice (including cross-family safety judging) behind one key

### Trade-offs Accepted

- Hand-written player state and DOM wiring instead of framework scaffolding (mitigated by clean FSM-driven module boundaries)
- No bundler optimizations (acceptable for a small player)
- Hand-written retry/branch orchestration in the pipeline (acceptable for a linear job with one bounded loop)

---

## Consequences

### Positive Outcomes

**Immediate Benefits**:

- The player ships with no build step beyond a Tailwind compile for the parent UI
- Crossfades and exact-position resume work on iOS
- The pipeline resumes after a crash by finding artifacts already on disk
- Provider keys live only in the pipeline environment — the running site needs no secrets

**Long-term Benefits**:

- A small dependency surface is cheaper to maintain and audit for a privacy-sensitive product
- Per-step model choice via OpenRouter means models can be re-benchmarked with a string swap
- The Phase 2 factory routes reuse the same step functions in front of FastAPI

### Negative Outcomes

**Immediate Costs**:

- More boilerplate in the player than a framework would require
- Manual discipline needed to keep module boundaries clean without a framework enforcing them

**Trade-offs**:

- No compile-time type checking in the player (Python side keeps strict mypy)

### Risks and Mitigation

**Risk 1**: Player state complexity outgrows the vanilla approach

- **Probability**: Low-Medium
- **Impact**: Medium — harder-to-follow state wiring
- **Mitigation**: Keep the finite state machine central; each module is a factory function with injected dependencies, keeping units small and testable in Vitest + jsdom.

**Risk 2**: iOS audio behavior changes across OS versions

- **Probability**: Low
- **Impact**: Medium — crossfades or resume regress
- **Mitigation**: Playwright coverage of the two-tap start and playback loop; the audio engine owns the single `AudioContext` so fixes land in one place.

**Risk 3**: A future need genuinely calls for a graph framework or SPA

- **Probability**: Low
- **Impact**: Low — the decision is revisitable per the change process below
- **Mitigation**: This ADR and `docs/architecture.md` are the record; changing a settled decision happens by editing them with the owner's agreement, not by installing around them.

---

## Implementation Plan

### Phase 1: Player and pipeline foundation

- [ ] FastAPI app factory, static mount, player route, `/health`
- [ ] Vanilla ES-module player around `fsm.js`, with the Web Audio engine owning the single `AudioContext`
- [ ] Plain-Python pipeline: typed step functions, content-addressed artifact cache, Pydantic AI over OpenRouter
- [ ] Render deployment via `render.yaml`; Cloudflare R2 bucket for published assets

### Phase 2: Parent area and factory routes

- [ ] Server-rendered parent area (gate, settings, export/import)
- [ ] Pipeline step functions exposed behind FastAPI routes (pack requests, review queue)

### Rollback Plan

**Trigger Conditions**:

- A defining constraint (iOS audio, privacy) is discovered to be unmeetable with the chosen stack

**Rollback Steps**:

1. Record the discovery and the alternative in a superseding ADR
2. Migrate the affected layer only (the FSM-driven module boundaries localize a player rewrite; the pipeline's step functions localize an orchestration change)

---

## Validation

### Pre-Implementation Checklist

- [x] Decision addresses the defining constraints (iOS audio, privacy, pipeline resumability)
- [x] Success criteria are measurable
- [x] Risks are identified and mitigated
- [x] The choice reuses a proven sibling precedent

### Post-Implementation Validation

**Success Metrics**:

- Two-tap-to-narration and full playback loop pass in Playwright on a real browser
- No cookies set and assets load from the R2 domain (verified on cellular per `docs/setup.md`)
- Pipeline re-run of an unchanged story costs zero API calls (cache-hit assertions in pytest)

**Review Date**: Revisit only if a defining constraint proves unmeetable

---

## Related Decisions

**Related To**:

- [ADR-002](ADR-002-narration-provider.md) — Narration provider (the pipeline's TTS step runs through the OpenRouter gateway chosen here)

**Informs**:

- All future stack proposals — see the `settled-architecture` project skill, which points here and to `docs/architecture.md` as the authority

---

## References

### Code References

- `src/api/main.py` — FastAPI app factory
- `src/static/js/` — vanilla ES-module player around `fsm.js` and the Web Audio engine
- `src/pipeline/` — plain-Python step functions, content-addressed cache, provider layer over OpenRouter
- `src/config.py` — shared settings (R2 bucket, provider keys, per-step model choices); refuses config where the safety judge and writer share a model family
- `render.yaml` — Render deployment blueprint
- `deploy/r2-cors.json` — R2 CORS policy scoped to the player origin

### External Resources

- [FastAPI](https://fastapi.tiangolo.com) — async web framework
- [Web Audio API (MDN)](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API) — decoded-buffer playback and gain-node crossfades
- [Cloudflare R2](https://developers.cloudflare.com/r2/) — zero-egress object storage
- [OpenRouter](https://openrouter.ai/docs) — single-gateway access to multiple model providers
- [Render Blueprints](https://render.com/docs/blueprint-spec) — `render.yaml` deployment

---

## Metadata

**ADR Number**: 001
**Created**: 2026-07-07
**Last Updated**: 2026-07-07
**Version**: 1.0

**Authors**: Claude (AI Assistant)
**Reviewers**: Project Owner

**Tags**: architecture, fastapi, web-audio, vanilla-js, pydantic-ai, openrouter, cloudflare-r2, render, indexeddb, foundational-stack
