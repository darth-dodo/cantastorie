---
name: factory:review
description: Use when reviewing a Cantastorie PR or branch before Abhishek merges, combining Matt's code-review with the factory's definition of done and conventions, and reporting blocking versus non-blocking findings.
---

# factory:review

Review station (FACTORY.md line 5). Argument: a PR number or branch. Read the code and the Issue; never review from the diff summary alone.

## 1. Code review

Invoke `mattpocock-skills:code-review` against `origin/main` (merge base). Its two axes: Standards (this repo's conventions, `AGENTS.md`, `docs/architecture.md`) and Spec (the Issue and its OpenSpec change).

## 2. Factory checklist

| Check | Blocking if missing |
|---|---|
| `Closes #N`, Issue "done when" each shown with evidence | Yes |
| `factory:test` green, CI `CI Success` green | Yes |
| OpenSpec change archived in this PR, `openspec/specs/` matches the code | Yes, if the Issue has a change |
| No secrets, `.env` untouched, new env vars in `.env.example`, `render.yaml` and the FACTORY.md secrets table (names only) | Yes |
| Settled stack respected (`settled-architecture`), or an ADR added in `docs/adr/` | Yes |
| Generation, review or publishing touched: the PR's Safety section shows the parent review gate still holds, with the B2/H1 tests named and green (`factory:test`) | Yes |
| Child paths (the player and `/play`): no cookie, no account, no Clerk or other third-party script added; child state stays in IndexedDB | Yes |
| Player, parent area or workshop touched: Playwright green on this branch | Yes |
| Conventional Commit title (commitizen builds `CHANGELOG.md` from it), no hand edit to `CHANGELOG.md` | No |
| No em or en dashes, name written as "Cantastorie" | No |

## 3. Report

Two lists, blocking then non-blocking, each finding with an id, `file:line` and the smallest fix. Ids run F1, F2, ... across both lists and never repeat in one comment. Under the lists, the same findings as one table, so `factory:retro` can parse them:

```markdown
### Findings
| id | severity | file:line | finding |
|---|---|---|---|
| F1 | Blocking | src/pipeline/publish.py:42 | Digest checked before the copy, not inside it |
| F2 | Non-blocking | tests/test_parent_review.py:10 | Raw HTML match, select by element id |
```

Severity is exactly `Blocking` or `Non-blocking`. A file-wide finding uses `file:1`; a finding with no file uses `-`. No findings: the table with no rows. Checklist passes and "not verified" notes are not findings. Post the same as one PR comment (`gh pr comment <n> --body-file <file>`). Never approve or merge; Abhishek merges.
