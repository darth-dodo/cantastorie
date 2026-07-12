# ADR-007: LangSmith App-Wide Observability

**Date**: 2026-07-12
**Status**: Accepted
**Context**: The app and pipeline need observability — tracing for LLM calls, HTTP requests, narration, and image generation
**Decider(s)**: Project Owner

---

## Summary

LangSmith is added as the observability layer for both the FastAPI app and the authoring pipeline. The `langsmith` Python SDK provides `TracingMiddleware` for FastAPI request tracing, `wrap_openai` for auto-tracing LLM calls through Pydantic AI's OpenAI-compatible provider, and `@traceable` decorators for non-LLM external calls (narration TTS, image generation). When `LANGSMITH_TRACING=false` (the default), the SDK is inert — no data leaves the process, and the wrapped/decorated callables behave identically to their unwrapped originals.

---

## Problem Statement

### The Challenge

The pipeline makes LLM calls (write, safety, revise), TTS calls (Voxtral narration), and image generation calls (illustration) — all through OpenRouter. The FastAPI app serves the player, parent area, and workshop. When something goes wrong (a bad story, a slow generation, a failed safety gate), there is no trace of what happened: which model was called, with what prompt, what it returned, how long it took, or where in the pipeline the failure occurred.

### Why This Matters

- **Pipeline debugging**: a story that fails the safety gate needs the full LLM call chain visible — the prompt, the response, the model, the step.
- **Cost monitoring**: every LLM call has a cost; without tracing, there is no per-step or per-story cost breakdown.
- **Workshop runs**: in-process pipeline runs triggered from `/workshop` need request-level tracing to correlate a run with its LLM calls.
- **Privacy constraint**: the app forbids tracking and analytics on child-facing traffic. Observability must be pipeline-and-operator-side only, never child-side.

### Success Criteria

- [x] All LLM calls (write, safety, revise) are traced with prompt, response, model, and duration
- [x] All narration TTS calls are traced with text, voice, model, and duration
- [x] All image generation calls are traced with prompt, model, and duration
- [x] All FastAPI HTTP requests are traced with method, path, and status
- [x] Pipeline runs appear as root spans containing nested LLM/narration/image spans
- [x] When tracing is disabled, zero overhead and zero data leaves the process

---

## Context

### Current State

- The pipeline uses Pydantic AI's `OpenAIProvider` pointed at OpenRouter's OpenAI-compatible endpoint. `build_model()` in `src/pipeline/providers.py` creates the provider.
- Narration uses a raw `httpx.Client` to POST to OpenRouter's `/audio/speech` endpoint (`NarrationClient.synthesize`).
- Image generation uses a raw `httpx.Client` to POST to OpenRouter's `/chat/completions` endpoint with `modalities: ["image", "text"]` (`ImageClient.generate`).
- The FastAPI app in `src/api/main.py` has no middleware.
- Configuration is in `src/config.py` as a Pydantic `BaseSettings` class.

### Constraints

- **No new provider key at story time** — the privacy architecture forbids keys in the browser; LangSmith's key is pipeline/app-side only, like OpenRouter's.
- **No child data** — LangSmith traces operator and pipeline activity, never child-facing traffic. The player page is a static shell; its requests are bucket-direct to R2, not through the app server.
- **Settled architecture** — LangSmith is an observability layer, not a replacement for OpenRouter (still one gateway), not a graph framework (still plain Python pipeline), not a frontend dependency (no browser-side SDK).

---

## Options Considered

### Option 1: LangSmith

**Description**: Use the `langsmith` Python SDK with `wrap_openai` for LLM calls, `@traceable` for non-LLM external calls, and `TracingMiddleware` for FastAPI.

**Pros**:
- `wrap_openai` auto-traces all Pydantic AI LLM calls without touching step code — the OpenAI client is wrapped at the provider seam.
- `@traceable` is a one-line decorator on existing methods — no restructuring.
- `TracingMiddleware` is a one-line `app.add_middleware()` — no route changes.
- When disabled, the SDK is inert: `wrap_openai` passes through, `@traceable` calls the function directly, `TracingMiddleware` adds negligible overhead.
- LangSmith is the de facto standard for LLM observability; the UI is purpose-built for tracing LLM call chains.

**Cons**:
- Adds one dependency (`langsmith`).
- Adds three env vars (`LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`).
- The LangSmith API key is a second key in the pipeline environment (alongside OpenRouter).

**Risks**:
- LangSmith SDK could add overhead to LLM calls even when tracing is off — mitigated by the conditional wrap in `build_model` (only wraps when `langsmith_tracing=True`).
- `@traceable` decorators are always active (even when tracing is off), but the SDK checks `LANGSMITH_TRACING` before doing any work — the function is called directly when off.

### Option 2: OpenTelemetry + OTLP exporter

**Description**: Use `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-openai`, and an OTLP exporter to a backend like Jaeger or Honeycomb.

**Pros**:
- Vendor-neutral standard.
- Rich ecosystem of instrumentations.

**Cons**:
- More moving parts: instrumentation libraries, exporter config, a separate backend to run.
- OpenAI instrumentation for OTel is less mature than LangSmith's `wrap_openai`.
- Requires running a collector/backend — more operational burden for a one-owner project.

