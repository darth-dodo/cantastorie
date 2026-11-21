---
name: factory:test
description: Use before opening or updating a Cantastorie PR, or when asked whether a branch passes, to run every local gate CI and the definition of done expect and report each one with its real exit code.
---

# factory:test

Mirrors the CI jobs behind `CI Success` (Lint & Format Check, Type Check, Python Test, JS Test, E2E Test, Security Scan, Build Docker Image) plus the checks CI cannot run. Report a table: gate, command, exit code, one line of evidence. Never pipe a gate through `tail` or `grep` when judging it: that hides the exit code. Run each gate on its own.

## Always

| Gate | Command |
|---|---|
| Lint, format, types | `make check` (ruff check, ruff format check, mypy) |
| Python tests + coverage floor | `uv run pytest --cov=src` (fails below `fail_under = 70` in `pyproject.toml`) |
| JS tests | `npx vitest run` |
| E2E | `E2E_PORT=<free port> npx playwright test` (a free port per worktree, so parallel builders do not collide) |
| Security scan | CI's Security Scan job: `uvx bandit -r src/ -ll` (same flags as ci.yml's security job) |
| Spec coverage | Every scenario in the change's `specs/` has a test that asserts it; list scenario -> test file:line |
| Changed changes | `openspec validate <change-id> --strict` for each change the branch touches |

R2 Bucket Audit runs on `main` only, with secrets PRs do not get: not a local gate. Say so in the table rather than skipping it silently.

## When the branch touches deploy files

`render.yaml`, `Dockerfile`, `.github/workflows/ci.yml`, `pyproject.toml`, `uv.lock`, `package.json`, `package-lock.json`: run `docker build .` (CI's Build Docker Image, which also compiles Tailwind in stage 1), then the `render-deploy` skill's "Before opening a deploy PR" smoke test.

## When the branch touches generation, review or publishing

Show the parent review gate and the safety checks still hold: the B2/H1 tests pass and are named in the report, at least `tests/api/test_parent_review_gate.py`, `tests/api/test_parent_approve.py`, `tests/pipeline/test_publish.py` and the review tests in `tests/workshop/test_records.py`, plus any new test the branch adds for the gate. The child never meets an unreviewed story; a red here blocks the PR like any other gate. Never claim the gate holds without the test names and their result.

## Result

All gates green: say so plainly. Any red: stop, use `superpowers:systematic-debugging`, fix the cause, rerun every gate.
