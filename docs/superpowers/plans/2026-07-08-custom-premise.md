# Custom Premise Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional free-text `premise` that flows into the writer prompt so the pipeline can author a *specific* story, without touching the locked `Theme` enum.

**Architecture:** Thread an optional `premise: str | None = None` through the existing authoring seams (`cli.generate → generate_story → author_story → write_story`), mirroring how the injectable `*_model` params already work. The premise is appended to the write prompt and — only when present — added to the write step's cache inputs and `derive_story_id` hash, so a premised run stages to its own folder while every no-premise run stays byte-identical to today. Revise, safety, and content gates are unchanged.

**Tech Stack:** Python 3.12, pydantic-ai (writer/judge doubles: `TestModel`, `FunctionModel`), Typer CLI, pytest. All tests are zero-network.

## Global Constraints

- Python `>=3.12`; run tests with `uv run pytest`.
- `premise=None` MUST reproduce today's behavior exactly — same prompt, same cache key, same `derive_story_id` output (existing on-disk caches preserved). Achieve this by adding the `"premise"` key to the write-inputs dict **only when premise is not None**.
- `premise` is **keyword-only** on `generate_story`, `author_story`, `write_story`, and a keyword-or-positional default on `_write_inputs` / `derive_story_id`.
- Revise step is NOT modified — the failed candidate already carries the plot.
- Story is tagged under the existing theme `gentle_forest_friends`; no new theme, no `Theme` enum change.
- Pre-commit runs ruff + mypy on commit; all new code must be typed and lint-clean.

---

### Task 1: `write.py` — premise in prompt, cache inputs, and story id

**Files:**
- Modify: `src/pipeline/steps/write.py` (`_write_inputs`, `derive_story_id`, `write_story`)
- Test: `tests/pipeline/test_authoring_steps.py`

**Interfaces:**
- Consumes: existing `Settings`, `ArtifactCache`, `StoryDraft`, `DraftModel` test double (in `test_authoring_steps.py`).
- Produces:
  - `_write_inputs(theme: Theme, language: Language, settings: Settings, premise: str | None = None) -> dict[str, object]`
  - `derive_story_id(theme: Theme, language: Language, settings: Settings, premise: str | None = None) -> str`
  - `write_story(theme, language, settings, cache, *, model: Model | None = None, premise: str | None = None) -> Story`

- [ ] **Step 1: Write the failing tests**

Add to `tests/pipeline/test_authoring_steps.py`. First extend the write import (line 29) to include `derive_story_id`:

```python
from src.pipeline.steps.write import (
    StoryDraft,
    derive_story_id,
    story_from_draft,
    write_story,
)
```

Then add these three tests after `test_rerunning_write_with_unchanged_inputs_makes_zero_model_calls` (after line 160):

```python
def test_write_threads_the_premise_into_the_writer_prompt(tmp_path: Path) -> None:
    """Given a premise,
    When the write step runs,
    Then the premise text reaches the writer model's prompt."""
    model = DraftModel(good_draft())
    write_story(
        "the_sleepy_sea",
        "it",
        _settings(),
        _cache(tmp_path),
        model=model,
        premise="A little boat counts the stars.",
    )
    assert any("A little boat counts the stars." in seen for seen in model.seen_prompts)


def test_premise_changes_the_story_id_so_runs_do_not_collide() -> None:
    """Given the same theme and language,
    When the premise differs (or is absent),
    Then derive_story_id yields a distinct id for each — no cache collision."""
    settings = _settings()
    plain = derive_story_id("the_sleepy_sea", "it", settings)
    one = derive_story_id("the_sleepy_sea", "it", settings, premise="boat counts stars")
    two = derive_story_id("the_sleepy_sea", "it", settings, premise="bear bakes bread")
    assert len({plain, one, two}) == 3


def test_no_premise_leaves_the_story_id_unchanged() -> None:
    """Given premise is None,
    When derive_story_id runs,
    Then the id equals the pre-premise id — existing caches are preserved."""
    settings = _settings()
    assert derive_story_id("the_sleepy_sea", "it", settings, premise=None) == derive_story_id(
        "the_sleepy_sea", "it", settings
    )


def test_rerunning_write_with_the_same_premise_makes_zero_model_calls(
    tmp_path: Path,
) -> None:
    """Given a premised story already persisted,
    When write runs again with the same premise,
    Then the model is never re-invoked — the artifact is served from disk."""
    model = DraftModel(good_draft())
    cache = _cache(tmp_path)
    first = write_story(
        "the_sleepy_sea", "it", _settings(), cache, model=model, premise="Same premise."
    )
    second = write_story(
        "the_sleepy_sea", "it", _settings(), cache, model=model, premise="Same premise."
    )
    assert first == second
    assert model.calls == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/pipeline/test_authoring_steps.py -k "premise or story_id" -v`
