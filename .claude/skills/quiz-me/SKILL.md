---
name: quiz-me
description: Use when Abhishek says "quiz me" or asks to test or refresh his mental map of the Cantastorie codebase, its specs, ADRs, factory or recent changes. Runs a 5 question spaced-repetition quiz grounded in the repo and logs weak topics.
---

# Quiz me (Cantastorie)

Keeps Abhishek's mental map of Cantastorie current. One question at a time, real code with `file:line`, honest scores, spaced repetition. The log lives outside the repo, so a quiz never needs a PR.

## 1. Load state

- Log: `~/cantastorie-factory/quiz-log.md`. If it does not exist, create the folder and the file from the template below. Never put it in the repo.
- Last quiz date: the newest `### Quiz - YYYY-MM-DD` heading in the log (none means first quiz).
- What changed since then: `gh pr list --state merged --search "merged:>=<last quiz date>" --json number,title,files --limit 20`.

## 2. Pick 5 questions

Topics and where to read before asking:

| Topic | Read first |
|---|---|
| `recent-prs` | Merged PRs since the last quiz: titles, diffs (`gh pr diff <n>`), CHANGELOG.md |
| `specs-adrs` | `openspec/specs/`, open `openspec/changes/`, `docs/adr/` (all of `docs/adr/`) |
| `code-structure` | `src/api/`, `src/pipeline/`, `src/workshop/`, `src/config.py`, `src/static/`, `tests/` |
| `product-safety` | `docs/product.md`, `docs/architecture.md`, the parent review gate, the safety judges |
| `factory-ops` | FACTORY.md, CLAUDE.md, `.claude/settings.json`, `.github/workflows/ci.yml`, `render.yaml`, the `render-deploy` failure table |

Priority, highest first:
1. Topics never quizzed (baseline).
2. Topics overdue: today is past `next_due`, lowest score first.
3. `recent-prs` whenever anything merged since the last quiz.
4. Fill the rest from the lowest scores.

Spread the 5 across at least 3 topics. Say in one line why you picked them ("factory-ops scored 4 eight days ago and is overdue").

## 3. Ask, one at a time

- Ask one question, wait for the answer, then grade it before the next one.
- Read the source before asking and again before grading. Never quiz from memory.
- Vary the types: conceptual ("why does pending content get its own private R2 bucket?"), code reading ("what does `src/config.py` do when `R2_ENDPOINT_URL` is set?"), location ("where is the health route defined?"), failure ("a deploy is green but the parent page shows no staged story: cause?"), change impact ("what else must change if we add an env var?").
- Probe mental models, not trivia. No line-number recall questions.
- Grade each answer: right, partly, or wrong, then give the correct answer in two or three sentences with `file:line` evidence.

## 4. Score and log

Score each topic quizzed, 1 to 10:
- 1 to 3: can't recall, revisit the source together
- 4 to 5: vague, partial
- 6 to 7: solid, minor gaps
- 8 to 9: strong, handles edge cases
- 10: could teach it

Next review from the new score: 1 = 2 days, 2 = 3 days, 3 = 5 days, 4 = 8 days, 5 = 13 days, 6 = 21 days, 7 = 34 days, 8 = 55 days, 9 = 89 days, 10 = 144 days.

Update the topic table and append the session to the log, then show the updated table. Finish with the weakest topic and one file to read before next time.

## Log template

```markdown
# Cantastorie quiz log

| Topic | Score | Last quizzed | Next due |
|---|---|---|---|
| recent-prs | - | - | - |
| specs-adrs | - | - | - |
| code-structure | - | - | - |
| product-safety | - | - | - |
| factory-ops | - | - | - |

## History
```

Each session appends:

```markdown
### Quiz - YYYY-MM-DD
1. [topic] Q: ... A: (what the answer showed) -> right | partly | wrong
...
Scores: factory-ops 4 -> 6, ...
```
