---
name: factory:dashboard
description: Use when Abhishek asks to see the factory, a dashboard, or "what needs me". Rebuilds one private page from live sources each run (GitHub, CI, the live app, ~/cantastorie-factory/metrics.csv, experiments.md), "Needs Abhishek" first; writes nothing to the repo or GitHub.
---

# factory:dashboard

The visual version of `factory:recap`. Read only (apart from `git fetch` updating remote-tracking refs): never write a comment, label, Issue, commit or repo file. Gather fresh every run; never reuse numbers from an earlier page.

## 1. Gather (in parallel, note the time of each read)

| Panel | Command or file |
|---|---|
| Open PRs and CI | `gh pr list --state open --json number,title,headRefName,isDraft,statusCheckRollup,reviewDecision` |
| Open Issues | `gh issue list --state open --limit 100 --json number,title,labels` (epics are the `epic` label) |
| Board | The "Cantastorie" project: find its number, then list its items (Status, Estimate per item); commands under the table |
| Merged per day | `gh pr list --state merged --limit 100 --json number,mergedAt`, counted per day for the last 7 days |
| Main CI | `gh run list --branch main --workflow CI --limit 1 --json databaseId,conclusion,createdAt,url` |
| Smoke | `gh run list --workflow "Post-deploy smoke check" --limit 3 --json databaseId,conclusion,status,createdAt,url`, then read the run log (`gh run view <id> --log`) and keep only the lines naming each checked path (`/health`, `/play`, `/health/version`) with its HTTP code or failure |
| Missing secrets | `RENDER_DEPLOY_HOOK_URL is not set` in the `Deploy to Render` step of the main CI log; a token error in the `codecov/codecov-action` steps of the main CI log (command under the table) |
| Deploy lag | `curl -s --max-time 10 https://cantastorie.onrender.com/health/version` (its `commit`) vs `git rev-parse origin/main` (after `git fetch origin`), and `git log --oneline <live>..origin/main` |
| Flywheel | `~/cantastorie-factory/metrics.csv`: medians of `lead_time_h`, `ci_red_runs`, `review_blockers`, `denied_calls`, `review_precision`, share of `first_pass_green`, sums of `escaped_defects` and `review_misses`, last 5 rows vs the 5 before ("n/a" under 10 rows; skip empty cells) |
| Experiment | The `running` row of `docs/factory/experiments.md` (id, change, metric, baseline, target) |
| Running builders | `git worktree list`; a worktree on a `feature/`, `feat/`, `fix/`, `chore/`, `docs/`, `refactor/`, `test/` or `spec/` branch with no PR (`gh pr list --head <branch> --state all`) is a running builder; skip `main`, detached heads and other branches |

Commands too long for a table cell:

```bash
gh project list --owner darth-dodo --format json --jq '.projects[] | select(.title=="Cantastorie") | .number'
gh project item-list <number> --owner darth-dodo --format json --limit 200
gh run view <id> --log | grep -i codecov | grep -i token
```

A failed command does not stop the page: its panel says what failed and the command, the rest still render. Until `/health/version` or the smoke workflow exists on `main`, their panels say "not deployed yet" with the command that failed.

## 2. Page order

1. **Needs Abhishek**: green PRs to merge (not draft, at least one check, every check's `conclusion` or `state` is `SUCCESS`, `SKIPPED` or `NEUTRAL`), decisions waiting (Issues labelled `needs-info`, `ready-for-human`, or `paused` with open questions in the handoff), missing secrets (the secret's name and the run link, never a value), the latest smoke problem if any (run link and HTTP code only).
2. **Flow**: open PRs with CI state, open Issues by epic and label, board Status and Estimate counts, merged PRs per day (last 7 days), deploy lag (live SHA, `origin/main`, commits between), running builders.
3. **Quality**: the flywheel trend, last 5 vs previous 5, with the direction of each metric.
4. **Experiment**: the running row and its target.

Every number shows its source (command or file) and the read time, for example `3 (gh pr list, 21:40)`. Name the page "Cantastorie factory". Inclusive language, no em or en dashes.

## 3. Publish

Write the gathered rows to a scratch file (never in the repo) as `{"rows": [...], "meta": [...]}`, one row per line on the page: `panel` (`needs`, `flow`, `quality` or `experiment`), `item` (unique), `value`, `detail`, `source`, `read_at`.

1. **Rerun** (`~/cantastorie-factory/dashboard-url` holds a URL): upload the new JSON to that Artifact (`asset: true`) and repoint the `factory` file source at it as the type's instructions say, then delete the old upload. The page is already there; leave it alone.
2. **First run**: if the Artifact tool's `quickstart` (intent `other`) lists the Data type, start from it with the title "Cantastorie factory", follow its instructions to write `dash/meta`, `datasets/factory` (a `file` source, `fixed: true`) and `files/index.html` (four tables in the page order, `data-source="factory"`, `data-row-key="item"`), and save the URL to `~/cantastorie-factory/dashboard-url`.
3. **No Artifact tool or no Data type**: write one HTML page from the same rows under `mktemp -d`, run `open <file>`, print the path.

Print the URL or path and the "Needs Abhishek" items as the reply.

## Never include

Secret values, `.env` contents, transcripts, story text, family tokens, Clerk user ids, cookies, or anything from `gh secret`. Never open `.env`.
