---
name: settled-architecture
description: Use when choosing or proposing frameworks, libraries, build tooling, audio playback, narration/TTS, state management, hosting, deployment, or repo structure in cantastorie — before recommending any new dependency or structural change.
---

# Settled Architecture

## Overview

Cantastorie's stack is **settled and documented in `docs/architecture.md`**, with significant decisions recorded as **Architecture Decision Records in `docs/adr/`**. Those are the authority for every technology choice. Read them before proposing any dependency, tool, or structural change — proposals that contradict them are wrong even when they'd be good defaults elsewhere.

## The Settled Decisions

| Area | Settled | Explicitly NOT |
|------|---------|----------------|
| App shape | One FastAPI app: player page + parent area | Separate frontend app, static-only site |
| Player UI | Vanilla ES modules, FSM-managed | React, Vue, Svelte, any framework |
| Build tooling | None (Tailwind CLI only, for parent UI) | Vite, webpack, esbuild, TypeScript |
| Audio playback | Web Audio API: decoded buffers + gain nodes | `<audio>` tags, Howler.js, media elements |
| Story assets | Bucket-direct from Cloudflare R2 | Routing story bytes through the app server |
| Child state | IndexedDB only | Cookies, accounts, server-side state |
| Pipeline | Plain Python + Pydantic AI, filesystem checkpoints | LangGraph or any graph framework |
| LLM/image access | OpenRouter (per-step model choice) | Direct provider SDKs |
| Narration | **Gemini 3.1 Flash TTS via OpenRouter** (defaults — one pinned house voice); **Voxtral voice profiles via the Mistral API** (voice cloning only, `MISTRAL_API_KEY` scoped to that one capability); **Deepgram** for word timings (STT pass) and the fallback voice bench (Aura, `it`/`es`/`de`) — see **[ADR-004](../../../docs/adr/ADR-004-narration-deepgram-voxtral.md)** as amended by **[ADR-008](../../../docs/adr/ADR-008-narration-gemini-defaults-mistral-cloning.md)** | Browser TTS; ElevenLabs (retired — ADR-004); Voxtral-via-OpenRouter for defaults (its exposed roster is English/French only — ADR-008); any vendor key beyond the two flagged pipeline-only exceptions (Deepgram, Mistral) |
| Hosting | Render via Docker (`render.yaml`) | GitHub Pages, Netlify, Vercel |
| Parent authentication | Clerk (parent area only — [ADR-003](../../../docs/adr/ADR-003-parent-authentication-clerk.md), Accepted); JWT verified via JWKS (PyJWT, no vendor SDK); one parent account = one family; token in Clerk `public_metadata` | Clerk script or cookie logic on any child path; self-hosted auth; Supabase (platform coupling) |

**Why Web Audio is non-negotiable:** iOS makes media-element volume read-only, which kills the mandated gentle crossfades. Any library built on media elements (Howler's default html5 mode included) inherits that. Gain nodes work everywhere. This decision is unchanged.

**Narration note:** default narration runs on the same OpenRouter key as the rest of the pipeline (**Gemini 3.1 Flash TTS**, one house voice pinned across all languages at the AI-366 bake-off; the exact OpenRouter model id is verified at T0 and lives in env). **Voice cloning** (Nonna Narrates, ADR-006) runs exclusively on **Voxtral voice profiles via the Mistral API** — `MISTRAL_API_KEY` exists for that single capability and no other code path. The narrator emits no word timestamps, so reading-mode karaoke timings are reconstructed at slice 6 via a **Deepgram STT transcription pass** over the narrated audio; Deepgram Aura presets are the fallback voice bench for `it`/`es`/`de`. Warmth, cross-language voice consistency, Greek support (gated on a listening test; MAI-Voice-2 is the named fallback, else the Greek shelf defers), and timing-alignment quality are validated at AI-366 (Gemini roster vs the Aura bench) before the library is built. **ElevenLabs is retired entirely** — no transport, no key, no fallback role; it is the documented un-retirement option only if Mistral cloning disappoints, and re-adding it takes a superseding ADR. The full reasoning is in [ADR-004](../../../docs/adr/ADR-004-narration-deepgram-voxtral.md) and [ADR-008](../../../docs/adr/ADR-008-narration-gemini-defaults-mistral-cloning.md).

## Red Flags — Stop and Read docs/architecture.md and docs/adr/

- "The monorepo's CLAUDE.md says Vue/React is the pattern here" — that file describes *sibling* projects. Cantastorie's own `docs/architecture.md` overrides it.
- "Library X handles iOS audio quirks for us" — the iOS constraint is why Web Audio was chosen; a wrapper doesn't lift it.
- "A bundler/framework would make this easier" — no-bundler is a settled decision, not an oversight.
- "Let's just use ElevenLabs for narration" — ElevenLabs is retired by decision, not oversight (ADR-004); the default narrator is Gemini TTS via OpenRouter (ADR-008), the fallback bench is Deepgram Aura, and re-adding ElevenLabs requires a superseding ADR.
- "Voxtral can narrate the defaults" — its OpenRouter roster is English/French only with no cloning parameter (ADR-008); Voxtral's remit is voice cloning via the Mistral API, nothing else.
- "Just add the Mistral key to another step" — `MISTRAL_API_KEY` is scoped to voice cloning by decision (ADR-008); widening its blast radius takes a superseding ADR.
- Recommending `npm install <framework>` or a new provider SDK/key without citing `docs/architecture.md` or the relevant ADR.
- "Let's add Clerk script or cookie logic to the player page" — Clerk is parent-area-only (ADR-003); the child player loads no Clerk script, sets no cookies, and carries no credentials on story-time R2 fetches. An automated guard test enforces this.

Changing a settled decision is possible — but it happens by editing `docs/architecture.md` (and adding or superseding an ADR in `docs/adr/`) with the human's agreement first, never by installing around it.