Expected: FAIL — `write_story() got an unexpected keyword argument 'premise'` and `derive_story_id()` takes 3 positional args.

- [ ] **Step 3: Implement premise support in `write.py`**

Replace `_write_inputs` and `derive_story_id` (lines 87–102) with:

```python
def _write_inputs(
    theme: Theme, language: Language, settings: Settings, premise: str | None = None
) -> dict[str, object]:
    inputs: dict[str, object] = {
        "theme": theme,
        "language": language,
        "model": settings.write_model,
        "prompt_version": PROMPT_VERSION,
    }
    # Only when present, so a no-premise run keeps its pre-premise cache key.
    if premise is not None:
        inputs["premise"] = premise
    return inputs


def derive_story_id(
    theme: Theme, language: Language, settings: Settings, premise: str | None = None
) -> str:
    """The story's stable id — a slug plus a hash of the write inputs.

    Deterministic from theme + language + writer model (+ premise when given),
    so the CLI can name the working folder content/{story-id}/ before the story
    is written.
    """
    return f"{theme.replace('_', '-')}-{language}-{cache_key(_write_inputs(theme, language, settings, premise))[:8]}"
```

Replace `write_story` (lines 105–127) with:

```python
def write_story(
    theme: Theme,
    language: Language,
    settings: Settings,
    cache: ArtifactCache,
    *,
    model: Model | None = None,
    premise: str | None = None,
) -> Story:
    """Author a native-language story; unchanged inputs cost zero API calls.

    An optional premise steers the plot; when given it also distinguishes the
    cache key and story id, so a premised run never reuses a plain-theme story.
    """
    llm = model if model is not None else build_model(settings.write_model, settings)
    inputs = _write_inputs(theme, language, settings, premise)
    story_id = derive_story_id(theme, language, settings, premise)

    def produce() -> bytes:
        prompt = (
            f"Write a bedtime story in {LANGUAGE_NAMES[language]} "
            f"on the theme: {theme.replace('_', ' ')}."
        )
        if premise is not None:
            prompt += f"\nFollow this premise closely:\n{premise}"
        draft = build_write_agent(llm).run_sync(prompt).output
        story = story_from_draft(draft, story_id=story_id, theme=theme, language=language)
        return story.model_dump_json().encode()

    return Story.model_validate_json(run_step(cache, "write", inputs, produce))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/pipeline/test_authoring_steps.py -v`
Expected: PASS (all existing write tests plus the four new ones).

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/steps/write.py tests/pipeline/test_authoring_steps.py
git commit -m "feat: optional premise in the write step

