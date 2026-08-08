# Branching Stories Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The pipeline authors branching stories (one choice point, two arms, spoken option labels, watercolor choice cards) and the player actually follows the tapped branch — closing the gap between the shipped choice *overlay* and real branch-following.

**Architecture:** The `story.json` contract already models branching (`Story.shape`, `Page.choice`, `ChoicePoint`, `ChoiceOption.card_image`, `next_page` links) — this plan makes the pipeline *produce* it and the player *honor* it. Pipeline side: a `BranchingStoryDraft` writer output, per-heard-path content validation, label narration, option card illustration, and assembly of the new assets. Player side: the loaded story gains a `pagesFrom(pageId)` walker, and a tapped option extends the playable path with the chosen arm *before* the store advances — the store's index-based state machine is untouched.

**Tech Stack:** Python 3.12 / FastAPI / Pydantic AI over OpenRouter (pipeline); vanilla ES modules + Vitest + jsdom (player); pytest + moto (pipeline tests); Playwright (E2E).

**Tracking:** Linear AI-428. Branch: `feature/branching-stories` (create from `origin/main`; this plan lives on `docs/branching-stories-plan`).

## Global Constraints

- Run Python tests with `uv run pytest`; JS tests with `npm test` (Vitest). New worktrees need `uv sync --extra dev` first.
- All limits come from `src/pipeline/content_rules.py` constants (`PAGE_COUNT`, `PAGE_WORDS_MIN/MAX`, `STORY_WORDS_MIN/MAX`, `SENTENCE_WORDS_MAX`). **Never hardcode 8, 10, 12, or 20** — docs/product.md says 8 pages/12-word cap while the constants say 10/20; that conflict is resolved elsewhere (noted in AI-428), and deriving from constants makes the resolution a one-line change.
- `story.json` stays `schema_version: 1` — every contract change in this plan is additive and optional (`ChoiceOption.audio`), so published linear stories remain valid.
- One choice point per branching story at launch (YAGNI). Validation is written against *enumerated heard paths* so multiple choice points validate correctly later, but the writer only produces one.
- Arm length is a new constant `ARM_PAGES = 4`; shared prefix is `PAGE_COUNT - ARM_PAGES` pages. Every heard path is exactly `PAGE_COUNT` pages (product rule: "every heard path is 8 pages and stays within the linear totals").
- A page with a `choice` has `next_page = None` — continuations live only on the options. Arm-final pages also have `next_page = None`.
- The pipeline's only transport is OpenRouter via `src/pipeline/providers.py`; every generated artifact goes through `ArtifactCache` (`run_step`/`cache_key`) so unchanged inputs cost zero API calls.
- Conventional commits (`feat:`, `test:`, `fix:`); commit after every green test cycle. Pre-commit runs ruff/mypy — keep them green.
- The store (`src/static/js/store.js`) stays pure: no DOM, no audio, no story data. Path data lives in `main.js`/`playback.js`.

---

### Task 1: Heard-path enumeration and branching content rules

**Files:**
- Modify: `src/pipeline/models.py:69-76` (ChoiceOption gains `audio`)
- Modify: `src/pipeline/content_rules.py`
- Test: `tests/pipeline/test_content_rules_branching.py` (new)

**Interfaces:**
- Consumes: `Story`, `Page`, `ChoicePoint`, `ChoiceOption` from `src/pipeline/models.py`; existing `check_story`, `ContentViolation`, `ContentRule`, `words`, `page_word_count` from `content_rules.py`.
- Produces: `ARM_PAGES: int = 4`; `heard_paths(story: Story) -> list[list[Page]]`; `ContentRule` literal extended with `"branch_structure"` and `"path_length"`; `check_story(story)` validating branching stories per-path. `ChoiceOption.audio: PageAudio | None = None`. Later tasks (2, 3, 7, 8) call `check_story` unchanged and rely on `heard_paths`.

- [ ] **Step 1: Add the `audio` field to `ChoiceOption`**

In `src/pipeline/models.py`, extend `ChoiceOption` (the field order matters for readability, keep `next_page` last):

```python
class ChoiceOption(BaseModel):
    label: str  # story text: counts toward every limit and the gloss map
    card_image: str | None = None
    audio: PageAudio | None = None  # the spoken label; timings stay empty
    next_page: str
```

`PageAudio` is defined above `ChoiceOption` in the same file — no import changes needed.

- [ ] **Step 2: Write a branching-story factory for tests**

Create `tests/pipeline/test_content_rules_branching.py`. Give it a module-level factory that builds a *valid* branching story derived from the constants — every test mutates a copy of this:

