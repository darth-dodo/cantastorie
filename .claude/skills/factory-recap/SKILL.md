---
name: factory:recap
description: Use when Abhishek asks for a recap, status or digest of a Cantastorie session or day. Prints under 15 lines from GitHub and the local factory metrics; writes nothing.
---

# factory:recap

Flywheel step 4 (FACTORY.md "Flywheel"). Read only: never write a file, comment, label or commit. Argument: a window (default today, local time). For the visual version on one page, run `factory:dashboard`.

## Gather

| Line | Source |
|---|---|
| Merged | `gh pr list --state merged --search "merged:>=<date>" --json number,title` |
| Open Issues | `gh issue list --state open --json number,title,labels` |
| Blockers | Open Issues labelled `needs-info` or `paused` |
| Main CI | `gh run list --branch main --workflow CI --limit 1 --json conclusion,createdAt`, and the same for `--workflow "Post-deploy smoke check"` (with `status`) |
| Trend | `~/cantastorie-factory/metrics.csv`: median `lead_time_h`, `review_blockers` and share of `first_pass_green` over the last 5 rows vs the 5 before; "n/a" with fewer rows |
| New lessons | The "Lesson" line of `~/cantastorie-factory/retros/*.md` changed in the window |
| Running experiment | `running` row in `docs/factory/experiments.md` |

## Print

At most 14 lines, this shape (drop a line only if it has nothing; keep the bedtime line):

```
Recap <date>
Merged (<n>): #a title, #b title (numbers only when more than 5)
Open (<n>): #c title, #d title
Blocked: none | #e (paused)
Main: CI success 17:19, smoke success
Trend (last 5 vs previous 5): lead 0.4h vs n/a, blockers 0 vs n/a, first-pass green 100% vs n/a
Lessons: <one per line, max 3>
Experiment: none running | E<n> <change>
Bedtime: is there a new story on the shelf tonight?
```
