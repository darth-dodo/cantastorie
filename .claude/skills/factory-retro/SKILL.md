---
name: factory:retro
description: Use when 10 merged Cantastorie PRs have no row in ~/cantastorie-factory/metrics.csv (checked at factory:build step 11), or when backfilling, to record each Issue's metrics row and one lesson in ~/cantastorie-factory/. Runs in batches of 10. Writes nothing in the repo.
---

# factory:retro

Flywheel step 1 (FACTORY.md "Flywheel"). Argument: none (a batch) or merged PR numbers. Raw data lives outside the repo, so a retro needs no PR:

- `~/cantastorie-factory/metrics.csv`: one row per Issue. Create the folder and the file with the header below if missing.
- `~/cantastorie-factory/retros/<issue>.md`: the retro file.

Rerunning for the same Issue replaces its row and file; never add a second row.

## 0. Batch

Retros run in batches of 10 merged PRs (FACTORY.md "Flywheel"), not after each merge. Counting starts at the first PR merged after the factory skills PR: the `Flywheel start` line in `docs/factory/experiments.md` names it, and older PRs are never backfilled. List merged PRs (`gh pr list --state merged --limit 50 --json number,mergedAt`), drop those at or before the `Flywheel start` PR, and drop those whose number is in the `pr` column of `metrics.csv`. Fewer than 10 left: stop and say how many more merges the batch needs. 10 or more: run steps 1 to 4 for every one of them, oldest first, in this run.

## 1. Gather

| Value | Source |
|---|---|
| PR facts | `gh pr view <pr> --json number,headRefName,mergedAt,closingIssuesReferences,commits,body,comments` |
| Issue and estimate | `gh issue view <issue> --json body` ("Estimate" section) |
| CI runs | `gh run list --branch <headRefName> --event pull_request --workflow CI --json conclusion,createdAt` (CI only; other workflows do not count) |
| Transcript | Lines of `~/.claude/projects/<project>/*.jsonl` (and `*/subagents/*.jsonl`) whose `cwd` is the builder's worktree, up to `mergedAt` |

## 2. Metrics row

Header (exact order): `issue,pr,merged_at,estimate_h,lead_time_h,first_pass_green,ci_red_runs,gate_reds,review_blockers,permission_prompts,denied_calls,escaped_defects,review_findings,findings_acted,review_precision,review_misses`

| Column | Rule |
|---|---|
| `issue` | First closing Issue number |
| `pr` | PR number |
| `merged_at` | `mergedAt`, ISO 8601 UTC |
| `estimate_h` | Issue estimate in hours (30m = 0.5) |
| `lead_time_h` | Earliest branch commit `committedDate` to `mergedAt`, hours, 2 decimals |
| `first_pass_green` | `true` if the oldest CI run on the PR concluded `success`, else `false` |
| `ci_red_runs` | CI runs on the PR with conclusion `failure` |
| `gate_reds` | Red gates in the PR body's `factory:test` evidence ("red", "failed", nonzero exit), counting each fixed red once |
| `review_blockers` | Items under "Blocking" in the `factory:review` PR comment; "None" is 0 |
| `permission_prompts` | Best effort: tool calls in the transcript window that waited on a human approval. Blank if the transcript does not show them |
| `denied_calls` | Best effort: `is_error` tool results in the window saying denied, refused or "too complex to verify". Blank if no transcript |
| `escaped_defects` | Production bugs found after merge that trace to this PR (a `bug` Issue, a fix PR, a Sentry event, a smoke check or log finding). 0 when the evidence shows no production bug; blank while the PR's own production check (deploy check, post-deploy smoke check) is still open. When a later bug traces to an older PR, update that PR's row |
| `review_findings` | Rows in the `factory:review` comment's Findings table (step 3). Blank if the PR has no review comment |
| `findings_acted` | Findings classed `acted_tested` or `acted_untested` (step 3) |
| `review_precision` | `acted_tested` / `review_findings`, 2 decimals. Higher is better. Blank when `review_findings` is 0 or blank |
| `review_misses` | Escaped defects of this PR that no finding flagged (step 3). Lower is better. 0 when `escaped_defects` is 0; blank while `escaped_defects` is blank |

Blank means unknown, never 0. Never guess a number.

## 3. Score the review

The reward for a review is decided here, after merge, from what the code did, never by the reviewer. Read the `factory:review` comment(s); a comment without the Findings table: number its findings F1, F2, ... in list order. For each finding id:

| Class | Rule |
|---|---|
| `acted_tested` | A commit after the review ran (or one the comment names as the fix) changed that `file:line`, and a test was added or changed for it that fails without the change. When the finding is a missing test, the added test is the change. Check fail-before by reverting the fix locally when cheap; otherwise say "fail-before not rerun" |
| `acted_untested` | That `file:line` changed for the finding, with no test (docs, workflows, CSS, templates with no test) |
| `dismissed` | Not changed in this PR: kept, deferred, or an observation |

For each escaped defect of the PR, name the finding id that flagged its cause (`flagged`) or `none` (`missed`, counted in `review_misses`). When a later bug updates an older PR's `escaped_defects`, update its `review_misses` too.

## 4. Retro file

```markdown
# Retro #<issue> (PR #<pr>)
<header line>
<metrics row>

## Went well
- ...
## Went wrong
- ...
## Review findings
- F1 acted_tested (<test file>), F2 dismissed, ...
- Escaped defect <Issue or PR>: flagged by F<n> | missed
## Lesson
Do X when Y, because Z. (#<pr>)
## Candidate experiment
One change, the metric it should move, and from what to what.
```

Ground every line in the gathered evidence (PR body, review comment, CI, transcript). One lesson only. Never copy a child's story text, a family token or a secret into a retro.

## 5. Report

Print each row and its lesson. A batch always leaves at least 5 rows merged after the `Last improve run` line in `docs/factory/experiments.md`, so say that `factory:improve` is due now and run it right after; it ends by running `factory:release`.
