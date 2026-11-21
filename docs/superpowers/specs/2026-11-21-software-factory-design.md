# Cantastorie Software Factory (ported from Maya)

**Date:** 2026-11-21 · **Status:** Draft design, pending owner review · **Source:** `darth-dodo/maya` `FACTORY.md` (16 Nov 2026) and `.claude/` at `ffbc7bd`

---

## Summary

Cantastorie adopts Maya's software factory in full: an agent-run pipeline from a product request to a live Render deploy, where the human jobs are to write the product doc, answer agents and press Merge. Every station, skill, guardrail and flywheel step from Maya is ported and adapted to cantastorie's stack (Python/uv, vanilla JS, Render with a CI-gated deploy hook). The work queue moves from Linear to GitHub Issues, planning moves to OpenSpec, and all of Linear's Cantastorie history is copied to GitHub except security-sensitive issues.

## Intent

| | |
|---|---|
| **Outcome** | Cantastorie is built the way Maya is: by `factory:*` skills, one station at a time, with machine-enforced guardrails and a measured improvement loop |
| **Who it's for** | Abhishek, as the single human in the loop (product doc, answers, merges) |
| **Success** | A request can go `factory:ask` → `factory:plan` → `factory:build` → PR → review → green CI → Abhishek merges → deploy → smoke check, with no step needing a Maya-specific name, command or path |
| **Constraints** | Cantastorie's settled architecture (the `settled-architecture` skill and `docs/adr/`) stays authoritative; no secrets in the repo; nothing in Linear is deleted |

## Decisions settled during brainstorming (2026-11-21)

| Question | Decision |
|---|---|
| Scope | Full factory: every `factory-*` skill and the supporting skills, not just the core loop |
| Work queue | GitHub Issues, like Maya (replacing Linear) |
| Specs | OpenSpec, like Maya; ADRs still record settled decisions |
| Linear history | Copy everything (open and closed) to GitHub Issues |
| Security-sensitive issues | Not copied; stay in Linear only, listed as "kept in Linear" |
| Merging | Denied to agents (`gh pr merge`); Abhishek merges |
| Herdr | Not ported |
| Extra pieces | GitHub Project board, post-deploy smoke workflow, `/health/version` |
| Flywheel start | Counts from the first PR merged after install; no backfill |

## Part 1: what lands in the repo

### Docs

| File | Content |
|---|---|
| `FACTORY.md` | Maya's, adapted. Same nine lines: 1 Intake, 2 Plan, 3 Build, 4 Verify, 5 Review, 6 Check, 7 Gate, 8 Ship, 9 Feedback. Line 6 is cantastorie's CI (`CI Success`: ruff, mypy, pytest with coverage, vitest, Playwright E2E, security scan, Docker build, R2 audit). Line 8 is the existing CI-gated Render deploy hook (AI-479). Secrets table lists cantastorie's env vars (OpenRouter, R2, Clerk, Sentry, LangSmith, Render deploy hook) with no values |
| `CLAUDE.md` | Adds the definition of done (evidence for every gate) and a pointer to `FACTORY.md`; existing content kept |
| `PHASE_2.md` | New: where ideas that are not for now go (`factory:ask` "Later") |
| `GLOSSARY.md` | Created by intake (`grill-with-docs`) when the first terms are settled |
| `docs/factory/experiments.md`, `docs/factory/LESSONS.md` | Empty tables, written by `factory:improve` |
| `docs/factory/linear-migration.md` | The `AI-NNN → #N` mapping (Part 2) |

### Guardrails: `.claude/settings.json`

Copied from Maya with cantastorie's commands:

