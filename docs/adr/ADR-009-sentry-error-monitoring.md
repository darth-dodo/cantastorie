# ADR-009: Sentry Error Monitoring (Server-Side Only)

**Date**: 2026-09-27
**Status**: Accepted
**Context**: Unhandled exceptions in the FastAPI app and failed pipeline runs are invisible unless someone reads Render logs
**Decider(s)**: Project Owner

---

## Summary

Sentry is added as the **error-monitoring** layer for the FastAPI app and the authoring pipeline CLI, via the `sentry-sdk[fastapi]` Python SDK. It is **server-side only**: the child player loads no Sentry script, keeping the ADR-003 guard (zero third-party JavaScript on the player) intact. It reports **errors only** — `traces_sample_rate=0` — because LangSmith remains the tracing layer (ADR-007). When `SENTRY_DSN` is unset (the default) Sentry never initializes and no data leaves the process.

---

## Problem Statement

### The Challenge

LangSmith (ADR-007) traces LLM, narration, and image calls, but it is not an error tracker: it does not group exceptions, alert on new ones, or tie them to a deploy. Today a 500 from `/parent` or `/workshop`, or a workshop run that lands `failed`, is only discoverable by reading Render's log stream. Workshop runs make this worse: `RunManager.execute` deliberately catches every exception to record the run as `failed`, so the error never surfaces as an HTTP failure at all.

### Why This Matters

- **Parents are the first users who aren't the operator** (ADR-003): a broken request flow for them has to be noticed without them reporting it.
- **Failed generations cost money**: a provider outage or a schema regression that fails every run should alert once, grouped, with the deploy that introduced it.
- **Privacy constraint**: the app forbids tracking and analytics on child-facing traffic, and parent requests or pipeline prompts can contain a child's name. Error reports must not carry that data.

### Success Criteria

- [x] Unhandled exceptions in FastAPI routes are reported, grouped, and tagged with environment and release (deployed commit)
- [x] Workshop/parent pipeline runs that land `failed` are reported, even though `execute()` swallows the exception
- [x] Pipeline CLI failures (`cantastorie generate`) are reported
- [x] Events carry no request bodies, no stack-frame local variables, no IPs, and no auth headers or cookies
- [x] The child player page loads no Sentry script
- [x] With `SENTRY_DSN` unset, Sentry never initializes

---

## Context

### Current State

- `src/observability.py` wires LangSmith at startup from `create_app()` and the CLI's `generate` command.
- `src/workshop/manager.py` runs generation in a thread and converts any exception into a `failed` run record (`error=str(error)`).
- Render injects `RENDER_GIT_COMMIT` into the runtime environment; the Sentry SDK's release auto-detection does not read it.

### Constraints

- **No browser SDK** — ADR-003's guard test asserts the player ships zero third-party JS. The parent and workshop pages could take one later, but that is a separate decision.
- **No child data** — events must be safe even when the failing request carried a child's name in its body or a prompt was in a local variable.
- **Settled architecture** — Sentry is an observability add-on alongside LangSmith, not a replacement for it and not a new provider on the story path.

---

## Options Considered

### Option 1: Sentry (Python SDK, errors only)

**Description**: `sentry-sdk[fastapi]`; `sentry_sdk.init` from a new `init_error_monitoring(settings)`; an explicit `capture_exception` where the workshop swallows run failures.

**Pros**:
- The FastAPI/Starlette integrations auto-enable — unhandled route errors are captured with no per-route code.
- Grouping, alerting, release tracking, and regression detection out of the box.
- An EU data region is available (`*.ingest.de.sentry.io` DSNs), matching the EU-first audience.
- Privacy options are first-class: `send_default_pii`, `max_request_body_size`, `include_local_variables`.

**Cons**:
- One more dependency and one more vendor to disclose as a data processor.
- One more secret-ish env var (`SENTRY_DSN`).

**Risks**:
- A future SDK default could widen what's captured — mitigated by setting every privacy option explicitly, pinned by tests.

### Option 2: LangSmith only (status quo)

**Description**: Rely on LangSmith traces plus Render logs.

**Pros**: No new dependency or vendor.

**Cons**: No exception grouping or alerting; HTTP errors outside LLM calls are not traced meaningfully; swallowed workshop failures stay invisible.

### Option 3: Structured logs + Render log alerts

**Description**: Log exceptions as JSON and alert from a log drain.

**Pros**: No vendor SDK in-process.

**Cons**: Needs a log-drain destination anyway (another vendor); no grouping, no release regression tracking; more code to maintain.

