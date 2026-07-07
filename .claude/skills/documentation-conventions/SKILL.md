---
name: documentation-conventions
description: Use when writing or restructuring cantastorie docs (architecture.md, product.md, README), adding or updating an ADR in docs/adr/, or recording a settled-decision change — so doc structure, ADR format, and authority rules stay consistent.
---

# Documentation Conventions

Cantastorie's documentation follows the sibling project **habla-hermano** (`../habla-hermano/docs/`) as its pattern source. When writing or restructuring docs, mirror those conventions. Voice throughout: precise, professional, no marketing superlatives. Convert relative dates to absolute.

## Where Docs Live

| Location | Holds |
|----------|-------|
| `docs/product.md` | Product spec: vision, behaviors (with **bold names**), content rules, decision log |
| `docs/architecture.md` | Settled technical design: stack choices and their rationale |
| `docs/system-overview.md` | The code **as built**: module map, state machines, seams |
| `docs/setup.md` | Deploy: R2 bucket, CORS, Render blueprint |
| `docs/adr/` | Architecture Decision Records — one file per significant decision |
| `docs/plans/` | Slice-by-slice implementation plans (created per slice) |
| `docs/design/` | Design documents for specific features |
| `.claude/skills/` | Shared project skills (tracked; personal `.claude` state stays local) |

**Authority for settled decisions**: `docs/architecture.md` **and** the ADRs in `docs/adr/`. A settled decision changes by editing those with the owner's agreement — never by working around them in code. `system-overview.md` describes the code as-built; where it and the code disagree, fix the doc.

## architecture.md Conventions

Follow habla-hermano's `architecture.md` structure:

- `# <Project> — Technical Architecture`, then a `> ` one-line tagline
- A **Table of Contents** (keep it in sync with every heading change)
- `## System Overview` — a mermaid diagram + a **Key design decisions** bullet list
- `## Technology Stack` — a table with **Component / Technology / Why**
- `## Project Structure` — an annotated file tree
- Domain sections (pipeline, storage, player, parent area, privacy…)
- A dedicated **`## Narration / Audio`** section — cantastorie's analog of hermano's `## Voice Architecture` (provider, why precomputed/bucket-direct, provider specifics, trade-offs)
- End with `## Testing`, `## Risks and Open Questions`, `## Related Documentation`

## product.md Conventions

Follow habla-hermano's `product.md` structure:

- `# <Project> Product Specification`, `> ` tagline, TOC
- `## Vision`, the storyteller/identity section, `## What We're Building (Current State)` (a status table)
- Feature sections, `## Who It's For`, `## UX Principles`
- `## Technical Architecture` — brief, links to `architecture.md`
- `## Roadmap` (phases; `### Future Ideas`), `## Success Metrics`, `## What We're NOT Building`

**Behaviors carry bold names** (e.g. **Parent gate**, **Resume offer**). Tests, tasks, and design docs reference those names — never rename or invent them casually; changing a bold name is a code-affecting change.

## ADR Format, Naming, and When to Write One

ADRs live in `docs/adr/`, named **`ADR-NNN-kebab-title.md`** (zero-padded 3 digits, incrementing). Add each new ADR to the index table in `docs/adr/README.md`.

**Write an ADR when a decision**: changes or establishes a **settled architectural decision** (stack, provider, structural pattern); is costly to reverse; has plausible alternatives worth recording; or affects multiple parts of the system or a defining constraint (audio, privacy, safety). Small, local, easily-reversed choices do not need one.

**Status**: `Proposed` → `Accepted` → `Superseded`/`Deprecated`. A superseded ADR stays and links to its replacement.

**Full section set** (match habla-hermano's `ADR-008` / `ADR-010` depth):

1. `# ADR-NNN: <Title>`
2. Metadata block: **Date**, **Status**, **Context**, **Decider(s)**
3. `## Summary`
4. `## Problem Statement` — `### The Challenge`, `### Why This Matters`, `### Success Criteria` (checkboxes)
5. `## Context` — `### Current State`, `### Requirements`
6. `## Options Considered` — `### Option A/B/C…`, each with Description, **Pros**, **Cons**, **Risks**, **Estimated Effort**
7. `## Comparison Matrix` — weighted scoring table
8. `## Decision` — `### Chosen Option`, **Rationale**, **Key Factors**, **Trade-offs Accepted**
9. `## Consequences` — `### Positive Outcomes`, `### Negative Outcomes`, `### Risks and Mitigation`
10. `## Implementation Plan` — phases + a Rollback plan
11. `## Validation`
12. `## Related Decisions`
13. `## References` — Code + External
14. `## Metadata` — ADR Number, Created, Tags

## Rules of Thumb

- **Keep TOCs and cross-links valid.** After any heading change, update the TOC and any anchors that point to it.
- **Don't invent facts.** Trace every claim to product.md, architecture.md, an ADR, the code, or hermano's pattern. Where something is unproven, say **"unverified"** — don't assert.
- **Cross-link, don't duplicate.** `system-overview.md` is the as-built map; reference it rather than restating it.
- **Absolute dates.** Convert "recently"/"last week" to a date.
- **Professional voice.** No superlatives ("blazingly fast", "100% secure"); state trade-offs honestly.