### Option 3: Structured logging

**Description**: Add structured logging (JSON) to each step and pipe to a log aggregator.

**Pros**:
- No new dependency.
- Full control over what's logged.

**Cons**:
- No trace hierarchy — logs are flat, not nested.
- No UI for visualizing call chains.
- Significant code changes to add logging to every step.

---

## Comparison Matrix

| Criterion | LangSmith | OpenTelemetry | Structured Logging |
|-----------|-----------|---------------|---------------------|
| LLM call tracing | Auto (wrap_openai) | Manual/instrumentation | Manual |
| Trace hierarchy | Yes (nested spans) | Yes (spans) | No (flat logs) |
| Setup effort | Low (3 env vars, decorators) | Medium (collector, exporter) | High (per-step logging) |
| UI for trace visualization | Yes (LangSmith UI) | Yes (Jaeger/Honeycomb) | No |
| Overhead when off | Negligible | Negligible | N/A |
| Vendor lock-in | Low (SDK is thin wrapper) | None | None |
| Operational burden | None (SaaS) | Medium (run backend) | Low |

---

## Decision

**LangSmith** — the `wrap_openai` seam gives auto-tracing of all LLM calls with a one-line change at the provider level, `@traceable` covers the non-LLM external calls (narration, images), and `TracingMiddleware` covers HTTP requests. The inert-when-disabled behavior satisfies the privacy posture: `LANGSMITH_TRACING=false` means zero data leaves the process.

### Integration points

| Surface | Mechanism | File |
|---------|-----------|------|
| LLM calls (write, safety, revise) | `wrap_openai` on the AsyncOpenAI client passed to `OpenAIProvider` | `src/pipeline/providers.py` → `build_model` |
| Narration TTS | `@traceable(name="narration.synthesize")` | `src/pipeline/providers.py` → `NarrationClient.synthesize` |
| Image generation | `@traceable(name="illustrate.generate")` | `src/pipeline/steps/illustrate.py` → `ImageClient.generate` |
| Pipeline root | `@traceable(name="pipeline.generate_story")` | `src/pipeline/generate.py` → `generate_story` |
| FastAPI requests | `TracingMiddleware` | `src/api/main.py` → `create_app` |
| Env var sync | `init_observability(settings)` | `src/observability.py`, called from `create_app` and CLI |

### Configuration

Three new settings in `src/config.py`:

| Setting | Type | Default | Purpose |
|---------|------|---------|---------|
| `langsmith_api_key` | `SecretStr` | `""` | LangSmith API key (pipeline/app-side only) |
| `langsmith_project` | `str` | `"cantastorie"` | LangSmith project name |
| `langsmith_tracing` | `bool` | `False` | Master switch; when false, SDK is inert |

---

## Consequences

- **One additional dependency**: `langsmith>=0.3.0` in `pyproject.toml`.
- **One additional env var**: `LANGSMITH_API_KEY` (only when tracing is enabled).
- **No impact on existing behavior**: when `langsmith_tracing=False` (the default), `build_model` uses the unwrapped `OpenAIProvider` path, `@traceable` calls functions directly, and `TracingMiddleware` is a no-op.
- **No child data traced**: the player page is a static shell; story-time traffic goes bucket-direct to R2, never through the app server. LangSmith only traces operator/workshop/pipeline activity.

---

## Implementation Plan

1. Add `langsmith>=0.3.0` to `pyproject.toml` dependencies.
2. Add `langsmith_api_key`, `langsmith_project`, `langsmith_tracing` to `Settings` in `src/config.py`.
3. Create `src/observability.py` with `init_observability(settings)` and `build_traced_openai_client(settings)`.
4. Modify `src/pipeline/providers.py`: conditionally wrap the OpenAI client in `build_model`; add `@traceable` to `NarrationClient.synthesize`.
5. Add `@traceable` to `ImageClient.generate` in `src/pipeline/steps/illustrate.py`.
6. Add `@traceable` to `generate_story` in `src/pipeline/generate.py`.
7. Add `TracingMiddleware` + `init_observability` call to `src/api/main.py`.
8. Add `init_observability` call to `src/pipeline/cli.py`.
9. Add env vars to `.env.example`.

---

## Validation

- `make check` passes (lint, format, strict mypy).
- `make test` passes (all existing tests unchanged).
- With `LANGSMITH_TRACING=false` (default), `build_model` returns an `OpenAIChatModel` with the same `base_url` and `model_name` as before.
- With `LANGSMITH_TRACING=true` and a valid API key, traces appear in the LangSmith UI.

---

## Related Decisions

- [ADR-001](ADR-001-technology-stack.md) — foundational stack; LangSmith is an observability layer, not a stack change.
- [ADR-005](ADR-005-workshop-area.md) — workshop runs pipeline in-process; LangSmith traces those runs via `TracingMiddleware`.

---

## References

- [LangSmith Python SDK](https://github.com/langchain-ai/langsmith-sdk)
- [LangSmith Documentation](https://docs.smith.langchain.com/)