---

## Comparison Matrix

| Criterion | Sentry | LangSmith only | Logs + alerts |
|-----------|--------|----------------|---------------|
| Exception grouping + alerting | Yes | No | Partial |
| Captures swallowed workshop failures | Yes (explicit capture) | No | Yes (explicit log) |
| Release/deploy correlation | Yes | No | Manual |
| Privacy controls | Explicit SDK options | N/A | Manual |
| Setup effort | Low | None | Medium |
| New vendor | Yes | No | Yes (log drain) |

---

## Decision

**Sentry, server-side, errors only.**

### Integration points

| Surface | Mechanism | File |
|---------|-----------|------|
| FastAPI route exceptions | Auto-enabled FastAPI/Starlette integrations | `src/api/main.py` → `create_app` calls `init_error_monitoring` |
| Workshop / parent pipeline runs | `sentry_sdk.capture_exception(error)` before landing the run `failed` | `src/workshop/manager.py` → `RunManager.execute` |
| Pipeline CLI | `init_error_monitoring` — uncaught exceptions reach the SDK's excepthook | `src/pipeline/cli.py` → `generate` |
| Init | `init_error_monitoring(settings)` | `src/observability.py` |

### Configuration

| Setting | Type | Default | Purpose |
|---------|------|---------|---------|
| `sentry_dsn` | `SecretStr` | `""` | Empty ⇒ Sentry never initializes |
| `sentry_environment` | `str` | `"development"` | `production` / `preview` on Render |
| `sentry_release` | `str` | `SENTRY_RELEASE`, else Render's `RENDER_GIT_COMMIT` | Ties every event to the deployed commit |

### Privacy settings (pinned by tests)

| SDK option | Value | Why |
|------------|-------|-----|
| `send_default_pii` | `False` | No IPs, no user identity; `authorization` / `cookie` headers are sent as `[Filtered]` |
| `max_request_body_size` | `"never"` | Parent request bodies can name a child |
| `include_local_variables` | `False` | Pipeline frames hold prompts and story text |
| `traces_sample_rate` | `0.0` | Tracing is LangSmith's job (ADR-007) |

Stack frames still carry source-code context lines from the repository, which contain no runtime data.

---

## Consequences

- **One additional dependency**: `sentry-sdk[fastapi]>=2.0.0`.
- **Up to three env vars**: `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, and optionally `SENTRY_RELEASE`.
- **A new data processor to disclose**: Sentry receives exception types, messages, stack traces, request URL/method, and filtered headers — operator/parent-side only. Use an EU-region project.
- **Exception messages are sent verbatim**: code must keep child data out of exception messages, as it already must for the `error` field on run records shown in the workshop.
- **No impact when disabled**: with no DSN, `init_error_monitoring` returns before touching the SDK, and `capture_exception` is a no-op on an uninitialized SDK.

---

## Implementation Plan

1. Add `sentry-sdk[fastapi]` to `pyproject.toml`. ✅
2. Add `sentry_dsn`, `sentry_environment`, `sentry_release` to `Settings`. ✅
3. Add `init_error_monitoring(settings)` to `src/observability.py`; call it from `create_app` and the CLI. ✅
4. Report swallowed run failures in `RunManager.execute`. ✅
5. Document env vars in `.env.example`, `render.yaml`, and `docs/setup.md`. ✅

---

## Validation

- `make check` and `make test` pass.
- Unit tests pin: no init without a DSN; errors-only; the three privacy options; `RENDER_GIT_COMMIT` → release; workshop failures reach `capture_exception`.
- Probe with the real SDK (a failing POST carrying a child's name, a bearer token, and a session cookie): the captured event had an empty body, no frame `vars`, and `[Filtered]` auth/cookie headers.

---

## Related Decisions

- [ADR-003](ADR-003-parent-authentication-clerk.md) — the player's zero-third-party-JS guard is why there is no browser SDK.
- [ADR-005](ADR-005-workshop-area.md) — in-process runs are why failures need an explicit capture.
- [ADR-007](ADR-007-langsmith-observability.md) — LangSmith stays the tracing layer; Sentry is errors only.

---

## References

- [Sentry Python SDK — FastAPI](https://docs.sentry.io/platforms/python/integrations/fastapi/)
- [Sentry Python SDK — Options](https://docs.sentry.io/platforms/python/configuration/options/)
- [Sentry — Data Collected](https://docs.sentry.io/platforms/python/data-collected/)

---

## Metadata

- **Tags**: observability, error-monitoring, sentry, privacy, workshop
