# Factory experiments

One change at a time, kept only when its metric improves. Opened and closed by `factory:improve` (FACTORY.md "Flywheel"). Metric names are the columns of `~/cantastorie-factory/metrics.csv`; values are medians over 5 retros (`escaped_defects` and `review_misses` are sums).

Flywheel start: PRs merged after the factory skills PR (branch `chore/factory-skills`, PR 2 of 5) count; older PRs are never backfilled. `factory:retro` resolves the branch with `gh pr list --repo darth-dodo/cantastorie --state merged --head chore/factory-skills --json number,mergedAt`.

Last improve run: never

Status: `running` (next 5 retros pending), `kept` (target met or metric improved), `reverted` (not improved, revert PR merged).

| id | started | change | metric | baseline | target | status | result |
|---|---|---|---|---|---|---|---|

## Candidates

Added by `factory:ask` (Experiment route), picked up by `factory:improve` step 3.

| date | change | metric | hypothesis | words |
|---|---|---|---|---|
