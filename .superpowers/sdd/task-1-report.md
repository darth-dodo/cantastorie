# Task 1 Report — AI-409: Clerk identity layer

## Files changed

| File | Change |
|------|--------|
| `pyproject.toml` | Added `pyjwt>=2.9.0` and `cryptography>=43.0.0` to `[project].dependencies` |
| `uv.lock` | Updated by `uv sync --all-extras` |
| `src/config.py` | Added `clerk_publishable_key`, `clerk_secret_key`, `clerk_jwks_url` fields |
| `.env.example` | Added three `CLERK_*` vars with comment (unset ⇒ 404) |
| `src/api/auth.py` | New file — `_JwksState`, `_fetch_jwks`, `_get_keys`, `ParentContext`, `require_parent` |
| `tests/api/__init__.py` | New empty file (package marker) |
| `tests/api/test_auth.py` | New file — 9 tests covering all specified cases |

## Key design decisions

### JWKS cache / stale-if-error seam

The cache state lives in `_JwksState`, a plain mutable dataclass instance (`_jwks_state`) at module level. This avoids the `global` statement (ruff PLW0603) while keeping a monkeypatch-friendly seam: tests do `monkeypatch.setattr(_jwks_state, "keys", None)` and `monkeypatch.setattr(_jwks_state, "fetched_at", 0.0)` via the `reset_jwks_cache` autouse fixture.

The TTL check uses `time.monotonic()`. No injectable clock parameter was needed — the tests advance the perceived staleness by patching `_jwks_state.fetched_at` backwards (subtract `JWKS_TTL_SECONDS + 1`), which is deterministic without real sleeps.

Stale-if-error: `_get_keys` always attempts a refresh when the TTL has elapsed. If the fetch raises and `_jwks_state.keys` is not None, the exception is swallowed and stale keys are returned. If there is no prior cache, the exception propagates and `require_parent` catches it and raises 401.

### httpx fetch seam (not PyJWKClient)

`_fetch_jwks` is a module-level function that tests can monkeypatch. This is the same pattern as `NarrationClient` in `tests/pipeline/test_providers.py`. PyJWT's `PyJWKClient` was explicitly excluded because it uses `urllib` internally and cannot be intercepted at the httpx seam.

### ParentContext dataclass

Frozen dataclass (not Pydantic model) — lighter weight, matches the brief's recommendation. FastAPI doesn't serialize it as a response body; it's returned from a Depends-wired dependency.

### `from __future__ import annotations` must be absent in tests

FastAPI resolves route handler parameter annotations at decoration time (when `@app.get("/me")` executes). With PEP 563 lazy strings, `Annotated[ParentContext, Depends(require_parent)]` becomes the string `"Annotated[ParentContext, Depends(require_parent)]"` and FastAPI treats the parameter as a query parameter named `ctx`, returning 422. The test file explicitly does NOT include `from __future__ import annotations` and includes a comment explaining why.

### azp enforcement deferred

`require_parent` reads `azp` from the payload if present but does not validate it. A short comment (`# Hard-fail enforcement deferred until the azp allowlist config field is added in a later step`) marks the seam for the future config field per the brief's instructions.

## Verification commands and output

```
$ uv run pytest
...
====================== 168 passed, 11 warnings in 17.51s =======================

$ uv run ruff check .
All checks passed!

$ uv run mypy src
Success: no issues found in 28 source files
```

Pytest summary: **168 passed, 11 warnings** (9 new auth tests + 159 existing, all green).

Warnings are all pre-existing deprecations from starlette TestClient (per-request cookies) and pydantic-graph; none introduced by this task.

## Concerns

None. The implementation is straightforward and all acceptance criteria are met:
- No network in tests; no real sleeps.
- No Clerk SDK in `pyproject.toml`.
- `require_parent` is exported and importable but NOT wired into any router.
- Full type coverage; mypy strict mode passes.
