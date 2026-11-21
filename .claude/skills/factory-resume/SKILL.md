---
name: factory:resume
description: Use when picking up paused Cantastorie work, or when a new session should continue an Issue another session started. Restores the worktree from the pushed branch, re-verifies the real state, then continues factory:build from the recorded step.
---

# factory:resume

Trust evidence, not the handoff: re-run the gates before continuing. Run git commands one per call from the worktree.

| # | Step | Done when |
|---|---|---|
| 1 | Pick: the Issue given, or `gh issue list --label paused --json number,title`; if several, ask which | One Issue |
| 2 | Read the latest "Paused" comment: `gh issue view <n> --comments` | Branch, step, next action, open questions known |
| 3 | Open questions unanswered? Ask Abhishek first | Answered or none |
| 4 | Workspace: if the recorded worktree exists, enter it; otherwise `git fetch origin` and `git worktree add .claude/worktrees/<n> <branch>`, then enter it and run `uv sync --extra dev` and `npm ci` | In a worktree on the branch |
| 5 | Catch up: `git pull`; if `origin/main` moved, `git merge origin/main` (never rebase, force push is denied) | Branch current, conflicts resolved |
| 6 | Re-verify: `factory:test`; compare with the handoff's gates | Actual state known; differences noted |
| 7 | Unlabel: `gh issue edit <n> --remove-label paused`; comment "Resumed at step <s>" | Issue shows work is active |
| 8 | Continue `factory:build #<n>` from the recorded step (earlier if step 6 found new reds) | Back in the normal flow |

Worktrees share the machine: give each one its own `E2E_PORT=<free port>` for `npx playwright test`, never the port another builder's run is using.
