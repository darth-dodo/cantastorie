---
name: factory:improve
description: Use right after each factory:retro batch (10 new rows), or when asked to improve the Cantastorie factory from its metrics. Hill climbs: one experiment PR at a time, kept or reverted on the numbers, and folds new lessons into docs/factory/LESSONS.md.
---

# factory:improve

Flywheel step 2 (FACTORY.md "Flywheel"). Inputs: `~/cantastorie-factory/metrics.csv`, `~/cantastorie-factory/retros/*.md`, `docs/factory/experiments.md`, `docs/factory/LESSONS.md`. Every repo change goes through a PR; never edit a skill, allowlist or CI file without one. Never merge.

## When

Run right after each `factory:retro` batch (every 10 merged PRs, counted from the `Flywheel start` line in `experiments.md`, which `factory:retro` step 0 resolves to the merged `chore/factory-skills` PR). The trigger: `metrics.csv` has 5 rows with `merged_at` after the `Last improve run` line in `experiments.md` (`never`: the first 5 rows ever). Fewer than 5: stop and say how many are missing. Every run, including a close-only one, updates that line to the newest `merged_at` it used, in the same PR.

## 1. Close the running experiment

If `experiments.md` has a `running` row, compare its metric: median over the 5 retros since the experiment PR merged vs the row's `baseline`.

- Improved (moved toward `target`): set status `kept` and write the measured value in `result`.
- Not improved: open a revert PR (`git revert` of the experiment's squash commit, title `revert: experiment E<n> ...`), set status `reverted` and write the measured value and the revert PR in `result`.

Record the result in the same PR as step 3, or in its own `chore(factory): close experiment E<n>` PR if there is no new experiment.

## 2. Pick the worst metric

Medians of the last 5 rows vs the 5 before. With only 5 rows there is nothing to compare: pick the metric furthest from its ideal (0, or 100% for `first_pass_green` and 1.00 for `review_precision`). Metrics, and which way is better:

| Column | Better |
|---|---|
| `lead_time_h` | lower |
| `first_pass_green` (share of `true`) | higher |
| `ci_red_runs` | lower |
| `gate_reds` | lower |
| `review_blockers` | lower |
| `permission_prompts` | lower |
| `denied_calls` | lower |
| `escaped_defects` (sum over the 5 rows, not median) | lower |
| `review_precision` | higher |
| `review_misses` (sum over the 5 rows, not median) | lower |

`issue`, `pr`, `merged_at` and `estimate_h` are context (an experiment may still cite `estimate_h`); they are not scored. Skip a metric that is blank in more than 2 of the rows used (`permission_prompts` competes only once retros can fill it from the transcripts). The worst metric is the one that moved most in the bad direction, or if none moved, the furthest from its ideal.

## 3. One experiment PR

- Prefer a row in the `## Candidates` list of `experiments.md` (added by `factory:ask`) whose metric is the worst metric; use its change and hypothesis, and remove its row in the same PR.
- One change only (a skill, `.claude/settings.json` allowlist or CI edit), with a hypothesis: "Allowing `git push` for feature branches cuts `permission_prompts` from 6 to 2."
- Never an experiment that weakens the parent review gate, the child-path privacy rules or a CI safety job (Security Scan, R2 Bucket Audit): the child never meets an unreviewed story, whatever the metric says.
- Branch `chore/factory-experiment-e<n>`, title `chore(factory): experiment E<n> <change>`.
- Append a row to the table in `docs/factory/experiments.md`: `| E<n> | <date> | <change> | <metric> | <baseline median> | <target> | running | #<pr> |`.
- Fold the period's retro lessons into `docs/factory/LESSONS.md` under their station, deduplicated, each citing its PR, under 40 rules (merge or drop the weakest to stay under).
- Conventional Commit title (commitizen builds the changelog from it), `factory:test`, `factory:review`.

## 4. Close the batch

Run `factory:release` to check whether a release is due, then stop for Abhishek.

## Rules

- Never two `running` experiments at once.
- Never keep an experiment without its 5 follow-up retros.
- A kept change stays; a reverted one is not retried without a new hypothesis.
