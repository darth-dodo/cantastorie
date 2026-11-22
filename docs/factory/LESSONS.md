# Factory lessons

Lessons folded in by `factory:improve`, read by `factory:build` at step 4.

## From the end-to-end trial (2026-11-22, Issue #183, PRs #192 and #193)

1. **Pull `main` before starting a builder.** A Claude session loads skills from the checkout it starts in. A checkout behind the merge of the factory skills has no `factory:*`, and the station has to be followed by hand. An untracked file in the way of the pull (the old Linear `next-task`) blocks it silently.
2. **`mattpocock-skills:code-review` may not be installed.** When `factory:review` cannot invoke it, dispatch a fresh reviewer subagent on the same two axes (Standards, Spec) and say so in the review comment.
3. **Local Node 26 breaks vitest.** Node 26's built-in `localStorage` shadows jsdom's, and 52 JS tests fail in untouched files. CI runs Node 22. Run vitest and Playwright locally with `NODE_OPTIONS=--no-experimental-webstorage`, or use Node 22.
4. **Install the Playwright browser in a fresh environment.** `npx playwright install chromium` before the E2E gate; without it every E2E test fails at browser launch.
5. **Check free disk before heavy gates.** The full pytest run plus `docker build` filled the disk and every command then failed with `ENOSPC`. `df -h /System/Volumes/Data` first; prune Docker build cache if under about 10 GB.
6. **botocore `retries={"max_attempts": 1}` is two attempts** in legacy mode. Use `{"total_max_attempts": 1}` for a single attempt, and assert it in a test.
7. **Ruff reflows code between edits.** A scripted find-and-replace against code the formatter already rewrote silently does nothing; check that each edit applied (for example by running the new test and seeing it fail) before moving on.
