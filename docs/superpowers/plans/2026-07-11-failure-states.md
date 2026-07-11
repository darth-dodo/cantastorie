# Failure States: Audio Retry + Offline Screen (AI-367) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Never dead air, never a spinner: audio load failure shows a sleeping bird that speaks a retry prompt and recovers on tap; manifest load failure on cold start shows a clouds screen that speaks the offline prompt and recovers on tap.

**Architecture:** Two failure states wired into the existing vanilla-JS player. The audio-error state is a store flag (`audioError`) set by the playback loop's narration `.catch` (the seam left at `playback.js:34`), rendered as a full-screen tappable overlay inside the player; tap retries the same page. The offline state is a pre-store boot gate in `main.js` (the seam at `main.js:42`): while the manifest fetch fails, a clouds screen holds the boot, each tap speaks the offline prompt and retries the fetch. Two new spoken utterances (`audio_retry`, `offline`) join the pipeline and the dev fixtures. The retry prompt is banked by the whole-story prefetch so it can speak even on a flaky network; the offline prompt is served **same-origin** because the manifest (and R2 with it) is unreachable by definition in that state.

**Tech Stack:** Vanilla ES modules (no framework), Web Audio engine, pure-CSS illustrations, Vitest (jsdom) unit tests, Playwright E2E with `page.route()` interception, Python 3.12 pipeline (pydantic v2), pytest.

## Global Constraints

- Copy is **verbatim from `docs/product.md` → "Spoken Prompts"**:
  - Audio retry (it): `Oh! La storia fa un pisolino. Tocca l'uccellino per svegliarla.`
  - Offline (it): `Le nuvole hanno preso le storie. Riprova tra poco!`