```python
"""Branching content rules: heard-path enumeration and per-path limits."""

from src.pipeline.content_rules import (
    ARM_PAGES,
    PAGE_COUNT,
    PAGE_WORDS_MIN,
    check_story,
    heard_paths,
)
from src.pipeline.models import ChoiceOption, ChoicePoint, Page, Story

SHARED = PAGE_COUNT - ARM_PAGES

# PAGE_WORDS_MIN short sentences of one word each satisfies every limit:
# page words >= min, sentence cap, and story totals scale with PAGE_COUNT.
PAGE_TEXT = " ".join(["dorme."] * PAGE_WORDS_MIN)


def branching_story() -> Story:
    shared = [
        Page(id=f"p{i}", text=PAGE_TEXT, next_page=f"p{i + 1}")
        for i in range(1, SHARED)
    ]
    choice_page = Page(
        id=f"p{SHARED}",
        text=PAGE_TEXT,
        next_page=None,
        choice=ChoicePoint(
            options=(
                ChoiceOption(label="la lanterna", next_page="a1"),
                ChoiceOption(label="la barchetta", next_page="b1"),
            )
        ),
    )
    arm_a = [
        Page(
            id=f"a{i}",
            text=PAGE_TEXT,
            next_page=f"a{i + 1}" if i < ARM_PAGES else None,
        )
        for i in range(1, ARM_PAGES + 1)
    ]
    arm_b = [
        Page(
            id=f"b{i}",
            text=PAGE_TEXT,
            next_page=f"b{i + 1}" if i < ARM_PAGES else None,
        )
        for i in range(1, ARM_PAGES + 1)
    ]
    return Story(
        id="test-branching",
        language="it",
        title="La prova",
        theme="the little boat",
        shape="branching",
        pages=[*shared, choice_page, *arm_a, *arm_b],
    )


def test_heard_paths_enumerates_both_arms():
    paths = heard_paths(branching_story())
    assert len(paths) == 2
    assert all(len(path) == PAGE_COUNT for path in paths)
    assert paths[0][-1].id == f"a{ARM_PAGES}"
    assert paths[1][-1].id == f"b{ARM_PAGES}"


def test_valid_branching_story_passes():
    assert check_story(branching_story()) == []


def test_short_arm_is_a_path_length_violation():
    story = branching_story()
    story = story.model_copy(
        update={"pages": [p for p in story.pages if p.id != f"a{ARM_PAGES}"]}
    )
    # a(ARM_PAGES-1) now dangles: fix its link so only the length breaks
    for page in story.pages:
        if page.id == f"a{ARM_PAGES - 1}":
            page.next_page = None
    violations = check_story(story)
    assert any(v.rule == "path_length" for v in violations)


def test_choice_page_with_next_page_is_a_structure_violation():
    story = branching_story()
    for page in story.pages:
        if page.choice is not None:
            page.next_page = "a1"
    violations = check_story(story)
    assert any(v.rule == "branch_structure" for v in violations)


def test_unreachable_page_is_a_structure_violation():
    story = branching_story()
    orphan = Page(id="zz", text=PAGE_TEXT, next_page=None)
    story = story.model_copy(update={"pages": [*story.pages, orphan]})
    violations = check_story(story)
    assert any(v.rule == "branch_structure" for v in violations)


def test_linear_story_with_a_choice_is_a_structure_violation():
    story = branching_story().model_copy(update={"shape": "linear"})
    violations = check_story(story)
    assert any(v.rule == "branch_structure" for v in violations)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/pipeline/test_content_rules_branching.py -v`
Expected: FAIL — `ImportError: cannot import name 'ARM_PAGES'` (and `heard_paths`).

- [ ] **Step 4: Implement `ARM_PAGES`, `heard_paths`, and branching checks**

In `src/pipeline/content_rules.py`:

1. Add the constant next to the others and extend the rule literal:

```python
ARM_PAGES = 4  # pages per branch arm; shared prefix = PAGE_COUNT - ARM_PAGES

ContentRule = Literal[
    "page_count", "page_words", "story_words", "sentence_cap",
    "branch_structure", "path_length",
]
```

2. Add path enumeration (pure, recursive, bounded — a cycle revisits a seen id and stops as a structure error handled in `check_story`):

```python
def heard_paths(story: Story) -> list[list[Page]]:
    """Every path a child can hear: follow next_page, forking at each choice."""
    by_id = {page.id: page for page in story.pages}
    referenced: set[str] = set()
    for page in story.pages:
        if page.next_page:
            referenced.add(page.next_page)
        if page.choice:
            for option in page.choice.options:
                referenced.add(option.next_page)
    entry = next((p for p in story.pages if p.id not in referenced), story.pages[0])

    paths: list[list[Page]] = []

    def walk(page: Page | None, trail: list[Page]) -> None:
        if page is None or page in trail:
            paths.append(trail)
            return
        trail = [*trail, page]
        if page.choice is not None:
            for option in page.choice.options:
                walk(by_id.get(option.next_page), trail)
            return
        if page.next_page is None:
            paths.append(trail)
            return
        walk(by_id.get(page.next_page), trail)

    walk(entry, [])
    return paths
```

3. In `check_story`, keep the existing per-page checks (page words with labels, sentence cap) running over `story.pages` exactly as today, but make the count/total checks shape-aware. Structure checks for `shape == "branching"`:
   - every page with `choice` must have `next_page is None` → `branch_structure`
   - `shape == "branching"` requires at least one choice page; `shape == "linear"` requires none → `branch_structure`
   - every page must appear in some heard path (reachability) → `branch_structure`, with `page_id` set
   - each heard path must have exactly `PAGE_COUNT` pages → `path_length`, `detail` naming the terminal page id
   - each heard path's total words (choice labels counted on the choice page, via the existing `page_word_count`) must sit within `STORY_WORDS_MIN..STORY_WORDS_MAX` → `story_words`, `detail` naming the terminal page id

   For `shape == "linear"` the existing `page_count` and `story_words` whole-story checks run unchanged.

- [ ] **Step 5: Run the new tests, then the whole pipeline suite**

Run: `uv run pytest tests/pipeline/test_content_rules_branching.py -v` → all PASS.
Run: `uv run pytest tests/pipeline/ -v` → all PASS (existing linear tests must not regress; if a linear test fails, the shape-awareness in step 4.3 is wrong — fix it, do not touch the old tests).

- [ ] **Step 6: Commit**

```bash
git add src/pipeline/models.py src/pipeline/content_rules.py tests/pipeline/test_content_rules_branching.py
git commit -m "feat(pipeline): heard-path enumeration + branching content rules (AI-428)"
```

---

### Task 2: The branching writer

**Files:**
- Modify: `src/pipeline/steps/write.py`
- Test: `tests/pipeline/test_write_branching.py` (new)

**Interfaces:**
- Consumes: `ARM_PAGES`, `PAGE_COUNT`, `check_story` from Task 1; existing `write.py` internals: `StoryDraft`, `story_from_draft`, `WRITE_INSTRUCTIONS`, `write_story(theme, language, settings, cache, *, model=None, premise=None)`, `PROMPT_VERSION`.
- Produces: `BranchingStoryDraft` (pydantic model), `branching_story_from_draft(draft, *, story_id, theme, language) -> Story`, and `write_story(..., shape: Literal["linear", "branching"] = "linear")`. Page ids are `p1..p{SHARED}`, `a1..a{ARM_PAGES}`, `b1..b{ARM_PAGES}` — Tasks 3, 6, 7 rely on these ids being deterministic.

