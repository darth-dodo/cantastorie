---
name: settled-architecture
description: Use when choosing or proposing frameworks, libraries, build tooling, audio playback, state management, hosting, deployment, or repo structure in cantastorie — before recommending any new dependency or structural change.
---

# Settled Architecture

## Overview

Cantastorie's stack is **settled and documented in `docs/architecture.md`**. That file is the authority for every technology choice. Read it before proposing any dependency, tool, or structural change — proposals that contradict it are wrong even when they'd be good defaults elsewhere.

## The Settled Decisions

| Area | Settled | Explicitly NOT |
|------|---------|----------------|
| App shape | One FastAPI app: player page + parent area | Separate frontend app, static-only site |
| Player UI | Vanilla ES modules, FSM-managed | React, Vue, Svelte, any framework |
| Build tooling | None (Tailwind CLI only, for parent UI) | Vite, webpack, esbuild, TypeScript |
| Audio | Web Audio API: decoded buffers + gain nodes | `<audio>` tags, Howler.js, media elements |
| Story assets | Bucket-direct from Cloudflare R2 | Routing story bytes through the app server |
| Child state | IndexedDB only | Cookies, accounts, server-side state |
| Pipeline | Plain Python + Pydantic AI, filesystem checkpoints | LangGraph or any graph framework |
| LLM/image access | OpenRouter (per-step model choice) | Direct provider SDKs |
| Narration | ElevenLabs with character timestamps | Browser TTS, other TTS vendors |
| Hosting | Render via Docker (`render.yaml`) | GitHub Pages, Netlify, Vercel |

**Why Web Audio is non-negotiable:** iOS makes media-element volume read-only, which kills the mandated gentle crossfades. Any library built on media elements (Howler's default html5 mode included) inherits that. Gain nodes work everywhere.

## Red Flags — Stop and Read docs/architecture.md

- "The monorepo's CLAUDE.md says Vue/React is the pattern here" — that file describes *sibling* projects. Cantastorie's own `docs/architecture.md` overrides it.
- "Library X handles iOS audio quirks for us" — the iOS constraint is why Web Audio was chosen; a wrapper doesn't lift it.
- "A bundler/framework would make this easier" — no-bundler is a settled decision, not an oversight.
- Recommending `npm install <framework>` or a new provider SDK without citing `docs/architecture.md`.

Changing a settled decision is possible — but it happens by editing `docs/architecture.md` with the human's agreement first, never by installing around it.