- **Never dead air, never a spinner** (product.md UX principle #5): failures speak and offer a tap. No spinner elements, no silent failure screens.
- **CSS illustrations only**: the bird and clouds are drawn with gradients + pseudo-elements like the existing `.mascot` — no SVG files, no image assets for UI.
- Child tap targets ≥ 96px; the failure screens are whole-screen tappable buttons.
- Motion is gentle and guarded: any new animation gets an `@media (prefers-reduced-motion: reduce)` override that disables it.
- No new dependencies (JS or Python). No `<audio>` tags — all playback through the existing audio engine.
- Player stays framework-free ES modules; follow existing `el()` builder style in `screens.js`.
- "Offline mode" (caching stories for offline playback) is explicitly **out of scope** (product.md Decision Log: "Online only (v1)"). This is only the graceful failure screen.
- JS unit tests: `npx vitest run tests/js` must pass. E2E: `npx playwright test` must pass (server auto-starts via playwright.config.js).
- Python: `uv run pytest tests/pipeline -v`, `uv run mypy src`, `uv run ruff check src tests` must pass.
- **Known cross-task fact:** adding the retry prompt to prefetch changes the prefetch asset total from 18 to **19**. Two existing tests assert 18 and MUST be updated (Task 4): `tests/js/player.test.js` ("banks all 18 assets") and `tests/e2e/playback-loop.spec.js` (prefetch test, `status.total`). Do not "fix" the count by removing the prompt from prefetch.

---

### Task 1: Pipeline utterances — `audio_retry` and `offline`

**Files:**
- Modify: `src/pipeline/steps/narrate.py` (~line 37: `UtteranceName`, `IT_UTTERANCES`)
- Modify: `src/pipeline/publish.py` (~line 49: `MANIFEST_PROMPT_KEYS`)
- Test: `tests/pipeline/test_narrate.py` (~line 246), `tests/pipeline/test_publish.py` (~line 179)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: published manifests gain `prompts.audio_retry` and `prompts.offline` keys; utterance audio lands at `prompts/{lang}/audio_retry.{hash}.mp3` and `prompts/{lang}/offline.{hash}.mp3`. The player (Tasks 4 & 6) reads manifest keys `audio_retry` and `offline`.

- [ ] **Step 1: Extend the narrate test to cover the slice-2 prompts**

In `tests/pipeline/test_narrate.py`, find `test_slice_one_ships_the_three_italian_prompts_as_final_copy` (~line 246). Rename it and extend it so the utterance set is the five prompts with verbatim copy:

```python
def test_the_utterance_set_ships_final_italian_copy() -> None:
    """Given docs/product.md **Spoken Prompts**,
    When the utterance set is read,
    Then it carries the slice-1 prompts plus the slice-2 failure prompts,
    all as final copy, verbatim."""
    assert IT_UTTERANCES == {
        "shelf_greeting": "Ciao! Quale storia ascoltiamo oggi?",
        "story_start": "Si parte!",
        "end_prompt": "Fine! Ancora, o un'altra storia?",
        "audio_retry": "Oh! La storia fa un pisolino. Tocca l'uccellino per svegliarla.",
        "offline": "Le nuvole hanno preso le storie. Riprova tra poco!",
    }
```

Keep whatever docstring/assertion style the existing test uses if it differs — the binding requirement is the five keys and the verbatim strings.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/pipeline/test_narrate.py -v -k utterance`
Expected: FAIL (dict has only three entries).

- [ ] **Step 3: Add the two utterances in `narrate.py`**

```python
UtteranceName = Literal["shelf_greeting", "story_start", "end_prompt", "audio_retry", "offline"]

# Final Italian copy, verbatim from docs/product.md **Spoken Prompts** —
# the slice-1 set plus the slice-2 failure prompts (AI-367).
IT_UTTERANCES: Mapping[UtteranceName, str] = {
    "shelf_greeting": "Ciao! Quale storia ascoltiamo oggi?",
    "story_start": "Si parte!",
    "end_prompt": "Fine! Ancora, o un'altra storia?",
    "audio_retry": "Oh! La storia fa un pisolino. Tocca l'uccellino per svegliarla.",
    "offline": "Le nuvole hanno preso le storie. Riprova tra poco!",
}
```

- [ ] **Step 4: Update the publish mapping and its tests**

In `src/pipeline/publish.py`, extend `MANIFEST_PROMPT_KEYS` (player-facing keys on the right):

```python
MANIFEST_PROMPT_KEYS = {
    "shelf_greeting": "greeting",
    "story_start": "story_start",
    "end_prompt": "end",
    "audio_retry": "audio_retry",
    "offline": "offline",
}
```

In `tests/pipeline/test_publish.py`, the staging/publish tests assert the exact set of published prompt keys and the manifest `prompts` dict (~lines 179–190). Extend those assertions with the two new stems (`audio_retry`, `offline`) following the exact pattern the existing three use (same hashed-filename convention, same `PUBLIC_BASE` prefix). Read the test file first; every place that enumerates the three slice-1 prompts must now enumerate five.

- [ ] **Step 5: Run the pipeline test suites**

Run: `uv run pytest tests/pipeline/test_narrate.py tests/pipeline/test_publish.py -v`
Expected: PASS. If other pipeline tests enumerate the utterance set, fix them the same way and re-run `uv run pytest tests/pipeline -v` until green.

- [ ] **Step 6: Lint + typecheck**

Run: `uv run mypy src && uv run ruff check src tests`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/pipeline/steps/narrate.py src/pipeline/publish.py tests/pipeline/test_narrate.py tests/pipeline/test_publish.py
git commit -m "feat(pipeline): audio_retry + offline utterances for the failure states (AI-367)"
```

---

### Task 2: Dev fixture prompts + dev manifest keys

**Files:**
- Modify: `scripts/generate_dev_story.py` (~line 130, where `story-start.wav` and `end.wav` are written)
- Create (generated): `src/static/content/it/prompts/audio-retry.wav`, `src/static/content/it/prompts/offline.wav`
- Modify: `src/static/content/it/manifest.json`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: dev manifest keys `prompts.audio_retry` → `/static/content/it/prompts/audio-retry.wav` and `prompts.offline` → `/static/content/it/prompts/offline.wav`; the committed wav files. Tasks 4/6/7 rely on these exact URLs. The offline prompt **must** live at the same-origin conventional path `/static/content/{lang}/prompts/offline.wav` because the player derives it without a manifest.

- [ ] **Step 1: Add the two chimes to the dev fixture generator**

In `scripts/generate_dev_story.py` `main()`, next to the existing prompt writes (~line 130):

```python
    # Slice-2 failure prompts (AI-367): a sleepy falling third for the
    # napping story, a soft low pair for the clouds. Chime stand-ins until
    # the pipeline produces real utterance audio.
    (PROMPTS_DIR / "audio-retry.wav").write_bytes(chime([(659.25, 0.3), (523.25, 0.5)]))
    (PROMPTS_DIR / "offline.wav").write_bytes(chime([(440.0, 0.35), (392.0, 0.5)]))
```

- [ ] **Step 2: Regenerate fixtures and verify only the two new files appear**

Run: `uv run python scripts/generate_dev_story.py && git status --short`
Expected: only `src/static/content/it/prompts/audio-retry.wav` and `.../offline.wav` are new; no other fixture churn (the generator is deterministic). If other files changed, stop and report — do not commit churn.

- [ ] **Step 3: Add the manifest keys**

In `src/static/content/it/manifest.json`, extend `prompts`:

```json
  "prompts": {
    "greeting": "/static/content/it/prompts/greeting.wav",
    "story_start": "/static/content/it/prompts/story-start.wav",
    "end": "/static/content/it/prompts/end.wav",
    "audio_retry": "/static/content/it/prompts/audio-retry.wav",
    "offline": "/static/content/it/prompts/offline.wav"
  },
```

- [ ] **Step 4: Sanity-check the suite still passes**

Run: `npx vitest run tests/js`
Expected: PASS (nothing reads the new keys yet).

- [ ] **Step 5: Commit**

```bash
git add scripts/generate_dev_story.py src/static/content/it/prompts/audio-retry.wav src/static/content/it/prompts/offline.wav src/static/content/it/manifest.json
git commit -m "feat(fixtures): dev chimes + manifest keys for audio-retry and offline prompts (AI-367)"
```

---

### Task 3: Store — the `audioError` state

**Files:**
- Modify: `src/static/js/store.js`
- Test: `tests/js/store.test.js`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `state.audioError: boolean` (initial `false`); transitions `store.audioError()` (no args, only acts when `screen === "player"`) and `store.retryAudio()` (no args, clears the flag and sets `playing: true`). Every transition that leaves or restarts the player (`openStory`, `exitStory`, `replay`, `toShelf`) clears `audioError: false`. Tasks 4 and 5 rely on these exact names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/js/store.test.js` (match the file's existing test style — read it first):

```js
describe("audio-error state (AI-367)", () => {
  it("audioError() marks the player errored; it is a no-op off the player", () => {
    const store = createStore();
    store.audioError(); // still on the shelf
    expect(store.state.audioError).toBe(false);
    store.openStory();
    store.audioError();
    expect(store.state.audioError).toBe(true);
  });

  it("retryAudio() clears the error and plays", () => {
    const store = createStore();
    store.openStory();
    store.togglePlay(); // paused when the failure hit
    store.audioError();
    store.retryAudio();
    expect(store.state.audioError).toBe(false);
    expect(store.state.playing).toBe(true);
  });

  it("leaving or restarting the story clears the error", () => {
    const store = createStore();
    store.openStory();
    store.audioError();
    store.exitStory();
    expect(store.state.audioError).toBe(false);

    store.openStory();
    store.audioError();
    store.replay();
    expect(store.state.audioError).toBe(false);

    store.audioError();
    store.toShelf();
    expect(store.state.audioError).toBe(false);
  });

  it("a fresh openStory() never starts errored", () => {
    const store = createStore();
    store.openStory();
    store.audioError();
    store.exitStory();
    store.openStory();
    expect(store.state.audioError).toBe(false);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npx vitest run tests/js/store.test.js`
Expected: FAIL — `store.audioError is not a function`.

- [ ] **Step 3: Implement in `store.js`**

Add to `initialState()`:

```js
    audioError: false, // narration failed to load; the sleeping bird holds the stage
```

Add `audioError: false` to the patch objects of **both branches** of `openStory()`, and of `exitStory()`, `replay()`, and `toShelf()`. Add the two transitions after `togglePlay()`:

```js
    // Narration for the current page failed to load (AI-367). Only the
    // player shows the sleeping bird; a stale failure after exiting is noise.
    audioError() {
      if (state.screen !== "player") return;
      set({ audioError: true });
    },

    // The bird was tapped: clear the error and play — sync() re-narrates.
    retryAudio() {
      if (!state.audioError) return;
      set({ audioError: false, playing: true });
    },
```

- [ ] **Step 4: Run the full JS unit suite**

Run: `npx vitest run tests/js`
Expected: PASS (existing store tests unaffected — the new key defaults to `false`).

- [ ] **Step 5: Commit**

```bash
git add src/static/js/store.js tests/js/store.test.js
git commit -m "feat(player): audioError store state + retry transition (AI-367)"
```

---

### Task 4: Playback — failure wiring, spoken retry prompt, prefetch banking

**Files:**
- Modify: `src/static/js/playback.js`
- Modify: `tests/js/player.test.js` (prefetch total 18 → 19)
- Modify: `tests/e2e/playback-loop.spec.js` (prefetch total 18 → 19)
- Test: `tests/js/playback.test.js`

**Interfaces:**
- Consumes: `store.audioError()` / `store.retryAudio()` / `state.audioError` from Task 3; manifest key `prompts.audio_retry` from Tasks 1–2.
- Produces: on narration load failure the store enters `audioError` and the retry prompt speaks; `retryAudio()` makes `sync()` re-narrate the same page; `prompts.audio_retry` is added to the cover-tap prefetch list (asset total becomes 19).

- [ ] **Step 1: Write the failing unit tests**

`tests/js/playback.test.js` already has the helpers these tests need (`fakeEngine`, `fixtureStory`, `narrations()`, `promptsSpoken()`, `flush()`); its `fakeEngine().playNarration` never rejects, so the new suite wraps it.

First, extend the file's `PROMPTS` constant (line 101) so the retry prompt exists:

```js
const PROMPTS = {
  story_start: "/p/story-start.wav",
  end: "/p/end.wav",
  audio_retry: "/p/audio-retry.wav",
};
```

and extend the existing prefetch assertion ("whole-story prefetch starts on the cover tap…", ~line 148) to expect the third URL:

```js
    expect(prefetcher.prefetchStory).toHaveBeenCalledWith(story, [
      PROMPTS.story_start,
      PROMPTS.end,
      PROMPTS.audio_retry,
    ]);
```

Then append the new suite:

```js
describe("Audio won't load (AI-367) — the bird speaks, a tap wakes the story", () => {
  // The file's fakeEngine never fails; wrap it so the first `failures`
  // narrations reject the way a dead network makes the real engine reject.
  function failingEngine(failures = Infinity) {
    const wrapped = fakeEngine();
    let remaining = failures;
    const playNarration = wrapped.playNarration;
    wrapped.playNarration = async (url, opts) => {
      if (remaining > 0) {
        remaining -= 1;
        wrapped.calls.push(["narration", url]); // the attempt happened; the wire died
        throw new Error(`audio fetch failed: ${url}`);
      }
      return playNarration(url, opts);
    };
    return wrapped;
  }

  it("a narration load failure flips the store to audioError and speaks the retry prompt", async () => {
    engine = failingEngine();
    playback = createPlayback({ store, engine, prefetcher, prompts: PROMPTS });
    await playback.openStory(fixtureStory());
    engine.endPrompt(); // "Si parte!" ends; page 1's voice rejects
    await flush();

    expect(store.state.audioError).toBe(true);
    expect(promptsSpoken()).toContain(PROMPTS.audio_retry);
  });

  it("retryAudio() re-narrates the same page once the network is back", async () => {
    engine = failingEngine(1); // fail once, then recover
    playback = createPlayback({ store, engine, prefetcher, prompts: PROMPTS });
    await playback.openStory(fixtureStory());
    engine.endPrompt();
    await flush();
    expect(store.state.audioError).toBe(true);

    store.retryAudio(); // the bird was tapped
    await flush();
    expect(store.state.audioError).toBe(false);
    expect(narrations()).toEqual(["/s/p1.wav", "/s/p1.wav"]);
  });

  it("while the bird holds the stage, sync neither narrates nor pauses", async () => {
    engine = failingEngine();
    playback = createPlayback({ store, engine, prefetcher, prompts: PROMPTS });
    await playback.openStory(fixtureStory());
    engine.endPrompt();
    await flush();
    const callsWhenErrored = engine.calls.length;

    store.togglePlay(); // stray taps land under the overlay
    store.togglePlay();
    expect(engine.calls.length).toBe(callsWhenErrored);
  });

  it("a failure that lands after the child left the player never wakes the bird", async () => {
    const wrapped = fakeEngine();
    let rejectLate;
    const playNarration = wrapped.playNarration;
    let firstCall = true;
    wrapped.playNarration = async (url, opts) => {
      if (firstCall) {
        firstCall = false;
        return new Promise((_, reject) => {
          rejectLate = reject; // page 1's voice hangs on the wire
        });
      }
      return playNarration(url, opts);
    };
    engine = wrapped;
    playback = createPlayback({ store, engine, prefetcher, prompts: PROMPTS });
    await playback.openStory(fixtureStory());
    engine.endPrompt();

    store.exitStory(); // the child bails out to the shelf...
    rejectLate(new Error("late failure"));
    await flush();

    // ...so the stale failure is noise: no bird, no retry prompt.
    expect(store.state.audioError).toBe(false);
    expect(promptsSpoken()).not.toContain(PROMPTS.audio_retry);
  });

  it("a missing audio_retry prompt still shows the bird, just silently", async () => {
    engine = failingEngine();
    playback = createPlayback({
      store,
      engine,
      prefetcher,
      prompts: { story_start: PROMPTS.story_start, end: PROMPTS.end },
    });
    await playback.openStory(fixtureStory());
    engine.endPrompt();
    await flush();
    expect(store.state.audioError).toBe(true);
    expect(promptsSpoken()).not.toContain(PROMPTS.audio_retry);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npx vitest run tests/js/playback.test.js`
Expected: the new tests FAIL (failure currently only `console.warn`s).

- [ ] **Step 3: Implement in `playback.js`**

Replace the `narrate()` catch (line 34):

```js
      .catch((err) => {
        if (narratingPage !== index) return; // a superseded voice's failure is noise
        narratingPage = null;
        console.warn("narration failed", err);
        store.audioError();
        // The bird speaks its line — banked by prefetch on the cover tap,
        // so it plays even on the flaky network that caused the failure.
        if (prompts.audio_retry) engine.playPrompt(prompts.audio_retry).catch(() => {});
      });
```

In `sync()`, hold the stage while the bird shows — after the `resumeOpen || choiceOpen` guard:

```js
    if (state.resumeOpen || state.choiceOpen) return;
    if (state.audioError) return; // the sleeping bird holds the stage until a tap retries
```

In `openStory()`, bank the retry prompt with the others:

```js
      const promptUrls = [prompts.story_start, prompts.end, prompts.audio_retry].filter(Boolean);
```

- [ ] **Step 4: Run unit tests**

Run: `npx vitest run tests/js/playback.test.js`
Expected: PASS.

- [ ] **Step 5: Update the two prefetch-total tests (18 → 19)**

- `tests/js/player.test.js` (~line 148): rename `"whole-story prefetch banks all 18 assets around the cover tap — pages and prompts"` to say 19, and change the assertion to `{ total: 19, loaded: 19, failed: 0 }`.
- `tests/e2e/playback-loop.spec.js` (~lines 93–99): the `waitForFunction` floor `status.total >= 18` becomes `>= 19` and `expect(status.total).toBe(18)` becomes `toBe(19)`. The comment "8 audio + 8 images + the start and end prompts" becomes "8 audio + 8 images + the start, end, and audio-retry prompts". `storyAssetRequests.size` stays 16 (prompts are not under the story base).

- [ ] **Step 6: Run the full JS unit suite**

Run: `npx vitest run tests/js`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/static/js/playback.js tests/js/playback.test.js tests/js/player.test.js tests/e2e/playback-loop.spec.js
git commit -m "feat(player): narration failure enters audioError and speaks the retry prompt (AI-367)"
```

---

### Task 5: The sleeping bird — audio-error overlay UI

**Files:**
- Modify: `src/static/js/screens.js` (new `buildAudioError`)
- Modify: `src/static/js/main.js` (render wiring)
- Modify: `src/static/css/player.css` (bird illustration + overlay)
- Test: `tests/js/player.test.js`

**Interfaces:**
- Consumes: `state.audioError` / `store.retryAudio()` from Task 3; the failure wiring from Task 4.
- Produces: `buildAudioError(store)` exported from `screens.js`; a `.overlay.audio-error` element containing `.bird` appears inside the player screen while `state.audioError`, and disappears on tap.

- [ ] **Step 1: Write the failing integration test**

Append to `tests/js/player.test.js` (reusing `routedFetch`, `fakeEngine`, and the `openFirstCover` pattern already in the file):

```js
describe("audio-error overlay (AI-367): the sleeping bird", () => {
  it("a narration failure shows the bird with the retry line; a tap retries", async () => {
    const engine = fakeEngine();
    let failNarration = true;
    const playNarration = engine.playNarration.bind(engine);
    engine.playNarration = async (url, opts) => {
      if (failNarration) throw new Error("audio fetch failed");
      return playNarration(url, opts);
    };

    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: routedFetch, engine });
    document.querySelector(".cover").click();
    await vi.waitFor(() => expect(running.playback.hasStory()).toBe(true));
    engine.endPrompt(); // "Si parte!" ends; page 1 narration fails

    await vi.waitFor(() => expect(document.querySelector(".audio-error")).not.toBeNull());
    expect(document.querySelector(".audio-error .bird")).not.toBeNull();
    expect(document.querySelector(".audio-error .prompt").textContent).toBe(
      "Oh! La storia fa un pisolino. Tocca l'uccellino per svegliarla.",
    );

    failNarration = false;
    document.querySelector(".audio-error").click();
    await vi.waitFor(() => expect(document.querySelector(".audio-error")).toBeNull());
    expect(running.store.state.audioError).toBe(false);
    await vi.waitFor(() => expect(engine.state).toBe("playing"));
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npx vitest run tests/js/player.test.js`
Expected: the new test FAILS (no `.audio-error` element renders).

- [ ] **Step 3: Add `buildAudioError` to `screens.js`**

```js
// The audio-retry state (AI-367): narration failed to load. The whole
// overlay is one big tap target — never a small button for small hands.
export function buildAudioError(store) {
  const overlay = el("button", "overlay audio-error", { "aria-label": "riprova" });
  const bird = el("div", "bird");
  bird.appendChild(el("div", "wing"));
  const prompt = el("div", "prompt");
  prompt.textContent = "Oh! La storia fa un pisolino. Tocca l'uccellino per svegliarla.";
  overlay.append(bird, prompt);
  overlay.addEventListener("click", () => store.retryAudio());
  return overlay;
}
```

- [ ] **Step 4: Wire it into `main.js` render()**

Import `buildAudioError` alongside the other builders. In `render()`:
- extend `structural` with `state.audioError !== shown.audioError`;
- in the player branch, after the resume overlay line:

```js
        if (state.audioError) playerScreen.appendChild(buildAudioError(store));
```

- extend the `shown` bookkeeping objects (both the initial `let shown = {...}` and the assignment after rebuild) with `audioError: state.audioError` (initial `false`).

- [ ] **Step 5: Draw the bird in `player.css`**

Add after the existing overlay section, following the `.mascot` gradient/pseudo-element technique and the file's comment style:

```css
/* ── Audio-error: the sleeping bird (AI-367) ───────────────────────── */

.audio-error {
  gap: 22px;
}

/* A round little bird, eyes closed mid-nap. Same technique as .mascot:
   one gradient body, pseudo-element eyes — here closed arcs, not dots. */
.audio-error .bird {
  width: 140px;
  height: 120px;
  position: relative;
  border-radius: 50% 50% 46% 46%;
  background: radial-gradient(circle at 35% 30%, #cfe3f5, #8fb4d9 78%);
  box-shadow: 0 0 0 12px rgba(143, 180, 217, 0.15);
  animation: bird-breathe 3.2s ease-in-out infinite;
}

/* Closed eyes: two small downward arcs. */
.audio-error .bird::before,
.audio-error .bird::after {
  content: "";
  position: absolute;
  top: 44px;
  width: 16px;
  height: 8px;
  border-bottom: 3px solid #3d4f63;
  border-radius: 0 0 16px 16px;
}

.audio-error .bird::before {
  left: 34px;
}

.audio-error .bird::after {
  right: 34px;
}

/* A folded wing, tucked in for the nap. */
.audio-error .bird .wing {
  position: absolute;
  bottom: 18px;
  left: 50%;
  transform: translateX(-50%);
  width: 56px;
  height: 30px;
  border-radius: 50% 50% 60% 60%;
  background: rgba(61, 79, 99, 0.25);
}

.audio-error .prompt {
  max-width: 280px;
  font: 700 22px/1.35 var(--font-app);
}

@keyframes bird-breathe {
  0%,
  100% {
    transform: scale(1);
  }
  50% {
    transform: scale(1.04);
  }
}

@media (prefers-reduced-motion: reduce) {
  .audio-error .bird {
    animation: none;
  }
}
```

- [ ] **Step 6: Run the JS unit suite**

Run: `npx vitest run tests/js`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/static/js/screens.js src/static/js/main.js src/static/css/player.css tests/js/player.test.js
git commit -m "feat(player): sleeping-bird audio-error overlay, tap to retry (AI-367)"
```

---

### Task 6: The clouds — offline boot gate

**Files:**
- Modify: `src/static/js/screens.js` (new `buildOffline`)
- Modify: `src/static/js/main.js` (boot gate replacing the silent fallback)
- Modify: `src/static/css/player.css` (clouds illustration)
- Test: `tests/js/player.test.js` (replaces the "falls back to the built-in covers" test)

**Interfaces:**
- Consumes: dev manifest/offline fixture from Task 2 (same-origin URL convention `/static/content/{lang}/prompts/offline.wav`).
- Produces: `buildOffline(onRetry)` exported from `screens.js`; on cold-load manifest failure `init()` shows `.screen.offline`, each tap speaks the offline prompt and retries the fetch; `init()` resolves once the manifest loads. `manifestLoaded` remains `manifest !== null`.

- [ ] **Step 1: Rewrite the manifest-failure unit test to expect the clouds**

In `tests/js/player.test.js`, **replace** the test `"falls back to the built-in covers when the manifest is unreachable"` (~line 113) with:

```js
  it("a dead manifest shows the clouds; when the sky clears, a tap brings the shelf (AI-367)", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    let manifestUp = false;
    const flakyFetch = async (url) => {
      if (String(url).endsWith("manifest.json")) {
        if (!manifestUp) return { ok: false, status: 503 };
        return { ok: true, json: async () => manifest };
      }
      return { ok: true, arrayBuffer: async () => new ArrayBuffer(1) };
    };
    const engine = fakeEngine();
    const promptUrls = [];
    const playPrompt = engine.playPrompt.bind(engine);
    engine.playPrompt = async (url, opts) => {
      promptUrls.push(url);
      return playPrompt(url, opts);
    };

    const pending = init(document, { fetchFn: flakyFetch, engine });

    // The clouds hold the boot: no shelf, no spinner, one big tap target.
    await vi.waitFor(() => expect(document.querySelector(".offline")).not.toBeNull());
    expect(document.querySelector(".offline .prompt").textContent).toBe(
      "Le nuvole hanno preso le storie. Riprova tra poco!",
    );
    expect(document.querySelector(".cover")).toBeNull();

    // A tap while still offline speaks the line and retries — clouds remain.
    document.querySelector(".offline").click();
    await vi.waitFor(() =>
      expect(promptUrls).toContain("/static/content/it/prompts/offline.wav"),
    );
    await vi.waitFor(() => expect(document.querySelector(".offline")).not.toBeNull());

    // The network returns; the next tap loads the shelf and init resolves.
    manifestUp = true;
    document.querySelector(".offline").click();
    running = await pending;
    expect(running.manifestLoaded).toBe(true);
    expect(document.querySelectorAll(".shelf .cover").length).toBeGreaterThan(0);
  });
```

- [ ] **Step 2: Run to verify failure**

Run: `npx vitest run tests/js/player.test.js`
Expected: the rewritten test FAILS (today a dead manifest silently falls back to built-in covers).

- [ ] **Step 3: Add `buildOffline` to `screens.js`**

```js
// The offline state (AI-367): the shelf manifest failed on cold load.
// The whole screen is the retry button; each tap speaks the line again.
export function buildOffline(onRetry) {
  const screen = el("button", "screen offline", { "aria-label": "riprova" });
  const clouds = el("div", "clouds");
  for (let i = 0; i < 3; i++) clouds.appendChild(el("div", "puff"));
  const prompt = el("div", "prompt");
  prompt.textContent = "Le nuvole hanno preso le storie. Riprova tra poco!";
  screen.append(clouds, prompt);
  screen.addEventListener("click", onRetry);
  return screen;
}
```

- [ ] **Step 4: Replace the silent fallback in `main.js` with the boot gate**

Import `buildOffline`. In `fetchManifest`, update the comment (the seam is being closed):

```js
  } catch (err) {
    // Cold-load failure: the clouds screen (AI-367) holds the boot until
    // a tap retries and the manifest answers.
    console.warn("manifest unavailable", err);
    return null;
  }
```

In `init()`, move `engine ??= createAudioEngine();` **above** the manifest fetch, then replace the single `const manifest = ...` line with the gate:

```js
  engine ??= createAudioEngine();

  let manifest = fetchFn ? await fetchManifest(assetBase, fetchFn, lang) : null;

  // The offline prompt is served same-origin: in this state the manifest —
  // and R2 with it — is unreachable by definition, but the origin that
  // delivered this page is provably up.
  const offlinePromptUrl = `/static/content/${lang}/prompts/offline.wav`;

  while (fetchFn && manifest === null) {
    await new Promise((resolve) => {
      app.replaceChildren(
        buildOffline(() => {
          // The tap is also the wake gesture: unlock, speak, retry.
          engine
            .unlock()
            .then(() => engine.playPrompt(offlinePromptUrl))
            .catch((err) => console.warn("offline prompt skipped", err));
          resolve();
        }),
      );
    });
    manifest = await fetchManifest(assetBase, fetchFn, lang);
  }

  const stories = manifest?.stories ?? fallbackShelf;
```

(`const stories` and everything after stay as they are; the old `const manifest = fetchFn ? ... : null;` line is what gets replaced. `fallbackShelf` still covers the no-fetch dev path, e.g. opening the template without a network layer.)

- [ ] **Step 5: Draw the clouds in `player.css`**

```css
/* ── Offline: soft clouds took the stories (AI-367) ─────────────────── */

.screen.offline {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 28px;
  text-align: center;
}

.offline .clouds {
  position: relative;
  width: 220px;
  height: 100px;
}

/* Three overlapping puffs, the middle one proud — same gradient trick
   as .mascot, drifting a little unless motion is reduced. */
.offline .puff {
  position: absolute;
  bottom: 0;
  border-radius: 50%;
  background: radial-gradient(circle at 40% 30%, #f3f0ea, #cfd6df 80%);
  box-shadow: 0 0 0 10px rgba(207, 214, 223, 0.18);
  animation: cloud-drift 6s ease-in-out infinite;
}

.offline .puff:nth-child(1) {
  left: 0;
  width: 84px;
  height: 64px;
}

.offline .puff:nth-child(2) {
  left: 58px;
  bottom: 10px;
  width: 110px;
  height: 84px;
  animation-delay: -2s;
}

.offline .puff:nth-child(3) {
  right: 0;
  width: 78px;
  height: 58px;
  animation-delay: -4s;
}

.offline .prompt {
  max-width: 280px;
  font: 700 22px/1.35 var(--font-app);
  color: var(--greeting-sub);
}

@keyframes cloud-drift {
  0%,
  100% {
    transform: translateX(0);
  }
  50% {
    transform: translateX(8px);
  }
}

@media (prefers-reduced-motion: reduce) {
  .offline .puff {
    animation: none;
  }
}
```

- [ ] **Step 6: Run the full JS unit suite**

Run: `npx vitest run tests/js`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/static/js/screens.js src/static/js/main.js src/static/css/player.css tests/js/player.test.js
git commit -m "feat(player): offline clouds boot gate, tap speaks and retries (AI-367)"
```

---

### Task 7: E2E acceptance — request interception for both failures

**Files:**
- Create: `tests/e2e/failure-states.spec.js`

**Interfaces:**
- Consumes: everything above; the dev fixture URLs (`/static/content/it/prompts/audio-retry.wav`, `.../offline.wav`); story audio lives at `/static/content/it/stories/la-barchetta-e-la-luna/pN.{hash}.wav`.
- Produces: the two acceptance scenarios from the Linear issue, green.

- [ ] **Step 1: Write the spec**

```js
// Failure-state acceptance (AI-367), named after docs/product.md ->
// "When Things Go Wrong": never dead air, never a spinner. Playwright's
// route interception plays the part of the truly bad night.

import { expect, test } from "@playwright/test";

const STORY_AUDIO = "**/stories/**/*.wav";

test.describe("When Things Go Wrong (product.md)", () => {
  test("audio won't load: the sleeping bird appears, speaks, and a tap wakes the story", async ({ page }) => {
    const retryPromptRequests = [];
    page.on("request", (request) => {
      if (request.url().includes("/prompts/audio-retry")) retryPromptRequests.push(request.url());
    });

    // Every narration file is dead before the night begins; the prompts live.
    await page.route(STORY_AUDIO, (route) => route.abort());

    await page.goto("/?theme=dusk");
    await page.locator(".greeting").click();
    await page.waitForFunction(() => window.__shell?.engine.unlocked === true);
    await page.locator(".cover").first().click();

    // Then the bird holds the stage and its line is spoken — never silence.
    await expect(page.locator(".audio-error")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".audio-error .bird")).toBeVisible();
    await expect(page.locator(".audio-error .prompt")).toHaveText(
      "Oh! La storia fa un pisolino. Tocca l'uccellino per svegliarla.",
    );
    expect(retryPromptRequests.length).toBeGreaterThan(0);

    // The network returns; tapping the bird wakes the story cleanly.
    await page.unroute(STORY_AUDIO);
    await page.locator(".audio-error").click();
    await expect(page.locator(".audio-error")).toHaveCount(0);
    await expect(page.locator(".page-wash.current")).toHaveAttribute("data-page", "1", {
      timeout: 15_000,
    });
  });

  test("the shelf won't load: clouds speak, and when the sky clears a tap brings the stories", async ({ page }) => {
    const offlinePromptRequests = [];
    page.on("request", (request) => {
      if (request.url().includes("/prompts/offline")) offlinePromptRequests.push(request.url());
    });

    await page.route("**/manifest.json", (route) => route.abort());
    await page.goto("/?theme=dusk");

    // Clouds, the line, no covers, no spinner.
    await expect(page.locator(".offline")).toBeVisible();
    await expect(page.locator(".offline .prompt")).toHaveText(
      "Le nuvole hanno preso le storie. Riprova tra poco!",
    );
    await expect(page.locator(".cover")).toHaveCount(0);

    // A tap while still offline speaks and retries — the clouds remain.
    await page.locator(".offline").click();
    await expect(page.locator(".offline")).toBeVisible();
    expect(offlinePromptRequests.length).toBeGreaterThan(0);

    // The sky clears: the next tap loads the shelf.
    await page.unroute("**/manifest.json");
    await page.locator(".offline").click();
    await expect(page.locator(".cover").first()).toBeVisible({ timeout: 10_000 });
  });
});
```

- [ ] **Step 2: Run the new spec**

Run: `npx playwright test tests/e2e/failure-states.spec.js`
Expected: PASS (2 tests). If the bird never appears, check that the story-audio glob actually matches the fixture URLs (`pN.{hash}.wav` under `/static/content/it/stories/...`) before touching player code.

- [ ] **Step 3: Run the whole E2E + unit suites**

Run: `npx playwright test && npx vitest run tests/js`
Expected: PASS, including the updated 19-asset prefetch assertions.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/failure-states.spec.js
git commit -m "test(e2e): failure-state acceptance — bird retry + offline clouds (AI-367)"
```
