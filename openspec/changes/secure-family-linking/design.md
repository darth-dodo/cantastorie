# Design

## Context

`/parent/api/provision` (`src/api/routes/parent.py`) mints a token or adopts a client-supplied one and writes it to the Clerk user's `public_metadata` (`src/api/clerk.py`). The onboarding page reads any IndexedDB token on the device and posts it. The operator library template renders `story.family_token` and posts it back through `hx-vals`; `delete_published_story` takes `family_token` as a form field. Run records and staged stories live under `pending/{token}/` in the private bucket; `family_hash()` in `src/observability.py` is the logged identifier. One web instance (M11). See proposal.md for why.

## Goals / Non-Goals

**Goals:** an account can only belong to the family it started; provisioning can't be called at volume; the operator never sees a token.

**Non-Goals:** a second grown-up or device joining a family (with #179); token rotation (stays the manual ADR-003 procedure; no real families yet); account recovery.

## Decisions

- **Provision mints only.** `existing_token` is removed from the request model. A body that still carries it is refused with 400 rather than ignored, so an old cached page fails loudly instead of quietly starting a second family. Alternative: ignore the field, which hides a stale client.
- **Onboarding page:** stops reading IndexedDB before provisioning and simply provisions. After provisioning, the same-device bridge seeds this device as it does today.
- **Rate limit in process:** a small sliding-window counter keyed by Clerk user id, 5 attempts per 15 minutes. One web instance (M11) makes this correct; if the service ever scales out, the counter moves to R2 like the global run cap. Alternative: an R2 counter now, a round trip on every attempt for no present benefit.
- **Library:** the template shows `family_hash(token)`; actions post the story id only. The handler finds the story's family from the published manifests it already lists (`read_library`) and ignores any posted token.

## Risks / Trade-offs

- [A family can have only one grown-up account until #179] → accepted: no real families yet, and multi-device is optional for now.
- [In-memory limit resets on deploy] → a deploy grants at most a fresh window; acceptable at this scale.
- [A story id found in more than one family in the library lookup] → story ids carry a content-hash suffix; the lookup refuses an ambiguous id rather than guessing.

## Migration Plan

No data migration: no real families exist. Retire the three tokens found readable on 2026-10-03 by hand (delete their prefixes); `docs/setup.md` records how. Ship code, spec and tests in one PR, the first time this change is pushed. Rollback is a revert.
