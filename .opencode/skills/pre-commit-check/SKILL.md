# Skill: pre-commit-check

## Overview

Cantastorie uses a layered quality gate: Ruff (lint + format), mypy (types), detect-secrets, and commitizen (conventional commits). This skill ensures all checks pass before any commit.

## When to Use

- Before committing any change to cantastorie
- When diagnosing a failing CI run
- When a pre-commit hook rejects a commit

## The Check Sequence

Run in this order — fail fast:

1. **`make lint`** — Ruff linter on `src/` and `tests/`
2. **`make format-check`** — Ruff format verification
3. **`make typecheck`** — mypy on `src/`
4. **`make test`** — pytest + vitest
5. **`make pre-commit`** — full pre-commit (trailing-whitespace, end-of-file-fixer, ruff, ruff-format, mypy, detect-secrets, commitizen)

Or run `make check` (lint + format-check + typecheck) then `make test`, then `make pre-commit`.

## Common Failures

| Failure | Fix |
|---------|-----|
| Ruff lint error | `make lint-fix` to auto-fix |
| Ruff format error | `make format` to format |
| mypy type error | Fix the type annotation; do not use `# type: ignore` unless the AGENTS.md owner approves |
| detect-secrets flag | If false positive, update `.secrets.baseline` with `detect-secrets scan` |
| commitizen message | Use conventional commit format: `type(scope): description` |

## Commit Message Format

Conventional commits enforced by commitizen:

```
feat(player): add crossfade gain node
fix(pipeline): retry on OpenRouter 429
docs(adr): add ADR-003 for storage layout
test(api): cover parent gate redirect
```

## CI

GitHub Actions runs the same checks (`.github/workflows/ci.yml`). Local `make check` passing means CI should pass.