- [ ] **Step 1: Write the failing tests**

Create `tests/pipeline/test_write_branching.py`. Follow the mocking pattern of the existing write tests (find them with `grep -rl "write_story" tests/pipeline/` — they use `pydantic_ai`'s `TestModel` or a function model to return a canned draft; reuse that exact pattern):

```python
from src.pipeline.content_rules import ARM_PAGES, PAGE_COUNT, check_story
from src.pipeline.steps.write import BranchingStoryDraft, branching_story_from_draft

SHARED = PAGE_COUNT - ARM_PAGES
PAGE_TEXT = " ".join(["dorme."] * 30)


def make_draft() -> BranchingStoryDraft:
    return BranchingStoryDraft(
        title="La lanterna e la barchetta",
        shared_pages=[PAGE_TEXT] * SHARED,
        option_labels=("la lanterna", "la barchetta"),
        arm_a=[PAGE_TEXT] * ARM_PAGES,
        arm_b=[PAGE_TEXT] * ARM_PAGES,
    )


def test_draft_builds_a_valid_branching_story():
    story = branching_story_from_draft(
        make_draft(), story_id="s1", theme="the little boat", language="it"
    )
    assert story.shape == "branching"
    assert check_story(story) == []


def test_choice_sits_on_the_last_shared_page():
    story = branching_story_from_draft(
        make_draft(), story_id="s1", theme="the little boat", language="it"
    )
    choice_page = next(p for p in story.pages if p.choice is not None)
    assert choice_page.id == f"p{SHARED}"
    assert choice_page.next_page is None
    assert choice_page.choice.options[0].next_page == "a1"
    assert choice_page.choice.options[1].next_page == "b1"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/pipeline/test_write_branching.py -v`
Expected: FAIL — `ImportError: cannot import name 'BranchingStoryDraft'`.

- [ ] **Step 3: Implement draft model, builder, and prompt**

In `src/pipeline/steps/write.py`:

```python
from src.pipeline.content_rules import ARM_PAGES  # add to the existing import

SHARED_PAGES = PAGE_COUNT - ARM_PAGES


class BranchingStoryDraft(BaseModel):
    """The writer's branching return: shared prose, two labeled arms."""

    title: str
    shared_pages: list[str]  # prose for p1..p{SHARED_PAGES}; the last ends at the split
    option_labels: tuple[str, str]  # short picture-card labels, story language
    arm_a: list[str]  # prose continuing option one, ARM_PAGES pages to its ending
    arm_b: list[str]  # prose continuing option two, ARM_PAGES pages to its ending


def branching_story_from_draft(
    draft: BranchingStoryDraft, *, story_id: str, theme: Theme, language: Language
) -> Story:
    """Assemble the typed branching Story around the model's prose."""
    shared = [
        Page(
            id=f"p{i}",
            text=text,
            next_page=f"p{i + 1}" if i < SHARED_PAGES else None,
        )
        for i, text in enumerate(draft.shared_pages, start=1)
    ]
    shared[-1].choice = ChoicePoint(
        options=(
            ChoiceOption(label=draft.option_labels[0], next_page="a1"),
            ChoiceOption(label=draft.option_labels[1], next_page="b1"),
        )
    )

    def arm(prefix: str, texts: list[str]) -> list[Page]:
        return [
            Page(
                id=f"{prefix}{i}",
                text=text,
                next_page=f"{prefix}{i + 1}" if i < ARM_PAGES else None,
            )
            for i, text in enumerate(texts, start=1)
        ]

    return Story(
        id=story_id,
        language=language,
        title=draft.title,
        theme=theme,
        shape="branching",
        pages=[*shared, *arm("a", draft.arm_a), *arm("b", draft.arm_b)],
    )
```

Import `ChoicePoint`, `ChoiceOption` from `src.pipeline.models` alongside the existing imports. Add a branching prompt next to `WRITE_INSTRUCTIONS`, derived entirely from constants:

```python
BRANCHING_INSTRUCTIONS = WRITE_INSTRUCTIONS + f"""

This story BRANCHES. Structure:
- {SHARED_PAGES} shared pages; the last shared page ends at a gentle split
  (a path forks, two doors, two friends beckon — never danger).
- Two options a pre-reader can tell apart as pictures. Each label is 1-4
  words in the story language, concrete and visual (a lantern / a rowboat).
- Two arms of exactly {ARM_PAGES} pages each, continuing from the split.
  Each arm is a complete, different ending; BOTH arms land on comfort or
  sleepiness. Every heard path (shared pages plus one arm) must respect
  every limit above as if it were the whole story.
"""
```

- [ ] **Step 4: Extend `write_story` with `shape`**

Change the signature (default preserves every existing call):

```python
def write_story(
    theme: Theme,
    language: Language,
    settings: Settings,
    cache: ArtifactCache,
    *,
    model: Model | None = None,
    premise: str | None = None,
    shape: Literal["linear", "branching"] = "linear",
) -> Story:
```

Inside: `shape` joins the cache-key inputs dict (so a linear and branching run of the same theme cache separately); when `shape == "branching"`, the agent is built with `output_type=BranchingStoryDraft` and `instructions=BRANCHING_INSTRUCTIONS`, and the result goes through `branching_story_from_draft` instead of `story_from_draft`. The line `shape="linear"` at `write.py:82` (inside `story_from_draft`) stays — that function remains the linear builder.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/pipeline/test_write_branching.py tests/pipeline/ -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/pipeline/steps/write.py tests/pipeline/test_write_branching.py
git commit -m "feat(pipeline): branching writer — draft model, builder, prompt, shape param (AI-428)"
```

---

### Task 3: Branching-aware revise loop

**Files:**
- Modify: `src/pipeline/steps/revise.py`
- Test: `tests/pipeline/test_revise_branching.py` (new)

**Interfaces:**
- Consumes: `BranchingStoryDraft`, `branching_story_from_draft` from Task 2; existing `revise_story(story, failures, settings, cache, *, model=None) -> Story`, `build_revise_agent`, `author_story`.
- Produces: `revise_story` handles `story.shape == "branching"` transparently — same signature. `author_story` needs no signature change (it already passes the story through).

- [ ] **Step 1: Write the failing test**

Create `tests/pipeline/test_revise_branching.py`. Mirror the existing revise tests' mocking pattern (find with `grep -rl revise tests/pipeline/`). The test: revise a branching story (use the Task 2 draft factory, imported from a small shared helper or rebuilt inline) with a fake model that returns a corrected `BranchingStoryDraft`, and assert the revised story still has `shape == "branching"`, the same page-id structure, and `check_story(revised) == []`.

```python
def test_revise_preserves_branching_structure(fake_revise_model):
    story = branching_story_from_draft(make_draft(), story_id="s1",
                                       theme="the little boat", language="it")
    revised = revise_story(story, ["page a2: sentence over cap"],
                           settings, cache, model=fake_revise_model)
    assert revised.shape == "branching"
    assert {p.id for p in revised.pages} == {p.id for p in story.pages}
```

(Adapt fixture names to the existing test file's conventions — copy its `settings`/`cache` fixtures.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/pipeline/test_revise_branching.py -v`
Expected: FAIL — revise currently rebuilds via linear `StoryDraft`/`story_from_draft`, so shape/ids break (or the agent's output type rejects the branching draft).

- [ ] **Step 3: Implement**

In `revise_story` (`src/pipeline/steps/revise.py:60-89`): branch on `story.shape`. For `"branching"`, build the agent with `output_type=BranchingStoryDraft` and rebuild via `branching_story_from_draft(draft, story_id=story.id, theme=story.theme, language=story.language)`. Extract the agent construction + rebuild into a small local pair chosen by shape; `REVISE_INSTRUCTIONS` stays shared. `story.shape` is already inside the cache-key inputs via `story.model_dump` — no cache change needed.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/pipeline/test_revise_branching.py tests/pipeline/ -v` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/steps/revise.py tests/pipeline/test_revise_branching.py
git commit -m "feat(pipeline): revise loop rebuilds branching drafts shape-faithfully (AI-428)"
```

---

### Task 4: Safety gate judges branch arms and labels

**Files:**
- Modify: `src/pipeline/steps/safety.py` (only if the test exposes a gap)
- Test: `tests/pipeline/test_safety_branching.py` (new)

**Interfaces:**
- Consumes: `safety_gate(story, settings, cache, *, model=None) -> SafetyReport` (`safety.py:58`); the branching story factory from Task 1/2 tests.
- Produces: a regression guarantee — every branch arm's text and both choice labels reach the judge.

- [ ] **Step 1: Write the test**

The safety gate serializes the story for the judge. Assert the serialized prompt contains arm text and labels. Read `safety.py` first to find where the prompt is built; capture it with the same mocking approach the existing safety tests use (a function model that records its input):

```python
def test_judge_sees_both_arms_and_labels(recording_model, settings, cache):
    story = branching_story_from_draft(make_draft(), story_id="s1",
                                       theme="the little boat", language="it")
    safety_gate(story, settings, cache, model=recording_model)
    prompt = recording_model.last_prompt  # adapt to the recording mechanism
    assert "la lanterna" in prompt and "la barchetta" in prompt
    for page in story.pages:
        assert page.text.split(".")[0] in prompt
```

(Give each arm page distinct text in the factory for this test — e.g. `f"pagina {page_id} " + PAGE_TEXT` — so containment is meaningful.)

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/pipeline/test_safety_branching.py -v`
Expected: likely PASS already (the gate serializes the whole `Story`, and `model_dump_json` includes choice labels). If it FAILS, the prompt builder selects fields — extend it to include `page.choice.options[*].label` and re-run to PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/pipeline/test_safety_branching.py src/pipeline/steps/safety.py
git commit -m "test(pipeline): safety gate provably judges branch arms and choice labels (AI-428)"
```

---

### Task 5: Narrate the spoken option labels

**Files:**
- Modify: `src/pipeline/steps/narrate.py`
- Test: `tests/pipeline/test_narrate_labels.py` (new)

**Interfaces:**
- Consumes: `narrate_pages(pages, ...)` (`narrate.py:83`) and its `NarrationClient`/cache pattern; `ChoiceOption.audio` from Task 1.
- Produces: `narrate_choice_labels(pages: list[Page], language: Language, settings: Settings, cache: ArtifactCache, *, client: NarrationClient | None = None) -> list[Page]` — returns pages with `option.audio` set (`PageAudio(file=<wav path>, timings=[])`) for every option on every choice page. Task 7 consumes `option.audio.file`; Task 8 wires this into `generate_story`.

- [ ] **Step 1: Write the failing test**

Create `tests/pipeline/test_narrate_labels.py`, copying the fake-`NarrationClient` pattern from the existing narrate tests (find with `grep -rl narrate tests/pipeline/`):

```python
def test_labels_get_audio(fake_client, settings, cache):
    pages = branching_story_from_draft(make_draft(), story_id="s1",
                                       theme="the little boat", language="it").pages
    narrated = narrate_choice_labels(pages, "it", settings, cache, client=fake_client)
    choice_page = next(p for p in narrated if p.choice is not None)
    for option in choice_page.choice.options:
        assert option.audio is not None
        assert option.audio.file.endswith(".wav")
        assert option.audio.timings == []


def test_non_choice_pages_untouched(fake_client, settings, cache):
    pages = [Page(id="p1", text="dorme.", next_page=None)]
    assert narrate_choice_labels(pages, "it", settings, cache,
                                 client=fake_client) == pages
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/pipeline/test_narrate_labels.py -v`
Expected: FAIL — `narrate_choice_labels` not defined.

- [ ] **Step 3: Implement**

In `narrate.py`, mirror `narrate_pages`' structure exactly: same client default, same `run_step` caching with inputs `{"text": option.label, "language": ..., "voice": ..., "model": ...}` (copy the exact input dict shape `narrate_pages` uses so a label and a page with identical text share nothing accidentally — add `"kind": "choice_label"` to the inputs). Each synthesized label is written next to page audio with a name like `{page_id}.opt{index}.wav`. Return `page.model_copy(update={"choice": ...})` with options carrying audio; leave non-choice pages untouched.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/pipeline/test_narrate_labels.py tests/pipeline/ -v` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/steps/narrate.py tests/pipeline/test_narrate_labels.py
git commit -m "feat(pipeline): synthesize spoken choice labels through the narration cache (AI-428)"
```

---

### Task 6: Illustrate the choice cards

**Files:**
- Modify: `src/pipeline/steps/illustrate.py`
- Test: `tests/pipeline/test_illustrate_cards.py` (new)

**Interfaces:**
- Consumes: `illustrate_story` (`illustrate.py:147`), `IllustrationSet` (`illustrate.py:~56`), `STYLE_PROMPT`, the httpx-transport test seam the existing illustrate tests use.
- Produces: `IllustrationSet.card_images: dict[str, Path]` keyed `f"{page_id}:{option_index}"` (e.g. `"p6:0"`); `illustrate_story` fills it for every option on every choice page, generated **against the character sheet** like page images. Task 7 consumes this dict. Branch-arm *page* images need no work — `illustrate.py:181` already loops `story.pages`, which contains the arms.

- [ ] **Step 1: Write the failing test**

Copy the existing illustrate test's mocked-transport fixture; feed it the branching story:

```python
def test_choice_options_get_cards(mock_transport, settings, cache):
    story = branching_story_from_draft(make_draft(), story_id="s1",
                                       theme="the little boat", language="it")
    result = illustrate_story(story, settings, cache, transport=mock_transport)
    choice_page = next(p for p in story.pages if p.choice is not None)
    assert f"{choice_page.id}:0" in result.card_images
    assert f"{choice_page.id}:1" in result.card_images
    assert all(path.exists() for path in result.card_images.values())


def test_linear_story_has_no_cards(mock_transport, settings, cache):
    # reuse any linear story fixture from the existing illustrate tests
    result = illustrate_story(linear_story, settings, cache, transport=mock_transport)
    assert result.card_images == {}
```

(Adapt the `transport=` kwarg name to whatever `illustrate_story` actually accepts — the existing tests show it.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/pipeline/test_illustrate_cards.py -v`
Expected: FAIL — `IllustrationSet` has no `card_images`.

- [ ] **Step 3: Implement**

1. `IllustrationSet` gains `card_images: dict[str, Path] = Field(default_factory=dict)`.
2. After the page-image loop in `illustrate_story`, iterate choice pages and their options. Card prompt (a module constant, versioned like `STYLE_PROMPT` — it participates in cache keys):

```python
CARD_PROMPT = (
    "A single choice card for a pre-reader: one clear subject only — {label} — "
    "centered, filling the frame, instantly recognizable at a glance. "
    "No text anywhere."
)
```

Compose it with `STYLE_PROMPT` and the character-sheet reference exactly the way page images do (same request/caching helpers, same `_artifact_path` pattern), inputs keyed by `{"label": option.label, "sheet": sheet_hash, "style": STYLE_PROMPT, "card": CARD_PROMPT, "model": ...}`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/pipeline/test_illustrate_cards.py tests/pipeline/ -v` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/steps/illustrate.py tests/pipeline/test_illustrate_cards.py
git commit -m "feat(pipeline): watercolor choice cards illustrated against the character sheet (AI-428)"
```

---

### Task 7: Assemble hashes the new choice assets

**Files:**
- Modify: `src/pipeline/steps/assemble.py:86-127`
- Test: `tests/pipeline/test_assemble_branching.py` (new)

**Interfaces:**
- Consumes: `assemble_story(story, illustrations) -> AssembledStory` (`assemble.py:86`), `_hashed_name`, `MissingAssetError`; `option.audio` (Task 5), `IllustrationSet.card_images` (Task 6).
- Produces: assembled choice pages whose options carry immutable hashed `card_image` and `audio.file` names, with the underlying files registered in `AssembledStory.assets`. A choice page missing either asset raises `MissingAssetError(page_id, "card_image" | "label_audio", path)`.

- [ ] **Step 1: Write the failing test**

```python
def test_options_get_hashed_assets(tmp_path):
    story, illustrations = assembled_branching_fixture(tmp_path)  # helper below
    result = assemble_story(story, illustrations)
    choice_page = next(p for p in result.story.pages if p.choice is not None)
    for option in choice_page.choice.options:
        assert option.card_image in result.assets
        assert option.audio.file in result.assets
        assert "." in option.card_image  # hashed name, not a path


def test_missing_card_raises(tmp_path):
    story, illustrations = assembled_branching_fixture(tmp_path)
    illustrations.card_images.clear()
    with pytest.raises(MissingAssetError):
        assemble_story(story, illustrations)
```

The `assembled_branching_fixture` helper builds the Task 1 branching story, writes stub `.wav`/`.png` bytes into `tmp_path` for every page, option label, and card, and attaches them (`page.audio`, `option.audio`, `IllustrationSet.page_images`, `.card_images`) — copy the stub-asset approach from the existing assemble tests.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/pipeline/test_assemble_branching.py -v`
Expected: FAIL — options pass through unhashed (first assertion) and nothing raises for missing cards.

- [ ] **Step 3: Implement**

Inside the page loop in `assemble_story`, when `page.choice is not None`: for each option (enumerate for the card key), resolve `illustrations.card_images[f"{page.id}:{index}"]` and `option.audio.file`; raise `MissingAssetError(page.id, "card_image", path)` / `(page.id, "label_audio", path)` when absent; hash with `_hashed_name(f"{page.id}.opt{index}", data, suffix)`; register in `assets`; rebuild the option via `option.model_copy(update=...)` and the page's `choice` via `ChoicePoint(options=(...))` in the same `page.model_copy(update=...)` that already rewrites audio/image.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/pipeline/test_assemble_branching.py tests/pipeline/ -v` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/steps/assemble.py tests/pipeline/test_assemble_branching.py
git commit -m "feat(pipeline): assemble hashes choice cards and label audio into story.json (AI-428)"
```

---

### Task 8: Shape plumbing — generate, CLI, workshop

**Files:**
- Modify: `src/pipeline/generate.py:41-52` (signature) and its step calls
- Modify: `src/pipeline/cli.py:33-46`
- Modify: `src/api/routes/workshop.py:227-235`, `src/templates/workshop/dashboard.html:15-30`
- Test: `tests/pipeline/test_generate_branching.py` (new); extend the existing workshop route test file

**Interfaces:**
- Consumes: everything from Tasks 2, 5, 6, 7.
- Produces: `generate_story(theme, language, settings, *, shape: Literal["linear", "branching"] = "linear", ...)` running the full branching pipeline; CLI `--shape branching` actually reaching it (today `cli.py:33-46` validates the flag and drops it); the workshop start-run form gaining a Shape select whose value flows through `start_run` into the manager's `generate_story` call.

- [ ] **Step 1: Write the failing pipeline test**

`tests/pipeline/test_generate_branching.py`: copy the existing end-to-end `generate_story` test (mocked models + fake narration client + mocked image transport — it exists; find it with `grep -rl generate_story tests/`), switch the write/revise fakes to return `BranchingStoryDraft`, call with `shape="branching"`, and assert the staged `story.json` has `shape == "branching"`, options with `audio` and `card_image` hashed names, and that every referenced asset was staged.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/pipeline/test_generate_branching.py -v`
Expected: FAIL — `generate_story() got an unexpected keyword argument 'shape'`.

- [ ] **Step 3: Implement the plumbing**

- `generate_story` gains the keyword `shape="linear"`, passes it to `write_story` (Task 2), and calls `narrate_choice_labels` (Task 5) right after `narrate_pages`. Illustrate/assemble need no call-site changes (Tasks 6-7 changed their internals).
- `cli.py`: the validated `shape` now passes to `generate_story(..., shape=shape)`.
- `workshop.py` `start_run`: add `shape: Annotated[str, Form()] = "linear"`, validate against `("linear", "branching")` (400 otherwise, matching the route's existing validation style), pass through the manager to `generate_story`. Check `src/workshop/manager.py` for the call site and thread the parameter.
- `dashboard.html`: add a Shape select next to the Language select, same `ws-field ws-select` classes:

```html
<label class="ws-field-group">
  <span class="ws-field-label">Shape</span>
  <div class="ws-field-select-wrap">
    <select name="shape" class="ws-field ws-select">
      <option value="linear">Linear</option>
      <option value="branching">Branching</option>
    </select>
  </div>
</label>
```

- [ ] **Step 4: Write/extend the workshop route test**

In the existing workshop test file (find with `grep -rl start_run tests/`): posting the start-run form with `shape=branching` passes it to the (mocked) manager; `shape=zigzag` returns 400.

- [ ] **Step 5: Run everything**

Run: `uv run pytest -v` → all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/pipeline/generate.py src/pipeline/cli.py src/api/routes/workshop.py src/workshop/manager.py src/templates/workshop/dashboard.html tests/
git commit -m "feat: shape flows CLI/workshop → generate → write; labels narrated in-run (AI-428)"
```

---

### Task 9: Player — path walker and a branching fixture

**Files:**
- Modify: `src/static/js/story.js`
- Create: `src/static/content/it/stories/dev-branching/story.json` (fixture; mirror the existing dev fixture's location — check where the current dev `story.json` lives with `find src/static/content -name "story.json"` and match)
- Test: `tests/js/story.test.js` (extend)

**Interfaces:**
- Consumes: `orderPages`, `loadStory` (`story.js:8-57`).
- Produces: `loadStory` result gains `pagesFrom(pageId) -> playable[]` — the ordered playable pages walking `next_page` from `pageId` (same `toPlayable` mapping, same halting-at-choice behavior). `orderPages` unchanged. Tasks 10-12 consume `pagesFrom`.

- [ ] **Step 1: Write the failing tests**

In `tests/js/story.test.js`, add a branching `storyJson` fixture object (shared prefix `p1..p2`, choice on `p2`, arms `a1..a2`, `b1..b2` — the player does not enforce `PAGE_COUNT`; short graphs keep tests readable):

```javascript
test("pagesFrom walks an arm to its ending", async () => {
  const loaded = await loadStory(fetchFnFor(branchingJson), "u");
  const arm = loaded.pagesFrom("b1");
  expect(arm.map((p) => p.id)).toEqual(["b1", "b2"]);
});

test("ordered walk still halts at the choice page", async () => {
  const loaded = await loadStory(fetchFnFor(branchingJson), "u");
  expect(loaded.pages.map((p) => p.id)).toEqual(["p1", "p2"]);
});
```

(`fetchFnFor` = whatever fetch-stub helper the existing tests in this file use; reuse it.)

- [ ] **Step 2: Run to verify failure**

Run: `npm test -- story` → FAIL: `loaded.pagesFrom is not a function`.

- [ ] **Step 3: Implement**

In `loadStory`, alongside `pages`/`allPages`, build a `byId` map of playables and return:

```javascript
pagesFrom(pageId) {
  const ordered = [];
  let current = byId.get(pageId);
  const seen = new Set();
  while (current && !seen.has(current.id)) {
    ordered.push(current);
    seen.add(current.id);
    current = current.next_page ? byId.get(current.next_page) : null;
  }
  return ordered;
}
```

`toPlayable` must therefore keep `id` and `next_page` on the playable object (check it does; add them if missing).

- [ ] **Step 4: Create the dev fixture**

Author `story.json` for a small branching story matching the pipeline contract (ids `p1..p6`, choice on `p6`, arms `a1..a4`/`b1..b4`, options carrying `label`, `card_image: null`, `audio: null`, `next_page`) reusing the existing fixture's audio/image asset names so it plays in dev without new assets. Add its cover to the same fixture manifest the current dev story uses.

- [ ] **Step 5: Run and commit**

Run: `npm test` → all PASS.

```bash
git add src/static/js/story.js src/static/content tests/js/story.test.js
git commit -m "feat(player): pagesFrom path walker + branching dev fixture (AI-428)"
```

---

### Task 10: Player — the tapped option extends the path

**Files:**
- Modify: `src/static/js/store.js:87-89`, `src/static/js/screens.js:254-268`, `src/static/js/playback.js:115-125`, `src/static/js/main.js` (wiring)
- Test: `tests/js/store.test.js`, `tests/js/playback.test.js` (extend both)

**Interfaces:**
- Consumes: `store.choose()` (`store.js:87` — today `set({choiceOpen: false, page: choicePage + 1})`), `buildChoiceOverlay` (`screens.js`, whose option click calls `store.choose()` at `screens.js:263`), playback's `choicePage` computation (`playback.js:119-123`), `pagesFrom` from Task 9.
- Produces: `store.choose(optionIndex = 0)` additionally records the pick in a new state array `choices` (for Task 12); `buildChoiceOverlay(view, store, onChoose)` where option `i`'s click calls `onChoose(i)` (falling back to `store.choose(i)` when `onChoose` is absent); `playback.extendPath(playablePages)` appends the chosen arm to its loaded pages and recomputes the next `choicePage`; `main.js` wires `onChoose = (i) => { playback.extendPath(loaded.pagesFrom(optionTarget(i))); store.choose(i); }`. **Order matters: extend first, then `store.choose()`** — choose advances `page` to `choicePage + 1`, which must already be the arm's first page.

- [ ] **Step 1: Write the failing store test**

```javascript
test("choose records the option index", () => {
  const store = createStore({ ...initialState, screen: "player",
                              choiceOpen: true, choicePage: 3, page: 3 });
  store.choose(1);
  expect(store.state.choices).toEqual([1]);
  expect(store.state.page).toBe(4);
  expect(store.state.choiceOpen).toBe(false);
});
```

(Adapt construction to the real `createStore` API — the existing store tests show it.)

- [ ] **Step 2: Write the failing playback test**

```javascript
test("extendPath appends the arm and playback narrates into it", () => {
  // load the branching fixture; play to the choice page; simulate:
  playback.extendPath(loaded.pagesFrom("a1"));
  store.choose(0);
  // assert the next narrated page is "a1" — follow the existing
  // playback test's fake-engine pattern for observing narration calls
});
```

- [ ] **Step 3: Run to verify failures**

Run: `npm test -- store playback` → FAIL: `choices` undefined; `extendPath` not a function.

- [ ] **Step 4: Implement**

- `store.js`: `initialState` gains `choices: []`; `choose(optionIndex = 0)` becomes `set({ choiceOpen: false, page: state.choicePage + 1, choices: [...state.choices, optionIndex] })`. `exitStory`/`replay`/`resumeRestart` reset `choices: []` wherever they reset `page` (read those transitions and mirror them).
- `screens.js`: `buildChoiceOverlay` takes `onChoose`; the click handler at `screens.js:263` becomes `option.addEventListener("click", () => (onChoose ? onChoose(index) : store.choose(index)))` (use `options.forEach((opt, index) => ...)`).
- `playback.js`: expose `extendPath(pages)` on the returned handle: `loaded.pages = [...loaded.pages, ...pages]` and recompute `choicePage` as the index of the next page with a `choice` at or after the current end (same `findIndex` shape as `playback.js:119`), pushing the result into the store the same way the initial load does.
- `main.js`: where the choice overlay is built, pass the `onChoose` closure from the Interfaces block; `optionTarget(i)` reads `loaded.pages[store.state.choicePage].choice.options[i].next_page`.

- [ ] **Step 5: Run all JS tests**

Run: `npm test` → all PASS (existing choice-overlay tests updated only if the new `buildChoiceOverlay` parameter breaks their call shape — add `undefined` third arg, behavior is unchanged without it).

- [ ] **Step 6: Commit**

```bash
git add src/static/js tests/js
git commit -m "feat(player): tapped option extends the path — real branch-following (AI-428)"
```

---

### Task 11: Player — choice cards and spoken labels

**Files:**
- Modify: `src/static/js/screens.js:254-268`, `src/static/js/playback.js`, `src/static/js/prefetch.js`
- Test: `tests/js/screens.test.js`, `tests/js/prefetch.test.js` (extend)

**Interfaces:**
- Consumes: `option.card_image` / `option.audio` (resolved to URLs by `toPlayable` — extend the mapping in `story.js` to resolve them against the asset base exactly like `page.image`/`page.audio.file`); the engine's prompt channel (`playPrompt` — see `audio-engine.js`); prefetch's per-page banking loop.
- Produces: the overlay renders an `<img class="choice-card-image">` inside the card when `card_image` is present (existing wash markup stays as the fallback — the dev fixture and mock shelf have no cards); when the overlay opens, playback speaks label 0 then label 1 on the prompt channel, sequentially, never overlapping narration; prefetch banks card images (HTTP cache) and label audio (decoded buffers) with the same never-fatal failure counting.

- [ ] **Step 1: Write the failing screens test**

```javascript
test("options with card images render them", () => {
  const overlay = buildChoiceOverlay(viewWithCards, store, undefined);
  const imgs = overlay.querySelectorAll("img.choice-card-image");
  expect(imgs.length).toBe(2);
  expect(imgs[0].src).toContain("p6.opt0");
});

test("options without card images keep the wash", () => {
  const overlay = buildChoiceOverlay(viewWithWashes, store, undefined);
  expect(overlay.querySelectorAll("img").length).toBe(0);
});
```

- [ ] **Step 2: Write the failing playback + prefetch tests**

Playback: opening the choice overlay calls `engine.playPrompt` with option 0's audio URL, and option 1's only after 0 ends (drive the fake engine's `onEnded` the way existing playback tests do). Prefetch: the branching fixture with `card_image`/`audio` set produces fetches for both new asset kinds; a failed card fetch increments the failure count without throwing.

- [ ] **Step 3: Run to verify failures**

Run: `npm test -- screens playback prefetch` → new tests FAIL.

- [ ] **Step 4: Implement**

- `story.js` `toPlayable`: options map to `{ ...option, card_image: option.card_image ? base + option.card_image : null, audioUrl: option.audio ? base + option.audio.file : null }`.
- `screens.js`: inside the options loop, `if (option.card_image)` append the `img` (with `alt: ""` — the button's `aria-label` already carries the label) instead of relying on the wash class alone.
- `playback.js`: in the store subscription where `choiceOpen` transitions to `true`, if the choice page's options carry `audioUrl`s, chain `playPrompt(url0)` → on ended → `playPrompt(url1)`. Skip silently when `audioUrl` is null (dev fixture).
- `prefetch.js`: in the per-page banking, for `page.choice?.options`, bank `option.card_image` alongside images and `option.audioUrl` alongside audio.

- [ ] **Step 5: Run all JS tests, then look at it**

Run: `npm test` → all PASS.
Run: `make dev`, open the player with the branching fixture, tap through to the choice — cards (or washes) render, arm B plays after tapping B.

- [ ] **Step 6: Commit**

```bash
git add src/static/js tests/js
git commit -m "feat(player): choice card images + spoken labels, prefetched like pages (AI-428)"
```

---

### Task 12: Player — resume across a branch

**Files:**
- Modify: `src/static/js/storage.js`, `src/static/js/main.js`
- Test: `tests/js/storage.test.js`, and a resume test in `tests/js/main.test.js` (or wherever shell-boot resume is tested — find with `grep -rl resume tests/js/`)

**Interfaces:**
- Consumes: `storage.save/load` (persists a state subset under one key), `store.state.choices` (Task 10), `pagesFrom` (Task 9), the existing resume flow (reopening an unfinished story restores `page`).
- Produces: `choices` rides in the persisted payload; on resume, `main.js` replays each recorded choice — extending the path via `pagesFrom` for each — **before** restoring `page`, so the restored index points into the rebuilt path. Restart paths clear `choices` (already handled in Task 10's store changes).

- [ ] **Step 1: Write the failing tests**

Storage: saved payload round-trips `choices`. Resume: load the branching fixture, simulate saved state `{ page: <index inside arm b>, choices: [1] }`, boot the shell, assert the resumed current page is the correct arm-b page (reuse the existing resume test's harness).

- [ ] **Step 2: Run to verify failure**

Run: `npm test -- storage main` → FAIL.

- [ ] **Step 3: Implement**

- `storage.js`: include `choices` in the saved subset and default it to `[]` on load (tolerate old payloads without the key — silent-failure philosophy).
- `main.js`: in the resume path, before applying the saved `page`, fold the saved `choices` over the loaded story: for each recorded index, find the next choice page in the current path, call `playback.extendPath(loaded.pagesFrom(option.next_page))`. If a recorded choice no longer matches the story graph (republished story), discard the save and start fresh — never crash.

- [ ] **Step 4: Run all JS tests**

Run: `npm test` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/static/js/storage.js src/static/js/main.js tests/js
git commit -m "feat(player): resume survives branches — chosen path persisted and replayed (AI-428)"
```

---

### Task 13: End-to-end proof and the audit script

**Files:**
- Create: `tests/e2e/branching.spec.js`
- Verify (modify only if red): `scripts/` audit script (find with `ls scripts/`), `docs/product.md` status row

**Interfaces:**
- Consumes: everything above; the existing Playwright config and e2e helpers (`tests/e2e/*.spec.js` show the dev-server + fixture pattern).
- Produces: a browser-level guarantee of the child-visible behavior, and a clean audit over a branching story.

- [ ] **Step 1: Write the E2E spec**

Following the existing spec's structure (dev server, fixture shelf):

```javascript
test("a tapped choice leads to that arm's ending", async ({ page }) => {
  // open shelf → tap the branching cover → advance to the choice
  // (use the ?speed= page-timer override the dev fixture relies on)
  // assert: overlay visible with two option buttons
  // tap option 2 → assert a page unique to arm B renders
  // advance to the end → assert the end screen shows
});

test("auto-continue picks the first option", async ({ page }) => {
  // reach the choice, wait out the nudge+auto-continue window
  // assert a page unique to arm A renders
});
```

Fill in real selectors from `screens.js` (option buttons render with class `option` inside the choice overlay) and real fixture page text.

- [ ] **Step 2: Run it**

Run: `npm run test:e2e -- branching`
Expected: PASS. The auto-continue test depends on the nudge timers already shipped — if no auto-continue exists in playback (check for the 30s/10s timers from docs/product.md §Picture-Choice Pattern), mark that test `.skip` with a one-line comment naming it as follow-up scope and note it in the AI-428 Linear issue; do **not** silently drop it.

- [ ] **Step 3: Run the audit script against a staged branching story**

Run the CI audit (see `.github/workflows` or `scripts/` for the entry point) over the branching fixture/staged output. If it validates page graphs, teach it that choice pages carry no `next_page` (mirror Task 1's structure rules). Green before proceeding.

- [ ] **Step 4: Update the product status row**

In `docs/product.md`, the **Picture choices** row: description now honestly reflects branch-following (this plan closes the "overlay only" gap). One-line edit.

- [ ] **Step 5: Final full run and commit**

Run: `uv run pytest && npm test && npm run test:e2e`
Expected: everything green.

```bash
git add tests/e2e/branching.spec.js docs/product.md scripts
git commit -m "test(e2e): branching flow proven in a real browser; audit branch-aware (AI-428)"
```

---

## Out of Scope (deliberately)

- **Glosses for choice labels** — the gloss step doesn't exist yet (slice 6); labels-count-toward-gloss is recorded in the model comment already.
- **Multiple choice points per story** — validation handles them (path enumeration is generic); the writer intentionally produces one. Revisit with library feedback.
- **Resolving the 8-vs-10 page / 12-vs-20 word-cap conflict** between docs/product.md and `content_rules.py` — flagged in AI-428; everything here derives from the constants.
- **The choice-confirmation prompt** (*"Ottima scelta!"*) — a per-language UI prompt, part of the recorded-prompts set, not per-story audio.
