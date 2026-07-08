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
| Narration | **Voxtral Mini TTS via OpenRouter to start** (`mistralai/voxtral-mini-tts-2603`); ElevenLabs under evaluation — see **[ADR-002](../../../docs/adr/ADR-002-narration-provider.md)** | Browser TTS; adding a second narration key/vendor to *run* the pipeline (ElevenLabs is an optional fallback, not required) |
| Hosting | Render via Docker (`render.yaml`) | GitHub Pages, Netlify, Vercel |

**Why Web Audio is non-negotiable:** iOS makes media-element volume read-only, which kills the mandated gentle crossfades. Any library built on media elements (Howler's default html5 mode included) inherits that. Gain nodes work everywhere. This decision is unchanged.

**Narration note:** narration now runs on the same OpenRouter key as the rest of the pipeline (Voxtral to start). The trade-off is no word timestamps yet, so reading-mode karaoke timings are reconstructed later; warmth, cross-language voice consistency, and Greek support are validated at AI-366 before the library is built. ElevenLabs (native timestamps, one voice) is deferred, not discarded — it is the documented fallback. The full reasoning is in [ADR-002](../../../docs/adr/ADR-002-narration-provider.md).

## Red Flags — Stop and Read docs/architecture.md and docs/adr/

- "The monorepo's CLAUDE.md says Vue/React is the pattern here" — that file describes *sibling* projects. Cantastorie's own `docs/architecture.md` overrides it.
- "Library X handles iOS audio quirks for us" — the iOS constraint is why Web Audio was chosen; a wrapper doesn't lift it.
- "A bundler/framework would make this easier" — no-bundler is a settled decision, not an oversight.
- "Let's just use ElevenLabs for narration" — narration starts on Voxtral via OpenRouter by decision, not oversight; ElevenLabs is a documented fallback under evaluation, not the default (see ADR-002).
- Recommending `npm install <framework>` or a new provider SDK/key without citing `docs/architecture.md` or the relevant ADR.

Changing a settled decision is possible — but it happens by editing `docs/architecture.md` (and adding or superseding an ADR in `docs/adr/`) with the human's agreement first, never by installing around it.
