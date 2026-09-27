# Audio Wake Implementation Plan (B9)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Narration never goes silent without a way back. The AudioContext is woken on every real user activation and whenever the page returns to view. A stalled voice is paused at its exact position, and the existing sleeping-bird audio-error state takes over. Tapping the bird resumes the story mid-sentence.

**Symptom (production):** "Sometimes the audio doesn't work" on cantastorie.onrender.com. No error is shown, the page never turns, and a reload fixes it.

**Tracking:** Linear AI-461. Branch: `fix/ai-461-audio-wake`. Release-readiness audit blocker B9.

## Root causes (from reading the code at commit 31ede31)

| Cause | Where | Confidence |
|-------|-------|------------|
| Nothing resumes the context after the tab is backgrounded or the device sleeps. | `docs/audits/release-readiness.md` B9 | High. This best explains the "sometimes". |
| `start()` on a context that isn't running throws nothing, and `onended` never fires. `playback.js` never reaches its `.catch`, so the sleeping bird never appears. | `audio-engine.js:96-106`, `playback.js:23-44` | High |
| `unlock()` resumes only `"suspended"`. Safari also reports `"interrupted"` (calls, Siri, other audio). | `audio-engine.js:120-123` | High |
| Unlock is a single `pointerdown` listener with `once: true`. `pointerdown` from touch is not activation-triggering, so the first tap may fail and nothing retries. | `main.js:317-332` | Likely. Confirm with remote devtools: log `ctx.state` on a silent device. |
| iOS mutes Web Audio when the ringer switch is on silent (unlike `<audio>`). | platform | High |

## Sources

- HTML Standard, activation-triggering input events. These are `keydown`, `mousedown`, `pointerdown` with a mouse `pointerType`, `pointerup` with any other `pointerType`, and `touchend`: https://html.spec.whatwg.org/multipage/interaction.html#activation-triggering-input-event
- Web Audio API, `AudioContext.resume()`: https://webaudio.github.io/web-audio-api/#dom-audiocontext-resume
- W3C Audio Session API, `navigator.audioSession.type = "playback"` (Safari 16.4+): https://w3c.github.io/audio-session/
- MDN, `visibilitychange`: https://developer.mozilla.org/en-US/docs/Web/API/Document/visibilitychange_event

**Tech stack:** vanilla ES modules, the Web Audio engine, Vitest (jsdom) with the injected fake context in `tests/js/audio-engine.test.js`, and Playwright E2E.

## Global Constraints

- Settled decisions hold (`AGENTS.md`, `docs/architecture.md`): **no `<audio>` tags**, no framework, no bundler, no new dependencies.
- Follow the injected-dependency factory style already used in `src/static/js/` (`createAudioEngine({ createContext, fetchFn })`).
- Do not touch the narration FSM transitions or the epoch/`pendingStart` guards in `audio-engine.js`. This plan changes **when** the engine is woken and adds a stall detector.
- The shelf greeting plays at most once per page life, never on every tap.
- Nothing snaps at bedtime: no new hard stops, spoken prompts or spinners.
- `unlock()` must stay cheap and idempotent. It gets called on every tap.
- The `visibilitychange` unlock is best-effort. It is not a user activation, and iOS Safari may refuse to resume from it. The next tap is the guaranteed wake.
- Green before every commit: `npx vitest run`, `npx playwright test`, `make check`. Use conventional commits.
- Out of scope: moving assets off `*.r2.dev` to a custom domain. List it as a PR follow-up only.

---

### Task 1: The engine wakes from any state that isn't running

**Files:**
- Modify: `src/static/js/audio-engine.js` (`unlock`, `ensureContext`)
- Test: `tests/js/audio-engine.test.js` (`describe("unlock")`)

- [ ] **Step 1: Write failing tests**
  - `unlock()` calls `ctx.resume()` when `ctx.state` is `"interrupted"`.
  - `unlock()` does not call `resume()` when the state is `"running"` or `"closed"`.
  - Two concurrent `unlock()` calls create one context and don't throw.
  - A rejected `resume()` is swallowed: `unlock()` resolves and `engine.unlocked` stays `false`.
  - When `globalThis.navigator.audioSession` exists, the first `ensureContext()` sets its `type` to `"playback"`, only once. When it's absent, or its setter throws, nothing throws. Stub it with `vi.stubGlobal` or a property define, and restore after each test.
