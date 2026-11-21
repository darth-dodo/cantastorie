---
name: factory:release
description: Use right after factory:improve closes a factory:retro batch, or when Abhishek asks for a Cantastorie release, a version, a tag, release notes, or asks to split unreleased changes into several releases (answered with one release at a time).
---

# factory:release

Flywheel step 3 (FACTORY.md "Flywheel"), after `factory:improve`, before `factory:recap`. A thin station: `release-manager` owns the mechanics (preconditions, SemVer, commitizen run with `uvx --from commitizen cz ...` because commitizen is a pre-commit hook and not in the project venv, `CHANGELOG.md`, the `v<version>` tag, the GitHub Release, the deploy check), all per `[tool.commitizen]` in `pyproject.toml` (`tag_format = "v$version"`). The tagging model: the release branch only changes files, and the tag goes on main's merge commit after Abhishek merges; `cz bump` never creates a tag, because a squash merge strands a tag made on the branch. This skill decides whether a release is due, gets Abhishek's names, and hands off. Never merge.

## Before every GitHub write

Run `TZ=Europe/Berlin date`. Monday to Friday, 08:00 to 17:59: do not push, open a PR, tag, create a release or comment. Keep the work local and stop with: "Stopped: GitHub writes are paused on weekdays 08:00-17:59 Berlin; rerun after 18:00 or at the weekend." Then say what is ready locally and the next command.

## 1. Is a release due?

Last tag: `git tag --sort=-v:refname` (first line, in version order). Due when either holds:

- a `feat:` PR merged since that tag: `git log <tag>..origin/main --format=%s`, a subject starting `feat` (`feat:`, `feat(scope):`, `feat!:`)
- an epic closed since that tag's date: `gh issue list --repo darth-dodo/cantastorie --label epic --state closed --json number,title,closedAt`

Neither: report "No release due: no `feat:` PR or closed epic since vX.Y.Z" and stop.

## 2. Propose, then wait

Propose to Abhishek and stop until he answers:

```
Release due: <n> PRs since vA.B.C (<k> feat:, <epic> closed)
Version: vX.Y.Z (release-manager step 2: <reason>)
Theme: "<suggestion>", or rename it
Covers: #first to #last
```

- Version comes from `release-manager` step 2 (commitizen with `major_version_zero = true`, so pre-1.0 bumps stay in 0.x). Never pick 1.0.0 unless he said so.
- The theme is Abhishek's. Offer one short suggestion from what shipped (like "Ten languages" for a release that adds the last languages); use his name if he gives one. Never "proof", never "premium".
- Asked to split ("up to #150 as one release"): one release at a time. Commitizen scopes a section only between existing tags (`cz changelog` takes a version or a tag range, not a commit; a commit end fails with "Could not find a valid revision range"), so an older untagged range cannot get its own section reliably. Say so, and propose one version covering everything since the last tag; to cut earlier next time, run `factory:release` when that range is the newest.

## 3. Release PR

Hand off to `release-manager` step 3 with the confirmed version. Its preconditions are not optional. On branch `chore/release-v<version>` from `origin/main`:

- `uvx --from commitizen cz bump --files-only --yes` updates `pyproject.toml`, `uv.lock` (through the `uv` version provider) and `CHANGELOG.md` (`update_changelog_on_bump = true` writes the `## v<version> (<date>)` section from the Conventional Commit titles), and creates no tag. Run no separate `cz changelog`: with no tag yet it would rewrite the section as `## Unreleased`. Never hand-write or reorder its entries.
- Commit, push the branch, open the release PR titled `chore(release): v<version>`.

One release per PR and per tag: no split PRs (step 2).

## 4. After Abhishek merges

Run `release-manager` step 4: take the release PR's merge commit on main, `<merge sha>` (`gh pr view <release pr> --json mergeCommit --jq .mergeCommit.oid`), and tag it with `git tag v<version> <merge sha>`, push the tag (`git push origin v<version>`), then `gh release create v<version> --title "v<version> <Theme>" --notes-file <file>`. Factory specifics:

- Release title `vX.Y.Z <Theme>`; the notes file starts `# vX.Y.Z <Theme>`, then one sentence, then the changelog section.
- Deploy check: the CI `deploy` job ran after `CI Success` on the release merge, `https://cantastorie.onrender.com/health/version` reports that commit, and the next "Post-deploy smoke check" run is green.

## 5. Report

```
Released: vX.Y.Z <Theme> <release url>
Tag: vX.Y.Z at <sha7> (merge of #<release pr>)
Deploy: <result>  Smoke: <run url>
```

When stopped early, say what is ready locally and the next command to run.

## Common mistakes

| Mistake | Instead |
|---|---|
| Naming the theme and carrying on | Suggest one, wait for Abhishek |
| Splitting unreleased changes into several versions | One release at a time, covering everything since the last tag (step 2) |
| Running `cz changelog` after the bump | The bump already wrote `CHANGELOG.md`; a second run rewrites it as `## Unreleased` |
| Letting `cz bump` tag the release branch (a squash merge strands that tag) | `uvx --from commitizen cz bump --files-only --yes` on the branch; after the merge, `git tag v<version> <merge sha>`, push the tag, `gh release create` (step 4) |
| `uv run cz ...` | Commitizen is not in the venv: `uvx --from commitizen cz ...` |
| Pushing because the PR "is just a release" | Check the Berlin time before each write; inside the window, stop with the reason |
| Hand-editing `CHANGELOG.md` entries | Commitizen builds them from the commit titles (`release-manager` step 3) |
