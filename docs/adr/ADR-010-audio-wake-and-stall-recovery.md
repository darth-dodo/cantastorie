# ADR-010: Audio Wake and Stall Recovery in the Child Player

**Date**: 2026-09-27
**Status**: Accepted
**Context**: Release-readiness blocker B9. In production, narration sometimes goes silent: no error, the page never turns, and a reload fixes it.
**Decider(s)**: Project Owner

---

## Summary

The child player's single `AudioContext` is no longer unlocked once and then trusted for the rest of the session. The player now does three things:

1. **Wakes the context on every user activation and when the page becomes visible again.** This lives in a new module, `wake.js`, and the engine's `unlock()` resumes from any state other than `running` or `closed`.
2. **Detects a stalled voice.** A narration position that stops advancing for 2.5 s, while the page is visible and playing, counts as a failure. The player then pauses the voice at its exact offset and shows the existing sleeping-bird audio-error state. A tap resumes mid-sentence.
3. **Sets `navigator.audioSession.type = "playback"`** where the API exists, so the iOS ringer switch no longer mutes narration.

The settled audio decisions stay as they are: Web Audio only, no `<audio>` elements, no new dependencies. So do the narration state machine and its epoch guards. This ADR records a change to *when* the engine is woken and *how a silent failure becomes visible*. It does not change how audio is played.

---

## Problem Statement

### The Challenge

Before this change, `main.js` unlocked the context from one `pointerdown` listener with `once: true` (commit 31ede31). Five causes, found by reading the code, can each leave the context not running while the player believes a story is playing:

| Cause | Confidence |
|-------|------------|
| Mobile browsers suspend the context when the tab is backgrounded or the device sleeps, and nothing resumed it. | High. This best explains "sometimes". |
| `source.start()` on a context that isn't running throws nothing, and `onended` never fires. The narration promise resolves, so the error path and the sleeping bird are never reached. | High |
| `unlock()` resumed only `"suspended"`. Safari also reports `"interrupted"` (calls, Siri, other audio). | High |
| `pointerdown` from touch is not an activation-triggering event, so the first tap could fail to unlock, and nothing retried. | Likely. Unverified on a device. |
| iOS mutes Web Audio when the ringer switch is on silent. `<audio>` elements are not muted. | High (platform behavior) |

### Why This Matters

- **The failure is silent.** A bedtime story that freezes with no sound and no visible state is the worst failure the product can have. docs/product.md "When Things Go Wrong" promises "never dead air".
- **Device sleep is the normal case.** The most likely event in the app's life is a tablet put down and picked back up.
- **A reload is not a recovery a child can perform.**

### Success Criteria

- [x] Every real user activation, and every return to view, attempts to wake the context.
- [x] The shelf greeting still plays at most once per page life.
- [x] A narration voice that stops advancing becomes the visible, tappable sleeping bird within about 3 s.
- [x] Tapping the bird resumes from the exact position, without restarting the page.
- [x] No prompt ever plays over narration because of this change.
- [x] Covered by unit tests (vitest) and by Chromium E2E tests that suspend a real `AudioContext`.
- [ ] Confirmed on real iOS and Android devices (manual checklist in the PR). Still pending.

---

## Context

### Current State