premise appended to the writer prompt; when present it also joins the
write cache inputs and derive_story_id hash, so a premised run stages to
its own folder and a no-premise run stays byte-identical to today."
```

---

### Task 2: Thread premise through `author_story` and `generate_story`

**Files:**
- Modify: `src/pipeline/steps/revise.py` (`author_story`)
- Modify: `src/pipeline/generate.py` (`generate_story`)
- Test: `tests/pipeline/test_authoring_steps.py`, `tests/pipeline/test_generate.py`

**Interfaces:**
- Consumes: `write_story(..., premise=...)` and `derive_story_id(..., premise)` from Task 1.
- Produces:
  - `author_story(theme, language, settings, cache, *, write_model=None, safety_model=None, revise_model=None, premise: str | None = None) -> tuple[Story, SafetyReport]`
  - `generate_story(theme, language, settings, *, write_model=None, safety_model=None, revise_model=None, narration_client=None, image_transport=None, premise: str | None = None) -> Path`

- [ ] **Step 1: Write the failing tests**

Add to `tests/pipeline/test_authoring_steps.py` (after the Task 1 tests). `DraftModel`, `JudgeModel`, `report_args`, `good_draft`, `author_story` are already available in that file:

```python
def test_author_story_forwards_the_premise_to_the_writer(tmp_path: Path) -> None:
    """Given a premise passed to author_story,
    When the authoring loop runs and the first draft passes every gate,
    Then the premise reached the writer model's prompt."""
    writer = DraftModel(good_draft())
    judge = JudgeModel(report_args())
    author_story(
        "the_sleepy_sea",
        "it",
        _settings(),
        _cache(tmp_path),
        write_model=writer,
        safety_model=judge,
        premise="Bruno bear has a birthday.",
    )
    assert any("Bruno bear has a birthday." in seen for seen in writer.seen_prompts)
```

Add to `tests/pipeline/test_generate.py` (after `test_rerunning_generate_reproduces_an_identical_staged_story`, line 201):

```python
def test_a_premise_stages_the_story_under_its_own_folder(tmp_path: Path) -> None:
    """Given the same theme and language,
    When one run has a premise and one does not,
    Then they stage to different working folders — no collision."""
    settings = _settings(tmp_path)

    def run(premise: str | None) -> Path:
        return generate_story(
            "the_sleepy_sea",
            "it",
            settings,
            write_model=TestModel(custom_output_args=_GOOD_DRAFT),
            safety_model=TestModel(custom_output_args=_PASSING_REPORT),
            revise_model=TestModel(custom_output_args=_GOOD_DRAFT),
            narration_client=_fake_elevenlabs(),
            image_transport=_fake_images(),
            premise=premise,
        )

    plain = run(None)
    premised = run("A birthday at sea.")
    assert plain.name != premised.name
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/pipeline/test_authoring_steps.py::test_author_story_forwards_the_premise_to_the_writer tests/pipeline/test_generate.py::test_a_premise_stages_the_story_under_its_own_folder -v`
Expected: FAIL — `author_story()` / `generate_story()` got an unexpected keyword argument 'premise'.

- [ ] **Step 3a: Thread premise through `author_story`**

In `src/pipeline/steps/revise.py`, change the `author_story` signature (lines 105–114) to add `premise` and forward it to `write_story`:

```python
def author_story(
    theme: Theme,
    language: Language,
    settings: Settings,
    cache: ArtifactCache,
    *,
    write_model: Model | None = None,
    safety_model: Model | None = None,
    revise_model: Model | None = None,
    premise: str | None = None,
) -> tuple[Story, SafetyReport]:
```

Then change the `write_story` call (line 121) to forward the premise:

```python
    story = write_story(theme, language, settings, cache, model=write_model, premise=premise)
```

Leave the revise loop untouched — the failed candidate already carries the plot.

- [ ] **Step 3b: Thread premise through `generate_story`**

In `src/pipeline/generate.py`, add `premise` to the signature (after `image_transport`, line 50):

```python
    image_transport: httpx.BaseTransport | None = None,
    premise: str | None = None,
) -> Path:
```

Change `derive_story_id` (line 57) to include the premise:

```python
    story_id = derive_story_id(theme, language, settings, premise)
