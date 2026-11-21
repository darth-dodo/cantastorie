---
name: factory:pause
description: Use when a Cantastorie builder must stop before its Issue is done (end of day, context or budget limit, blocked on Abhishek). Saves the work to the branch and writes a handoff on the Issue so any session can run factory:resume.
---

# factory:pause

State lives on GitHub, never only in the chat. Run git commands one per call from the worktree. Never commit to `main`, never force push.

| # | Step | Done when |
|---|---|---|
| 1 | Locate: Issue number, feature branch (`git branch --show-current`), worktree path (`pwd`), the `factory:build` step reached, ticked and open boxes in the change's `tasks.md` | All known |
| 2 | Evidence: run the current gate that matters (usually `uv run pytest`) and keep the exact failing lines, if any | Real status, not a guess |
| 3 | Save: `git add -A`, `git commit -m "wip: #<n> paused at factory:build step <s>"`, `git push -u origin <branch>` | Branch on GitHub has every change |
| 4 | Handoff comment on the Issue (template below) via `gh issue comment <n> --body-file <file>` | Comment posted |
| 5 | Label: `gh label create paused --color fbca04 --description "A builder stopped; see the handoff comment"` if missing, then `gh issue edit <n> --add-label paused` | Issue shows `paused` |
| 6 | Report the Issue link and the one next action | Done |

WIP commits are fine: squash merge removes them from `main`. Before `git add -A`, check `git status` for files that must not be committed (`.env`, `.serena/`, screenshots, scratch files); never commit them.

## Handoff template

```markdown
## Paused: factory:build step <s> of 11

- Branch: `<branch>` (pushed, last commit `<sha>`)
- Worktree: `<path>` (may be gone; resume recreates it from the branch)
- Tasks done: <ticked task ids>
- Tasks open: <open task ids>
- Gates: <green, or the failing command with its exact output lines>

**Next action:** <exactly one concrete step>

**Open questions for Abhishek:** <none, or the question>
```