- `audio-engine.js` owns one `AudioContext` with decoded buffers and gain-ramped crossfades. See [architecture.md → The Player](../architecture.md#the-player).
- `playback.js` turns pages on narration `onended`. The only failure signal was a rejected load, which raised `store.audioError()` and showed the sleeping bird.
- The E2E suite had drifted to 13 of 14 failing and did not run in CI. It was repaired first, under AI-462 (PR #95), so this change could be tested in a real browser.

### Requirements

- **Keep the settled audio stack.** Web Audio only, no `<audio>` tags, no framework, no bundler, no new dependencies ([ADR-001](ADR-001-technology-stack.md)).
- **Nothing snaps at bedtime.** No new hard stops, spinners, or spoken interruptions.
- **Keep the two-tap budget.** The first tap wakes and greets; the cover tap starts the story.
- **Prompts and narration never overlap.** This is an existing engine rule.

---

## Options Considered

### Option A: Keep the once-only unlock and add only a `statechange` listener

**Description**: Listen for `AudioContext` `statechange` and call `resume()` when the state leaves `running`.

**Pros**:
- Small change, local to the engine.

**Cons**:
- `resume()` outside a user activation is refused on iOS, so this cannot recover the main case.
- Browsers do not reliably fire `statechange` for device sleep.
- It does nothing for the silent-failure half of the problem: `onended` never firing.

**Risks**: The failure stays silent whenever the resume is refused.

**Estimated Effort**: Low

### Option B: Unlock on every activation and on visibility, plus a narration stall watchdog (chosen)

**Description**:
- `wake.js` adds capture-phase listeners for `pointerdown`, `pointerup`, `touchend`, `click` and `keydown`, with no `once`, and calls `engine.unlock()` on each. It also calls `unlock()` on a `visibilitychange` to visible.
- `unlock()` is idempotent, resumes any state that is not `running` or `closed`, and swallows a rejected resume.
- `playback.js` polls `engine.position()` every 500 ms. It only polls while playing, visible, on the player screen, with no overlay open and no start prompt speaking. If the position is frozen for 2.5 s, it holds the voice at its exact offset and raises the audio error.

**Pros**:
- Covers every cause in the table.
- Uses the existing sleeping-bird state, so there is no new UI.
- The engine derives position from `ctx.currentTime`, which freezes while the context is suspended. That makes a frozen position both the detection signal and the correct resume offset.

**Cons**:
- A `unlock()` call on every tap. It is a no-op when the context is running.
- A 500 ms interval while a story plays.

**Risks**:
- The watchdog could fire during normal operation. Page turns, crossfades, ducking, the start prompt and replay were each checked in review. The watchdog pauses while the tab is hidden.

**Estimated Effort**: Medium

### Option C: Narrate with `<audio>` elements, which iOS does not mute on silent

**Description**: Move narration to media elements.

**Pros**:
- The ringer switch no longer mutes narration.
- Media elements have mature lifecycle events.

**Cons**:
- Media-element volume is read-only on iOS, which kills the mandated gain crossfades and prompt ducking.
- Reverses a settled decision ([ADR-001](ADR-001-technology-stack.md), architecture.md).

**Risks**: A large rewrite of the engine that is already the most-tested module.

**Estimated Effort**: High

### Option D: A silent keep-alive loop to stop the context from suspending

**Description**: Play an inaudible looping buffer so the browser never suspends the context.

**Pros**:
- Can prevent some tab-background suspensions.

**Cons**:
- Does not survive device sleep or Safari interruptions.
- Wastes battery.
- Conflicts with the privacy-first, low-footprint player.
- Fights the platform rather than recovering from it.

**Risks**: Browser policy changes can break it silently.

**Estimated Effort**: Low

---

## Comparison Matrix

Scores run 1–5, where higher is better. Weighted totals are out of 5.

| Criterion (weight) | A: statechange | B: wake + watchdog | C: `<audio>` | D: keep-alive |
|---|---|---|---|---|
| Recovers after device sleep (30%) | 1 | 5 | 3 | 1 |
| Makes silent failure visible (25%) | 1 | 5 | 3 | 1 |
| Keeps settled stack & crossfades (20%) | 5 | 5 | 1 | 4 |
| Bedtime calm, no overlap (15%) | 4 | 4 | 3 | 3 |
| Effort / reversibility (10%) | 5 | 4 | 1 | 4 |
| **Weighted total** | **2.65** | **4.75** | **2.4** | **2.2** |

---

## Decision

### Chosen Option

**Option B: unlock on every activation and on visibility, plus a narration stall watchdog.**

**Rationale**: It is the only option that addresses both halves of the bug:

- **The context not being woken**, including after device sleep, where only a later user activation is allowed to resume audio on iOS.
- **The failure being invisible**, because `onended` never fires.

It does this without touching the settled stack or the narration state machine.

**Key Factors**:
- **Unlock on every activation.** Activation-triggering events (HTML Standard) are `keydown`, `mousedown`, `pointerdown` with a mouse pointer, `pointerup` with any other pointer, and `touchend`. `pointerdown` stays in the listener set because it is the mouse activation event.
- **The watchdog pauses the voice before showing the bird.** It does not stop it. Stopping would clear `narratingPage`, and the retry tap would then restart the page from zero. Pausing lets the existing `paused → resumeNarration` path continue mid-sentence.
- **The watchdog does not count while the document is hidden.** On return, the visibility wake gets its chance before any error is raised.

**Trade-offs Accepted**:
- **The visibility wake is best-effort.** It is not a user activation, and iOS Safari may refuse it. When that happens, the next tap is the guaranteed wake, and the child may see the bird for one tap.
- **A stall never speaks the retry line.** A stall means the audio clock is frozen, so a prompt started then can only play late, on top of the resumed story. On a load failure, the retry line plays only when the context is unlocked. A bird that appears while the context is locked is therefore silent until tapped, though it stays visible.
- **The shelf greeting now loads first, then plays only if the child is still on the shelf.** On a slow network, the greeting is dropped once the child has moved on. This closes a freeze found in the final review: a late greeting could silence the story-start prompt, whose `onEnded` never fired, and hold the story forever.
- **`audioSession.type = "playback"` pauses other audio on the device**, such as a parent's podcast, while a story plays.

---

## Consequences

### Positive Outcomes

- Every known way for the context to go non-running now leads to either a recovery or the visible sleeping bird. No path ends in silent dead air.
- The iOS ringer switch no longer mutes stories, on Safari 16.4 and later.
- The E2E suite runs in CI again (AI-462), and three new tests suspend a real `AudioContext`:
  - a stall shows the bird, and a tap resumes
  - returning to view wakes the audio with no tap
  - a tap alone wakes the audio

  Each test fails when its own wake path is reverted.

### Negative Outcomes

- **More moving parts in the player:** one new module (`wake.js`) and an interval in `playback.js`.
- **The visibility E2E test depends on Chromium's sticky-activation behavior**, and cannot model iOS refusing the resume.

### Risks and Mitigation

| Risk | Mitigation |
|------|------------|
| Watchdog false positive during normal playback | It runs only while playing, visible, with no overlay and no start prompt. The stall clock resets on page change and on non-playing states. Unit specs cover page turn, duck, overlays, the start prompt and hidden tabs. |
| A context that reports `running` but whose clock is frozen. Not observed on any device; unverified. | The stall path plays no prompt, so nothing is queued to overlap. The device checklist logs `ctx.state` when the watchdog fires. If `running` ever appears, `unlock()` needs a `suspend()`/`resume()` kick. |
| The story-start prompt silenced or frozen before it releases page 1 | Fixed for the greeting race (shelf-only greeting, with a spec that runs the real engine). General hardening, a release ceiling or a "superseded" signal, is a recorded follow-up. |
| iOS behavior differs from Chromium's | The real-device checklist in the B9 PR must pass before B9 is treated as closed in the audit. |

---

## Implementation Plan

Delivered on `fix/ai-461-audio-wake` (Linear AI-461), stacked on the E2E repair `fix/ai-462-e2e-suite` (PR #95). See [docs/plans/2026-09-27-audio-wake.md](../plans/2026-09-27-audio-wake.md).

1. **Engine:** `unlock()` resumes from any state other than `running` or `closed`, and swallows a rejected resume. The first context sets `audioSession`.
2. **`wake.js`:** unlocks on every activation and on visibility. `onFirstUnlock` runs the greeting exactly once, and only while the child is still on the shelf.
3. **`playback.js`:** the stall watchdog (`STALL_TIMEOUT_MS = 2500`, `STALL_POLL_MS = 500`), with a shared `wakeBird()` helper for stalls and load failures.
4. **E2E tests** in `tests/e2e/failure-states.spec.js`.
5. **Docs:** the audit, `system-overview.md`, `architecture.md`, and this ADR.

**Rollback plan**: every step is additive and self-contained in `wake.js`, `playback.js` and a few lines of `audio-engine.js` and `main.js`.

- Reverting the branch restores the once-only unlock.
- Setting `STALL_TIMEOUT_MS` to `Infinity` disables only the watchdog.

No data, schema or content changes are involved.

---

## Validation

- **Automated:** 186 vitest specs pass, including one that wires the real engine, the real playback loop and the store, and reproduces the greeting freeze before the fix. 17 Playwright tests pass, with 1 skip that predates this change (AI-370), and `make check` is clean. This was checked on 2026-09-27.
- **Manual, pending:** the real-device checklist in the B9 PR.
  1. **iPad Safari:** the first tap greets.
  2. **iPad Safari:** lock mid-story for 30 s, then unlock and tap. The story resumes, or the bird appears and a tap resumes.
  3. **iPhone:** narration is audible with the ringer switch on silent.
  4. **iPhone:** a playing podcast pauses when a story opens.
  5. **Android Chrome:** switch apps mid-story, then return and tap. The story recovers.
  6. **Desktop:** no regression, and the two-tap budget holds.
- **Follow-up measure:** the audit's B9 line stays "fixed in code; device checklist pending" until the manual checklist passes.

---

## Related Decisions

- [ADR-001](ADR-001-technology-stack.md): the foundational stack (Web Audio, vanilla ES modules, no bundler). This ADR works within it.
- `docs/audits/release-readiness.md` B9: the blocker this resolves in code.
- AI-462 / PR #95: the E2E repair this change depends on.

---

## References

### Code

- `src/static/js/wake.js`: `createWaker({ engine, root, doc, onFirstUnlock })`
- `src/static/js/audio-engine.js`: `unlock()` and `ensureContext()`
- `src/static/js/playback.js`: `updateWatchdog`, `pollWatchdog` and `wakeBird`
- `src/static/js/main.js`: the waker wiring and the shelf-only greeting
- `tests/js/wake.test.js`, `tests/js/playback.test.js`, `tests/js/player.test.js` and `tests/e2e/failure-states.spec.js`

### External

- HTML Standard, activation-triggering input events: https://html.spec.whatwg.org/multipage/interaction.html#activation-triggering-input-event
- Web Audio API, `AudioContext.resume()`: https://webaudio.github.io/web-audio-api/#dom-audiocontext-resume
- W3C Audio Session API: https://w3c.github.io/audio-session/
- MDN, `visibilitychange`: https://developer.mozilla.org/en-US/docs/Web/API/Document/visibilitychange_event

---

## Metadata

- **ADR Number**: 010
- **Created**: 2026-09-27
- **Tags**: audio, player, reliability, mobile, release-blocker