- **Deny:** `Bash(gh pr merge *)`, `Bash(git push --force *)`, `Bash(git push -f *)`, `Bash(git push origin main *)`, `Read(./.env)`, and the overlapping skills `mattpocock-skills:tdd`, `mattpocock-skills:diagnosing-bugs`, `mattpocock-skills:to-spec`, `mattpocock-skills:to-tickets`, `openspec-apply-change`.
- **Allow:** `make check`, `make test`, `uv run pytest`, `uv run ruff`, `uv run mypy`, `npx vitest run`, `npx playwright test`, read-only `git` (`status`, `diff`, `log`), `gh pr view|list|checks|create`, `gh issue view|list`, and `openspec list|status|validate|show|archive`.

Any existing project `.claude/settings.json` content is merged, not replaced.

### Skills: `.claude/skills/`

| Maya skill | Cantastorie version |
|---|---|
| `factory-ask`, `factory-plan`, `factory-build`, `factory-test`, `factory-review`, `factory-pause`, `factory-resume`, `factory-cleanup`, `factory-retro`, `factory-improve`, `factory-release`, `factory-recap`, `factory-dashboard` | Ported. `mix` gates become cantastorie's (`make check`, `uv run pytest`, `npx vitest run`, `npx playwright test`); Maya names, the Pokémon product rules, voice-latency checks and paths are replaced with cantastorie's (bedtime-story product rules: child safety, parent review, privacy); `~/maya-factory/` becomes `~/cantastorie-factory/` |
| `openspec-apply-change`, `openspec-archive-change`, `openspec-explore`, `openspec-propose`, `openspec-sync-specs`, `openspec-update-change` | Copied (they are product-neutral), with `openspec-apply-change` denied as in Maya |
| `project-manager`, `release-manager`, `render-deploy`, `quiz-me` | Ported to cantastorie: GitHub Issues and the board; commitizen releases (AI-488); `render.yaml` and the deploy hook; cantastorie's code and docs |
| `next-task` | Replaced by Maya's GitHub Issues version, adapted |
| `settled-architecture` | Kept as cantastorie's own (its content is cantastorie-specific) |
| `documentation-conventions` | Kept |
| `herdr` | Not ported |

### OpenSpec

`openspec init` in the repo, then `openspec/config.yaml` with cantastorie's context: the product (child-steered bedtime stories, parent approves everything, ten languages), the settled stack (pointer to `docs/architecture.md` and the ADRs), the safety and privacy rules, and "build with Superpowers; never use `openspec-apply-change`". Existing behaviour is not back-filled into `openspec/specs/`; specs grow as changes are archived.

### GitHub setup (repo settings; each needs Abhishek's go-ahead)

- **Labels:** `bug`, `feature`, `paused`, `needs-info`, `ready-for-human`, `epic`, `status:in-progress`, `priority:urgent|high|medium|low`
- **PR template:** Maya's, with cantastorie's definition of done
- **Project board** "Cantastorie" with Status and Estimate fields
- **Branch protection on `main`:** required `CI Success` check, no force pushes, admins included

## Part 2: Linear to GitHub Issues

**Scope:** every issue in Linear's Cantastorie project, open and closed, including epics, **except security-sensitive issues**. Those stay in Linear and are listed in the mapping doc as "kept in Linear". An issue is security-sensitive when its title or description describes an unfixed or recently fixed vulnerability in detail (token exposure, auth bypass, unauthenticated access, secrets); each one the migration skips is listed for Abhishek to confirm.

**Each copied issue:**

- **Title:** unchanged.
- **Body:** first line `Migrated from Linear AI-NNN (link)`, then the original description, then a metadata block: status, priority, assignee, created and completed dates, parent epic.
- **Comments:** one comment holding the Linear comments in order, each with its author and date.
- **Labels:** priority (`priority:*`), `epic` for epics, `bug`/`feature` where Linear had them, `status:in-progress` for In Progress or In Review.
- **State:** Done → closed as completed; Canceled → closed as not planned; Duplicate → closed as not planned with the target noted; open → open.
- **Hierarchy:** children reference their epic as `#N`, epics list their children; links are filled in after all issues exist.
- **Board:** open issues are added to the Project board.

**Running it:**

