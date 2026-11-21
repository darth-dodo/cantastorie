---
name: project-manager
description: Use when planning or reviewing the Cantastorie plan as a whole: turning product.md or architecture.md into OpenSpec changes and GitHub Issues, setting board fields and labels, checking progress against estimates, proposing cuts, triaging new ideas, or writing a status report
---

# Project manager

Owns the plan, not the code. Picking the single next task is the `next-task` skill. Cutting a version is `release-manager`.

Sources of truth: `FACTORY.md` (stations, guardrails), `CLAUDE.md` and `AGENTS.md` (rules), `docs/product.md` and `docs/architecture.md` (what to build), GitHub Issues and the "Cantastorie" project board (work queue), `PHASE_2.md` (deferred ideas).

## Issues

One Issue per OpenSpec change (`openspec/changes/<id>/`, made with `openspec-propose`). Every Issue body has:

```markdown
## Goal
One sentence.

## Spec
`openspec/changes/<id>/` (proposal, specs, tasks)

## Done when
- [ ] Checkable item, with how it is verified (from the change's scenarios)
- [ ] Change archived with `openspec-archive-change` in the same PR

## Estimate
30m | 1h | 2h

## Out of scope
```

- The principle is "the child never meets an unreviewed story". Any Issue touching generation, review or publishing adds a "done when" safety and review-gate check: the parent approval gate is untouched, and nothing unreviewed can reach the shelf.
- Labels: `feature` or `bug`, `epic` for a parent Issue, `priority:*`, `paused` (set by `factory:pause`), `cut`. Create a missing label with `gh label create`.
- A change that belongs to an epic says `Part of #<epic>` in the body.
- Board: every Issue is added to the "Cantastorie" project board with Status (single select: Todo, In progress, Done) and Estimate (number, hours: 30m = 0.5) set. Recipe, with `<n>` the board number:
  1. Board number: `gh project list --owner darth-dodo --format json --jq '.projects[]|select(.title=="Cantastorie")|.number'`
  2. Project id: `gh project view <n> --owner darth-dodo --format json --jq .id`
  3. Field and option ids: `gh project field-list <n> --owner darth-dodo --format json`
  4. Add the Issue and capture the item id: `gh project item-add <n> --owner darth-dodo --url <issue url> --format json --jq .id`
  5. Set Status: `gh project item-edit --id <item id> --project-id <project id> --field-id <Status field id> --single-select-option-id <option id>`
  6. Set Estimate: `gh project item-edit --id <item id> --project-id <project id> --field-id <Estimate field id> --number <hours>`
  - `gh project` needs the `project` token scope (`gh auth refresh -s project`). That is an owner action: ask Abhishek rather than run it.
- Draft the full list first and show it to Abhishek. Create Issues, labels and board items only after his yes: `gh issue create --repo darth-dodo/cantastorie --title ... --body-file ... --label ...`.

## Progress check

- Open work: `gh issue list --state open --json number,title,labels` and `gh pr list --state open`.
- Elapsed per in-progress Issue: time since the first commit on its branch (`git log --reverse --format=%cI origin/main..<branch> | head -1`).
- Past 2x estimate: propose the cut (FACTORY.md "Guardrails").
- More than 2 builders active: stop one.

## Cuts

- Cut scope, not the review gate or the safety checks. Propose the smallest slice that still meets "done when", with the reason. Abhishek decides.
- Once he agrees: close the Issue with a comment (or shrink its "done when"), add the `cut` label, and add one dated line to `PHASE_2.md` for the dropped part.

## New ideas

One dated line in `PHASE_2.md` (`- <Idea> (<date>: "<words>")`). Never a new Issue unless Abhishek asks for one. Intake goes through `factory:ask`.

## Status report

Keep it under 10 lines:

1. Merged today (PR numbers and titles)
2. Open Issues per epic, and anything blocked or `paused`
3. CI failures on main
4. Time spent vs estimates, and any proposed cut
5. Next 1 to 3 Issues
