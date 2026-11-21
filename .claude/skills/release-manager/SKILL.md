---
name: release-manager
description: Use when cutting a Cantastorie release, bumping the version, tagging, writing GitHub release notes, or generating CHANGELOG.md with commitizen
---

# Release manager

A release is a version label on code already merged to main. The CI `deploy` job ships main after `CI Success`, not on tags, so a release never ships new code by itself.

The factory runs this skill through `factory:release`, which adds the release-due check, the theme (release title `vX.Y.Z <Theme>`) and the one-release-at-a-time rule.

Commitizen config is `[tool.commitizen]` in `pyproject.toml` (`tag_format = "v$version"`, `version_provider = "uv"`, `update_changelog_on_bump = true`, `major_version_zero = true`). Commitizen is a pre-commit hook only: it is not in the project venv and no dependency may be added. Always run it as `uvx --from commitizen cz ...`, never through `uv run`.

## 1. Preconditions (stop if any fail)

- `git fetch origin` and work from `origin/main`.
- CI on latest main is green: `gh run list --branch main --limit 1 --json conclusion,headSha`.
- There is something to release: `git log $(git tag --sort=-v:refname | head -1)..origin/main --format=%s` lists at least one Conventional Commit title.

## 2. Pick the version (SemVer, pre-1.0)

- Preview, changing nothing: `uvx --from commitizen cz bump --dry-run`. It reads the commit titles since the last tag.
- With `major_version_zero = true`: a `feat:` or a breaking change (`!` or `BREAKING CHANGE`) bumps minor (0.4.0 to 0.5.0); only `fix:`, `chore:`, `docs:`, `refactor:`, `test:` bumps patch (0.4.0 to 0.4.1).
- 1.0.0 only when Abhishek says so.
- Cross-check: every `feat:` title since the tag should appear in the preview. A PR merged with a non-conventional title will be missing; tell Abhishek rather than hand-editing.

## 3. Release PR

1. `git worktree add ../cantastorie-release-vX.Y.Z -b chore/release-vX.Y.Z origin/main`
2. `uvx --from commitizen cz bump --files-only --yes`. It updates `pyproject.toml`, `uv.lock` and `CHANGELOG.md` (the `## vX.Y.Z (date)` section, generated from the commit titles) and creates no tag. Run no separate `cz changelog`: with no tag yet it rewrites the new section as `## Unreleased`. Never hand-write or reorder entries.
3. `make check` green.
4. Commit `chore(release): vX.Y.Z`, push the branch, open the PR titled `chore(release): vX.Y.Z` with that version's changelog section as the body.
5. Stop. Abhishek merges.

One release per PR and per tag. Never split a range into several releases.

## 4. After the merge

1. Merge commit: `gh pr view <n> --json mergeCommit --jq .mergeCommit.oid`
2. Tag that commit (not the branch, because a squash merge strands a branch tag): `git tag v<version> <merge sha>`, then `git push origin v<version>`.
3. Notes: copy the version's changelog section into a scratchpad file, then `gh release create v<version> --title "v<version>" --notes-file <file>` (`factory:release` sets the title with the theme).
4. Verify the deploy (`render-deploy` "Verify a deploy"): the CI `deploy` job ran after `CI Success` on the merge commit, `curl -fsS https://cantastorie.onrender.com/health` returns 200, and `/health/version` reports the merge commit once that route exists.
5. Report: version, release URL, deploy check result.

## Never

- Tag a commit that is not on main, or retag or delete a pushed tag.
- Release with red CI on main.
- Run commitizen through `uv run`, or add it as a dependency.
- Fix a bad release by rewriting it. Fix forward with a patch release.
