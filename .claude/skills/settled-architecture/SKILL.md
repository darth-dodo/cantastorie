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
| Narration | **Voxtral Mini TTS via OpenRouter** (`mistralai/voxtral-mini-tts-2603`); **Deepgram** for word timings (STT pass) and the fallback voice (Aura) — see **[ADR-004](../../../docs/adr/ADR-004-narration-deepgram-voxtral.md)** | Browser TTS; ElevenLabs (retired — ADR-004); any vendor key beyond the flagged pipeline-only Deepgram exception |
| Hosting | Render via Docker (`render.yaml`) | GitHub Pages, Netlify, Vercel |

**Why Web Audio is non-negotiable:** iOS makes media-element volume read-only, which kills the mandated gentle crossfades. Any library built on media elements (Howler's default html5 mode included) inherits that. Gain nodes work everywhere. This decision is unchanged.

**Narration note:** narration runs on the same OpenRouter key as the rest of the pipeline (Voxtral). Voxtral emits no word timestamps, so reading-mode karaoke timings are reconstructed at slice 6 via a **Deepgram STT transcription pass** over the narrated audio; Deepgram Aura is the named fallback voice. Warmth, cross-language voice consistency, Greek support, and timing-alignment quality are validated at AI-366 (a Voxtral-vs-Aura bake-off) before the library is built. **ElevenLabs is retired entirely** — no transport, no key, no fallback role; re-adding it takes a superseding ADR. The full reasoning is in [ADR-004](../../../docs/adr/ADR-004-narration-deepgram-voxtral.md) (which supersedes ADR-002).

## Red Flags — Stop and Read docs/architecture.md and docs/adr/

- "The monorepo's CLAUDE.md says Vue/React is the pattern here" — that file describes *sibling* projects. Cantastorie's own `docs/architecture.md` overrides it.
- "Library X handles iOS audio quirks for us" — the iOS constraint is why Web Audio was chosen; a wrapper doesn't lift it.
- "A bundler/framework would make this easier" — no-bundler is a settled decision, not an oversight.
- "Let's just use ElevenLabs for narration" — ElevenLabs is retired by decision, not oversight (ADR-004); the narrator is Voxtral via OpenRouter, the fallback voice is Deepgram Aura, and re-adding ElevenLabs requires a superseding ADR.
- Recommending `npm install <framework>` or a new provider SDK/key without citing `docs/architecture.md` or the relevant ADR.

Changing a settled decision is possible — but it happens by editing `docs/architecture.md` (and adding or superseding an ADR in `docs/adr/`) with the human's agreement first, never by installing around it.