- [ ] **Step 2: Implement**
  - `unlock()`: resume when `ctx.state !== "running" && ctx.state !== "closed"`, and catch resume errors (`console.warn`).
  - `ensureContext()`: on first creation, run a feature-detected `navigator.audioSession.type = "playback"` inside try/catch.
  - Update the `unlock` comment: it now runs on every activation, not only the first gesture.
- [ ] **Step 3:** `npx vitest run tests/js/audio-engine.test.js` passes. Commit `fix(audio): resume the context from any non-running state`.

### Task 2: Wake on every activation and on return to view

**Files:**
- Create: `src/static/js/wake.js` exporting `createWaker({ engine, root, doc, onFirstUnlock })`, which returns `{ dispose }`
- Modify: `src/static/js/main.js`. Replace the `root.addEventListener("pointerdown", …, { once: true })` block (~line 317) with `createWaker`.
- Test: `tests/js/wake.test.js` (new). Add to `tests/js/player.test.js` only if `main.js` wiring needs a smoke check.

**Behavior:**
- Capture listeners on `root` for `pointerdown`, `pointerup`, `touchend`, `click` and `keydown`, with no `once`. Each calls `engine.unlock()`.
  - `pointerdown` stays because it is the activation event for mouse users.
  - `pointerup` and `touchend` are the activation events for touch.
- A `visibilitychange` listener on `doc` calls `engine.unlock()` when `doc.visibilityState === "visible"`.
- After an unlock resolves with `engine.unlocked === true`, and before anything else, `greeted` is checked and set **synchronously**. `onFirstUnlock(event)` runs only for the call that flips it. One tap fires three events, so this guard is what stops a double greeting.
- `greeted` flips on the first successful unlock **even if** that event targeted `.cover` or `.settings-gear`. `main.js`'s `onFirstUnlock` skips the greeting for those targets, which keeps today's behavior (a cover-first tap never greets).

- [ ] **Step 1: Write failing tests** (use a fake engine whose `unlock` flips `unlocked`, and a jsdom root)
  - `pointerup`, `touchend`, `click`, `keydown` and `pointerdown` on the root each call `engine.unlock()`.
  - Repeated events keep calling `unlock()` (no `once`).
  - One gesture that fires `pointerup`, `touchend` and `click` calls `onFirstUnlock` exactly once.
  - An `unlock` that resolves while still locked doesn't call `onFirstUnlock`, and the next successful one does.
  - `visibilitychange` with `visibilityState` defined as `"visible"` calls `unlock()`. With `"hidden"` it doesn't.
  - `dispose()` removes every listener.
- [ ] **Step 2: Implement** `wake.js`, then wire it into `main.js`. The `onFirstUnlock` handler keeps the current greeting logic: play `manifest?.prompts?.greeting` unless `event.target.closest(".cover")` or `.closest(".settings-gear")`, and on error `console.warn("greeting skipped", err)`.
- [ ] **Step 3:** `npx vitest run` passes, and `npx playwright test tests/e2e/two-tap.spec.js` still passes (the two-tap budget holds). Commit `fix(player): wake audio on every activation and on return to view`.

### Task 3: Narration stall watchdog

**Files:**
- Modify: `src/static/js/playback.js`
- Test: `tests/js/playback.test.js`

**Behavior:** export `STALL_TIMEOUT_MS = 2500` and `STALL_POLL_MS = 500`. The watchdog **runs** while all of these hold:
- `story !== null` and `narratingPage !== null` (so an instance left behind by `switchLanguage` never fires)
- `state.screen === "player"`
- `state.playing`
- `!state.choiceOpen`, `!state.resumeOpen`, `!state.audioError`
- `!starting`

On each poll where `engine.state === "playing"`, it compares `engine.position()` with the last sample. The stall clock resets whenever the position advances, whenever `engine.state` isn't `"playing"`, and whenever `narratingPage` changes (a page turn). If the position hasn't advanced for `STALL_TIMEOUT_MS`:
1. `engine.pauseNarration()`. With the context suspended, `currentTime` is frozen, so this holds the exact spot.
2. `store.audioError()`
3. `if (prompts.audio_retry) engine.playPrompt(prompts.audio_retry).catch(() => {})`