- In AI-number order, paced below GitHub's rate limits (about one issue every few seconds).
- **Resumable and idempotent:** a mapping file records `AI-NNN → #N` as each issue is created; a rerun skips anything already mapped.
- The final mapping is committed as `docs/factory/linear-migration.md`.
- **Linear is read-only** apart from one note added to the project description: "Moved to GitHub Issues on <date>; see docs/factory/linear-migration.md".
- Creating the issues will notify Abhishek once per issue unless he unwatches the repo during the run.

## Part 3: flywheel and runtime

### Flywheel (as in Maya)

- **`factory:retro`**: once 10 merged PRs have no row, writes one metrics row per merged PR to `~/cantastorie-factory/metrics.csv` (`lead_time_h`, `ci_red_runs`, `review_blockers`, `denied_calls`, `first_pass_green`, `escaped_defects`, `review_precision`, `review_misses`) and one lesson per Issue in `~/cantastorie-factory/retros/`. Counting starts at the first PR merged after install.
- **`factory:improve`**: after each retro batch, compares medians of the last 5 with the 5 before, opens ONE experiment PR for the worst metric, logs it in `docs/factory/experiments.md`, keeps or reverts the last one, folds lessons into `docs/factory/LESSONS.md`.
- **`factory:release`**: after `factory:improve`, or on request; checks whether a release is due and hands off to `release-manager` (commitizen `cz bump`, CHANGELOG, tag, GitHub Release).
- **`factory:recap`**: under 15 lines; writes nothing.
- **`factory:dashboard`**: one private "Cantastorie factory" page rebuilt from live sources each run, "Needs Abhishek" first, every number sourced; writes nothing to the repo or GitHub.

### Supporting pieces

- **GitHub Project board** (Part 1).
- **Post-deploy smoke workflow** (`.github/workflows/smoke.yml`): hourly and after each deploy; checks `/health`, `/play` and `/health/version` on the live URL; reports, never blocks a deploy.
- **`/health/version`**: a public endpoint returning `{"commit": RENDER_GIT_COMMIT}` (empty locally), so the dashboard can show deploy lag. The only app code in this work; covered by a test.

### Runtime (as in Maya)

- `planner`: one long-lived session for lines 1 and 2 (`claude --remote-control planner`).
- `builder-<issue>`: one per Issue in its own worktree (`claude -w <issue> --remote-control builder-<issue>`, then `factory:build #<issue>`); at most two in parallel.
- `reviewer`: a subagent the builder starts with Matt's `code-review`.
- Blocked prompts surface on the phone through Remote Control.

## Order of work

Each step is its own PR:

1. Guardrails (`.claude/settings.json`) and docs (`FACTORY.md`, `CLAUDE.md`, `PHASE_2.md`, `docs/factory/`)
2. Skills and `openspec init` with `openspec/config.yaml`
3. GitHub setup: labels, PR template, board, branch protection (each with Abhishek's go-ahead)
4. Linear migration (dry run first: the list of issues to copy and to keep in Linear, for Abhishek to confirm)
5. Smoke workflow and `/health/version`

## Testing and verification

- **Skills:** each ported skill is checked for leftover Maya names, commands and paths (`mix`, `Pokémon`, `maya`, `~/maya-factory`), and every command it names exists in cantastorie.
- **Guardrails:** a denied command (`gh pr merge`) is refused in a fresh session.
- **OpenSpec:** `openspec validate --strict` passes on a first real change.
- **Migration:** the dry run lists every Linear issue with its planned action; after the run, the mapping count equals copied issues, a rerun creates nothing, and a sample of issues is spot-checked (body, comments, labels, state, epic links).
- **Smoke and `/health/version`:** a pytest for the endpoint; one manual run of the workflow against the live URL.
- **End to end:** one small real request taken through every station, from `factory:ask` to a merged, deployed, smoke-checked PR.

## Out of scope

- Herdr
- Back-filling `openspec/specs/` from existing behaviour
- Back-filling factory metrics for PRs merged before install
- Changing any settled architecture decision