```

Change the `author_story` call (lines 60–68) to forward the premise:

```python
    story, _report = author_story(
        theme,
        language,
        settings,
        cache,
        write_model=write_model,
        safety_model=safety_model,
        revise_model=revise_model,
        premise=premise,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/pipeline/test_authoring_steps.py tests/pipeline/test_generate.py -v`
Expected: PASS (all existing tests plus the two new ones).

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/steps/revise.py src/pipeline/generate.py tests/pipeline/test_authoring_steps.py tests/pipeline/test_generate.py
git commit -m "feat: forward premise through author_story and generate_story

generate derives the working-folder id with the premise and forwards it
through the authoring loop to the writer; revise stays untouched."
```

---

### Task 3: CLI `--premise` option

**Files:**
- Modify: `src/pipeline/cli.py` (`generate` command)
- Test: `tests/pipeline/test_cli.py`

**Interfaces:**
- Consumes: `generate_story(..., premise=...)` from Task 2.
- Produces: `cantastorie generate --theme ... --language ... [--premise "<text>"]`.

- [ ] **Step 1: Write the failing test and fix the existing stub**

In `tests/pipeline/test_cli.py`, update the existing `fake_generate` (line 33) so it tolerates the new keyword — the CLI will now always pass `premise`:

```python
    def fake_generate(
        theme: str, language: str, settings: object, premise: str | None = None
    ) -> Path:
        seen.update(theme=theme, language=language)
        return tmp_path / "staging" / "the-sleepy-sea-it-abc12345"
```

Then add a new test after `test_generate_rejects_a_theme_outside_the_locked_set` (line 63):

```python
def test_generate_forwards_an_optional_premise(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Given a --premise,
    When generate is invoked,
    Then the premise is forwarded to the authoring run."""
    seen: dict[str, object] = {}

    def fake_generate(
        theme: str, language: str, settings: object, premise: str | None = None
    ) -> Path:
        seen.update(theme=theme, language=language, premise=premise)
        return tmp_path / "staging" / "gentle-forest-friends-en-abc12345"

    monkeypatch.setattr(cli, "generate_story", fake_generate)

    result = runner.invoke(
        app,
        [
            "generate",
            "--theme",
            "gentle_forest_friends",
            "--language",
            "en",
            "--premise",
            "Bruno bear has a surprise party.",
        ],
    )

    assert result.exit_code == 0
    assert seen["premise"] == "Bruno bear has a surprise party."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/pipeline/test_cli.py -v`
Expected: FAIL — `test_generate_forwards_an_optional_premise` fails because `--premise` is an unknown option (exit code != 0).

- [ ] **Step 3: Add the `--premise` option in `cli.py`**

In `src/pipeline/cli.py`, add a `premise` option to the `generate` command signature (after `shape`, line ~35):

```python
@app.command()
def generate(
    theme: str = typer.Option(..., help="One of the locked launch themes"),
    language: str = typer.Option(..., help="Story language: it, es, en, el, de"),
    shape: str = typer.Option("linear", help="linear or branching"),
    premise: str = typer.Option(
        "", help="Optional plot brief; steers the story beyond the theme seed"
    ),
) -> None:
```

Change the `generate_story` call (the `staged = generate_story(...)` line) to forward the premise, treating empty string as absent:

```python
    staged = generate_story(
        cast("Theme", theme),
        cast("Language", language),
        get_settings(),
        premise=premise or None,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/pipeline/test_cli.py -v`
Expected: PASS (all existing CLI tests plus the new one).

- [ ] **Step 5: Full suite + commit**

```bash
uv run pytest -q
```
Expected: all tests pass (94 prior + 7 new).

```bash
git add src/pipeline/cli.py tests/pipeline/test_cli.py
git commit -m "feat: cantastorie generate --premise for custom plots

An empty --premise is treated as absent, so generate without the flag is
unchanged; a given premise flows through to the writer prompt."
```

---

## After implementation

The birthday-bear story can then be generated (real, paid run — separate operator step) with:

```bash
uv run cantastorie generate \
  --theme gentle_forest_friends \
  --language en \
  --premise "Today is Bruno the brown bear's birthday, and he is bursting with excitement. One by one he visits his woodland friends — a small panda, a sleepy polar bear, a honey-loving sun bear, a shy spectacled bear — and each is oddly busy and hurries off without a birthday wish. Bruno's tail droops; feeling forgotten, he pads slowly home. But when he opens his door, everyone is there — lanterns, honey cake, a banner — it was a surprise all along. His friends were never too busy; they were getting everything ready. They hug, sing, and celebrate together as the moon rises and Bruno grows happily sleepy."
```