`narratingPage` is **left alone**. On the retry tap, `store.retryAudio()` sets `playing: true`, and `sync` takes the existing `narratingPage === state.page && engine.state === "paused"` branch, so `resumeNarration()` continues mid-sentence. The retry tap is an activation, so Task 2 has already asked the context to resume.

**Wiring:** start and stop the watchdog from a single `updateWatchdog(state)` called at the **top** of `sync`, before any early return, and again wherever `narratingPage` changes. `createPlayback` takes injected `setIntervalFn` and `clearIntervalFn` (defaults: the globals). It uses the fake engine's own `position()`, not a clock.

- [ ] **Step 1: Write failing tests** (vitest fake timers, or injected interval functions; the fake engine gains `position()` and a settable position)
  - Position advancing: after 5 s, no `audioError`.
  - Position frozen for `STALL_TIMEOUT_MS` while playing: `pauseNarration` is called, then `store.audioError()`, exactly once, and the retry prompt is played.
  - Retry after a stall: `store.retryAudio()` produces a `resume` call and **no** new `narration` call for the same page.
  - No error when paused, ducked (`engine.state !== "playing"`), with the choice overlay open, with the resume overlay open, off the player screen, or while the start prompt is still speaking.
  - A page turn resets the stall clock: frozen for 2 s, turn the page, frozen for 2 s more, no error.
  - The interval is cleared when leaving the player and when `audioError` is set, with no leaked intervals.
  - A playback instance after `clearStory()` never fires, even if the shared engine stalls.
- [ ] **Step 2: Implement** the watchdog inside `createPlayback`.
- [ ] **Step 3:** `npx vitest run tests/js/playback.test.js` passes. Commit `fix(playback): stall watchdog hands a frozen voice to the sleeping bird`.

### Task 4: E2E regression

**Files:**
- Test: `tests/e2e/failure-states.spec.js` (extend)

Use `page.addInitScript` to wrap `window.AudioContext` so every constructed context is pushed onto `window.__contexts`. Don't add a test-only handle to production code.

- [ ] **Step 1:** Stall recovery test:
  1. Open a story the way the existing tests do.
  2. Wait for page 1 narration: `engine.state === "playing"`.
  3. `await window.__contexts[0].suspend()`.
  4. Assert `.audio-error` is visible within 5 s.
  5. Click `.audio-error`.
  6. Assert it's gone, `ctx.state === "running"`, and `window.__shell.engine.position()` advances over about 1 s.
- [ ] **Step 2:** Visibility and tap test:
  1. Suspend the context.
  2. Define `document.visibilityState` as `"visible"` (`Object.defineProperty(document, "visibilityState", { get: () => "visible", configurable: true })`) and dispatch `visibilitychange`.
  3. Click the page.
  4. Assert `ctx.state === "running"`.
- [ ] **Step 3:** `npx playwright test` passes. Commit `test(e2e): suspended context recovers via the bird and via a tap`.

### Task 5: Docs and audit

- [ ] In `docs/audits/release-readiness.md`, tick the B9 checkbox (~line 440) and add one line under the B9 section linking this plan and AI-461.
- [ ] In `docs/system-overview.md`, update the `main.js` row (line 89, "audio unlock on first gesture") to per-activation plus visibility unlock via `wake.js`. Add a `wake.js` row, and mention the stall watchdog in the `playback.js` row.
- [ ] `make check` and `make test` pass. Commit `docs: B9 audio wake landed`, push, and open a PR. The PR description covers the root causes (with confidence), the manual device checklist below, the `audioSession` side effect, and the r2.dev follow-up.

## Manual test checklist (real devices, for the PR)

1. iPad Safari: the first tap on the shelf plays the greeting.
2. iPad Safari: start a story, lock the screen for 30 s, unlock and tap. Narration continues, or the bird appears and a tap resumes mid-sentence.
3. iPhone with the ringer switch on silent: narration is audible.
4. iPhone with a podcast playing: opening a story pauses the podcast. This is the expected `audioSession = "playback"` side effect.
5. Android Chrome: switch apps mid-story, return and tap. Audio recovers.
6. Desktop Chrome with a mouse: no regression, and the two-tap budget holds.

Debug aid on a failing device: in remote devtools, log `ctx.state`. Anything other than `"running"` while the page is silent confirms the context-state causes.
