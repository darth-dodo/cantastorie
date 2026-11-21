---
name: factory:build
description: Use when a builder session implements a Cantastorie GitHub Issue end to end, from picking it up to an open PR that meets the definition of done. Runs build and verify in order, calling factory:test and factory:review.
---

# factory:build

Build and verify stations (FACTORY.md lines 3 and 4). Argument: an Issue number, or none to let `next-task` pick. Invoke named skills with the Skill tool; do not copy their content. Each step must meet its "done when" before the next.

| # | Step | Use | Done when |
|---|---|---|---|
| 1 | Pick | `next-task` if no Issue given; `gh issue view <n>` | One Issue with a "done when" and a Spec link |
| 2 | Preconditions | Running in a worktree (`claude -w <issue>`), not on `main`; `openspec/changes/<change-id>/` exists on `origin/main`; `uv sync --extra dev` and `npm ci` run in the worktree | All true, else stop and say which |
| 3 | Branch | `git checkout -b <type>/<change-id>` from `origin/main` | On the feature branch |
| 4 | Read | The Issue, the change's `proposal.md`, `specs/`, `tasks.md`, then `docs/factory/LESSONS.md`; `settled-architecture` before adding anything new | Every scenario mapped to a planned test |
| 5 | Plan | `superpowers:writing-plans` from `tasks.md` | Short plan, one step per task |
| 6 | Build | `superpowers:test-driven-development` per task; tick each `tasks.md` checkbox when its verification passes | All build tasks ticked |
| 7 | Test | `factory:test` | All gates green |
| 8 | Archive | `openspec-archive-change <change-id>` | Change moved to `openspec/changes/archive/`, `openspec/specs/` updated |
| 9 | PR | `git merge origin/main` and rerun `factory:test` if it changed anything; PR template filled (every "How verified" line with its evidence, the Safety section when generation, review or publishing changed), `Closes #<n>`, Conventional Commit title (commitizen builds `CHANGELOG.md` from it, so no changelog edit); `superpowers:verification-before-completion`; then push once and do not push again until the first CI run has finished (a new push cancels it) | PR open, first CI run green (`CI Success`) |
| 10 | Review | `factory:review <pr>` | No blocking findings, or they are fixed and step 7 rerun |
| 11 | Stop | Report the PR link | Abhishek merges. Then run any post-merge task in `tasks.md`, comment the result on the Issue, and run `factory:retro` only if 10 merged PRs now have no row in `~/cantastorie-factory/metrics.csv` (a batch, then `factory:improve`); otherwise the PR waits for the next batch |

Denied, do not try: `openspec-apply-change`, Matt `tdd`, Matt `diagnosing-bugs` (use `superpowers:systematic-debugging`). Never merge, never push to `main`.
