---
name: next-task
description: Use at the start of a session, or after finishing a task, or when asked "what's next", "what should I pick up" or "what's in flight", to decide what to work on next in Cantastorie
---

# Next task

Reads GitHub Issues and PRs, the git working tree and OpenSpec changes. Repo: `darth-dodo/cantastorie`.

## 1. Gather (in parallel)

- `git status --short`, `git branch --show-current`, `git worktree list`
- `gh pr list --state open --json number,title,headRefName,statusCheckRollup,reviewDecision`
- `gh pr list --state merged --limit 5 --json number,title,closingIssuesReferences`
- `gh issue list --state open --json number,title,labels`
- `ls openspec/changes` (ignore `archive`, which is always there; every other entry is an unarchived change and should have an Issue)

## 2. Rank (first match wins)

1. **Stale loops:** an Issue still open whose PR is merged. Check its "done when" against the live app (`curl -sI https://cantastorie.onrender.com/health`), then close it with a link to the PR.
2. **Green PRs waiting for merge:** tell Abhishek. Never merge yourself; merging is the human gate.
3. **Uncommitted work** in any worktree: finish it or ask.
4. **Red PRs:** fix CI first (use superpowers:systematic-debugging).
5. **In progress:** an Issue labelled `paused` (run `factory:resume`), or with an open branch and no PR.
6. **New work:** the lowest numbered open Issue that has a "done when". No "done when" means ask, do not guess. An open OpenSpec change with no Issue means run `project-manager` to draft one.

Limits: max 2 builders in parallel, one Issue at a time per builder, stop at "done when". An Issue that touches generation, review or publishing needs the safety and review-gate check in its "done when" before it is picked up.

## 3. Report

One recommendation with the reason, then the ranked list in one line per item. For the visual version (what needs Abhishek, flow, quality), run `factory:dashboard`.
