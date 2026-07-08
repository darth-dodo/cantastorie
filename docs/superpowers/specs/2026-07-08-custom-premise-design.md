# Custom Premise for the Authoring Pipeline

**Date:** 2026-07-08
**Status:** Approved (design)
**Branch:** worktree-custom-premise

## Problem

The authoring pipeline generates a story from one of 12 *locked* themes. The
only thing that reaches the writer model is a two-to-three-word seed
(`on the theme: gentle forest friends.`); the model then invents its own plot.
There is no way to author a *specific* story. The immediate need is one story —
a bear excited for his birthday who visits his woodland friends one by one, each
seemingly too busy to wish him, grows sad and goes home, only to find a surprise
party his friends secretly planned — but the mechanism should be reusable.

## Decision

Add an **optional free-text `premise`** that carries the plot, kept *orthogonal*
to the locked `Theme` enum (chosen over adding a new theme or loosening the enum).
The story is tagged under the closest existing theme, **`gentle_forest_friends`**,
and the premise drives the actual narrative. When no premise is given, behavior is
identical to today.

## Approach

Thread an optional `premise: str | None = None` through the existing authoring
seams — the same pattern already used for the injectable `*_model` params. No new
value object (`Brief`) — YAGNI, there is exactly one field.

### Data flow

| Function | Change |
|---|---|
| `cli.generate` | New `--premise` option (optional string) → passed to `generate_story` |
| `generate_story` (`generate.py`) | New `premise` param → passed to `author_story` |
| `author_story` (`revise.py`) | New `premise` param → passed to `write_story`; **revise is untouched** |
| `write_story` (`write.py`) | New `premise` param → into `_write_inputs` **and** appended to the write prompt |
| `_write_inputs` / `derive_story_id` (`write.py`) | Include `premise` in the dict so cache key + working-folder id are distinct |

### Prompt shape

When `premise` is present, the write prompt becomes:

```
Write a bedtime story in English on the theme: gentle forest friends.
Follow this premise closely:
<premise text>
```

When `premise` is `None`, the prompt and inputs are exactly as today.

### Cache key & story id

`premise` joins `theme, language, model, prompt_version` in `_write_inputs`, so it
is part of the write step's content-addressed key and of `derive_story_id`'s hash.
A premised run stages to its own folder (e.g.
`gentle-forest-friends-en-<hash>`), never colliding with a plain
`gentle_forest_friends` run, and re-running with the same premise buys nothing.

### Why revise is untouched

The bounded 2-revision loop re-derives from the *failed story* plus the named
failures; the failed candidate already carries the plot in its pages, and revise's
only job is to fix listed failures. Passing the premise again would add surface for
no behavior change.

## Safety & content rules — unchanged

No new validation. Every candidate still clears all content limits (8 pages,
30–70 words/page, 250–600 total, 12-word sentence cap) as code, and all 9
cross-family safety verdicts, via the same bounded loop.

**Risk:** the "no one wishes him → sad → goes home" beat leans on the judge
accepting mild, kindness-resolved sadness. If it trips `no_fear_reinforcement`
twice, the run raises `StoryRejectedError` — by design. The premise wording steers
toward gentle, clearly-resolved sadness to minimize this.

## The premise text (initial)

> Today is Bruno the brown bear's birthday, and he is bursting with excitement.
> One by one he visits his woodland friends — a small panda, a sleepy polar bear,
> a honey-loving sun bear, a shy spectacled bear — and each is oddly busy and
> hurries off without a birthday wish. Bruno's tail droops; feeling forgotten, he
> pads slowly home. But when he opens his door, everyone is there — lanterns,
> honey cake, a banner — it was a surprise all along. His friends were never too
> busy; they were getting everything ready. They hug, sing, and celebrate together
> as the moon rises and Bruno grows happily sleepy.

Covers: different kinds of bears, meets friends one by one, mild sadness, surprise
party resolution, bedtime wind-down ending. Editable before the run.

## Testing

Following the existing zero-network pattern in `test_authoring_steps.py` (fake
models, no API):

- `write_story` with a `premise` includes it in the prompt and produces a valid
  typed linear story that passes the content rules.
- **Different premise → different `derive_story_id`** (and different cache key),
  so no false cache hit against a plain-theme run.
- Same premise re-run → **zero model calls** (cache proven), mirroring the
  existing rerun test.
- CLI passes `--premise` through to `generate_story` (extend `test_cli.py`).

## Out of scope

- Adding a `birthday` theme to the locked set / `docs/product.md`.
- Branching-shape authoring (writer still emits linear only).
- Passing premise into the revise step.
- Running the actual paid pipeline (separate, operator-gated step after this lands).
