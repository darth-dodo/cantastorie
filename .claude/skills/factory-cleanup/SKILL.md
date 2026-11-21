---
name: factory:cleanup
description: Use when tidying the Cantastorie factory after work merges, or when asked to clean up worktrees, branches or leftovers. Removes only local worktrees and branches whose PR is merged and whose tree is clean; reports everything else.
---

# factory:cleanup

Local housekeeping. Never deletes unmerged or uncommitted work, never touches `main`, never force removes. Run git commands one per call (worktree sessions reject compound git commands).

## 1. Gather

- `git fetch --prune origin`
- `git worktree list --porcelain`
- `git branch --format='%(refname:short)'`
- For each branch: `gh pr list --head <branch> --state all --json number,state --jq '.[0]'`

Squash merges leave branches that `git branch --merged` does not recognise, so the PR state from GitHub is the source of truth.

## 2. Decide per item

| Item | Remove when | Command |
|---|---|---|
| Worktree under `.claude/worktrees/` | Its branch's PR is `MERGED` and `git -C <path> status --porcelain` is empty | `git worktree remove <path>` (no `--force`) |
| Local branch | PR `MERGED` (or `CLOSED` and Abhishek agreed), no worktree uses it | `git branch -D <branch>` |
| Remote branch | PR `MERGED` | List only. Delete only after Abhishek's yes: `git push origin --delete <branch>` |
| Paused work (`paused` label) | Never | Keep, mention it |
| Untracked files in the repo root (for example `.DS_Store`, screenshots, `.serena/`) | Never | Report, suggest a `.gitignore` line |
| Local `.env` | Never | Leave it; never read or print it |

Anything with uncommitted changes, an open PR, or no PR at all is kept and reported.

## 3. Report

A table: item, PR, state, action taken or kept and why. End with the one command Abhishek may want to run himself (usually the remote branch deletes).
