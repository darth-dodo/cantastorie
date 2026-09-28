# ADR-011: Image Safety via a Cross-Family Vision Judge

**Date**: 2026-09-28
**Status**: Accepted
**Context**: Release-readiness blocker B4. The **Calm pictures** rule was verdicted by the text safety gate before any image existed, so it passed on every story. Rendered illustrations were never checked.
**Decider(s)**: Project Owner

---

## Summary

**Calm pictures** leaves the text safety gate and becomes its own pipeline step, run after `illustrate`. The step works like this:

1. **Every image a child sees is judged.** That means each page, each choice card, and the cover. The character sheet is a reference input that never ships, so it is not judged.
2. **The judge is a vision model on OpenRouter**, reached through Pydantic AI like the other judged steps. It returns a typed pass/fail verdict with a short reason for each of three criteria: `no_text`, `nothing_frightening` and `calm`. Temperature is 0.
3. **The judge must come from a different model family than the image model.** This is the same cross-family rule the text gate already follows. The default is `openai/gpt-4.1-mini`, judging `google/gemini-3.1-flash-lite-image`. `Settings` refuses a config where the two families match.
4. **A failing image is redrawn, but only a bounded number of times.** Each redraw gets its own cache key. After `IMAGE_SAFETY_MAX_REGENERATIONS = 2` redraws that still fail, the story is rejected with `ImageSafetyRejectedError`. The error names the image slot, the criterion and the judge's reason, and the workshop records that text on the failed run.
5. **Verdicts are cached on the image bytes**, so an unchanged image costs zero judge calls on a re-run.

OpenRouter exposes **no provider safety setting** for the configured image model, so none is enabled (see [Context](#current-state)).

The text gate now judges **eight** rules. The product still has nine, because **Calm pictures** is still enforced, just on the pictures themselves.

---

## Problem Statement

### The Challenge

`safety_gate` serialized the story with `story.model_dump_json()` and asked the judge to rule on `calm_pictures`: "any image descriptions contain no text and nothing frightening". The gate runs inside `author_story`, before `illustrate_story`, so every `Page.image` was `null`. The `Story` model carries no image-description field either. The judge was being asked about pictures it could not see, so the verdict was always a pass.

The image request also set no provider safety parameters, and nothing checked an image after generation. Image safety rested entirely on the wording of `STYLE_PROMPT`. That is the prompt-level hope this codebase rejects elsewhere ("The prompt is hope; content_rules.py is the validation", `src/pipeline/steps/write.py`). Approved page prose is interpolated directly into the image prompt.

### Why This Matters

- **A rule that always passes is worse than no rule**, because it reads as coverage.
- **In the family lane, no human looks at the images before a child does** (release-readiness B2). Where the layered-gate rationale in docs/product.md "Safety" ("a model mistake needs a human mistake on top of it") has no human layer, a vacuous model gate leaves nothing.
- **Images are the one asset a pre-reader always consumes.** Text in an image is also a content-rule violation that the text limits cannot see.

### Success Criteria

- [x] `calm_pictures` is no longer a text-gate rule, and the text judge's instructions no longer mention it
- [x] Every page, choice card and cover is judged by a vision model after it is rendered
- [x] The vision judge is a different family than the image model, enforced at config load
- [x] A failing image is redrawn, and past the bound the story is rejected with a reason that names the image and the criterion
- [x] An unchanged story re-runs with zero image calls and zero judge calls
- [x] Tests make no network calls
- [ ] The judge's false-pass and false-fail rates are measured on real renders (**unverified**; see [Validation](#validation))

---

## Context

### Current State

- **Pipeline order** (`src/pipeline/generate.py`): `author_story` (write → text gate → bounded revise) → narrate → illustrate → assemble → stage.
- **Cross-family rule** (docs/architecture.md "Model roles"; ADR-001): the safety judge must be a different family than the writer, and `Settings` refuses the config otherwise. This ADR applies the same reasoning to images: a model grading its own family's output shares its blind spots.
- **Image transport** (`src/pipeline/steps/illustrate.py`): plain httpx to OpenRouter `/chat/completions` with `modalities: ["image", "text"]`, because pydantic-ai 2.5.0 cannot parse image *outputs*. Pydantic AI does support image *inputs*, which is all the judge needs.
- **Provider safety settings, checked 2026-09-28:**
  - The OpenRouter docs (context7 `/openrouterteam/docs`, plus the image-generation guide) document these `image_config` keys: `aspect_ratio`, `quality`, `size`, `background`, `output_format`, `output_compression` and `moderation`. `moderation` (`"auto"`, `"low"`) is an OpenAI image-model parameter. The docs mention no `safety_settings` or content-filter option for Gemini.
  - The public endpoint record for `google/gemini-3.1-flash-lite-image` (`GET /api/v1/images/models/google/gemini-3.1-flash-lite-image/endpoints`) lists `resolution`, `aspect_ratio`, `n` and `input_references` as supported parameters. It lists only `cachedContent` as a passthrough parameter, for both the Google Vertex and Google AI Studio providers.
  - **So there is no safety setting to enable on this request.** Google's own default filters still apply provider-side, but they can't be configured from here.

### Requirements

- Use OpenRouter only, with no new SDK or key (ADR-001; docs/architecture.md "Technology Stack").
- Use Pydantic AI for the structured verdict, as the other judged steps do.
- Go through `ArtifactCache` (`run_step` / `cache_key`).
- Send images as base64 data URLs, never as public URLs. Staged images are not public.
- Keep the bounded-retry shape the pipeline already uses: a plain loop, not a graph runtime.

---

## Options Considered

### Option A: A cross-family vision judge after illustrate, with bounded redraws (chosen)

**Description**: A new step, `src/pipeline/steps/image_safety.py`. `illustrate_safely` runs `illustrate_story`, judges every shown image with a Pydantic AI agent over OpenRouter, and bumps a per-slot redraw count for each failure. It then re-runs `illustrate_story`. The images that passed are cache hits, and only the failed ones are redrawn. Past the bound, it raises `ImageSafetyRejectedError`.

**Pros**:
- It judges the real artifact, and the verdict is a typed report, not a free-text guess
- It reuses every existing seam: OpenRouter, Pydantic AI, `ArtifactCache`, cross-family `Settings` validation
- Caching keeps re-runs free, and redraws touch only the failing image
- The failure reason is concrete and ends up on the run record

**Cons**:
- It adds one judge call per shown image, plus two calls per redraw
- The judge can itself be wrong, in either direction
- Judging runs sequentially, which adds latency to every run

**Risks**:
- A strict judge could reject calm images repeatedly and waste redraws. The bound caps the spend.
- A lenient judge could pass a subtly frightening image. The parent or operator review is still the backstop wherever one exists.

**Estimated Effort**: Small. One new module, a keyword argument on `illustrate_story`, one setting and validator, tests and docs.

### Option B: Provider moderation only

**Description**: Enable the image provider's safety or moderation parameters and trust them.

**Pros**:
- Zero extra calls
- No second model to configure

**Cons**:
- Not available. OpenRouter exposes no safety parameter for the configured Gemini image model (see [Current State](#current-state)).
- Provider filters target policy harms such as violence, sexual content or hate. They don't target "frightening to a 4-year-old", and they never target in-image text.

**Risks**: It would read as coverage without checking the product's own criteria. That is the failure this ADR exists to remove.

**Estimated Effort**: None, but there is nothing to switch on.

### Option C: Human review only

**Description**: Remove `calm_pictures` from the text gate and rely on the operator or parent looking at every image before approval.

**Pros**:
- Humans judge "frightening" best
- Zero model cost

**Cons**:
- The family lane has no reliable human reviewer before a child sees the images (B2)
- It doesn't scale to Phase 3 live generation, which is planned as unanimous-pass auto-publishing

**Risks**: The layered-gate rationale collapses to a single layer.

**Estimated Effort**: Trivial in code. The cost falls entirely on process.

### Option D: No check

**Description**: Delete `calm_pictures` and rely on `STYLE_PROMPT`.

**Pros**: Simplest.

**Cons**: It makes the gap permanent and contradicts the product's **Calm pictures** rule.

**Risks**: A frightening or text-bearing image reaches a child with nothing in the way.

**Estimated Effort**: Trivial.

---

## Comparison Matrix

Scores run 1–5, where higher is better. Weighted totals are out of 5.

| Criterion (weight) | A: vision judge | B: provider moderation | C: human only | D: no check |
|---|---|---|---|---|
| Checks the product's three criteria on real images (35%) | 4 | 1 | 5 | 1 |
| Covers the family lane without a human (25%) | 4 | 2 | 1 | 1 |
| Fits the settled stack (OpenRouter, Pydantic AI, cache) (15%) | 5 | 3 | 5 | 5 |
| Cost and latency (15%) | 3 | 5 | 4 | 5 |
| Effort and reversibility (10%) | 4 | 5 | 5 | 5 |
| **Weighted total** | **4.0** | **2.55** | **3.85** | **2.6** |

---

## Decision

### Chosen Option

**Option A: a cross-family vision judge after illustrate, with bounded redraws.**

**Rationale**: It is the only option that checks the product's own criteria on the images a child actually sees, without depending on a human who may not be there. It adds no dependency, key or framework. Every piece is a seam the pipeline already has.

**Key Factors**:
- The family lane has no human image review (B2)
- The provider exposes no safety setting to lean on
- Cross-family judging is already a settled invariant, and extending it to images is one validator

**Trade-offs Accepted**:
- Extra calls per story and more sequential latency (see [Cost estimate](#cost-estimate-unverified))
- A redraw uses the same prompt, relying on generation variance rather than steering by the failure reason. This keeps the redraw's cache key independent of judge output.
- The judge's own error rate is unmeasured at launch

### Cost estimate (unverified)

The shown-image counts come from the content rules: 10 pages per heard path (`PAGE_COUNT`), and 4 pages per branch arm (`ARM_PAGES`).

| Story | Judged images | Judge calls, all pass | Worst case before rejection |
|---|---|---|---|
| Linear | 10 pages + 1 cover = **11** | 11 | 11 × (1 + 2) = 33 judge calls, plus 22 extra image calls |
| Branching | 6 shared + 2 × 4 arm pages + 2 cards + 1 cover = **17** | 17 | 17 × 3 = 51 judge calls, plus 34 extra image calls |

- **Each failed image adds one image call and one judge call per redraw**, at most two redraws.
- **A re-run of an unchanged story adds zero calls.**
- **Price per judge call.** OpenRouter listed `openai/gpt-4.1-mini` at $0.40 per million input tokens and $1.60 per million output tokens (queried 2026-09-28). Assuming about 1–2.5K input tokens per image and prompt, and about 150 output tokens, one call costs roughly $0.0006–0.0012. That puts a linear story at roughly **$0.01** in judge calls when everything passes.
- **Every figure above is unverified.** The token-per-image count, the real failure rate and so the average redraw cost are all unmeasured.

---

## Consequences

### Positive Outcomes

- **Calm pictures** is enforced on real images for the first time
- A text-bearing or frightening image rejects the story, with the image and the reason named on the run
- Cross-family judging now covers both text and images, and both are enforced at config load
- Existing image cache keys are unchanged. The first redraw is `regeneration: 1`, and attempt 0 adds no key field.

### Negative Outcomes

- Every run makes 11–17 more model calls
- Judging is sequential, so a run is slower by the sum of the judge latencies. It runs sequentially because Pydantic AI's async client is tied to one event loop, which is unsafe to share across worker threads.
- The text gate's prompt version moved to 2. Cached text verdicts are re-bought once, since the old nine-rule reports no longer validate.

### Risks and Mitigation

| Risk | Mitigation |
|------|------------|
| The judge falsely passes a frightening image | The operator or parent review stays in place wherever it exists. Measure judge accuracy on real renders (see [Validation](#validation)). |
| The judge falsely fails calm images and the story is rejected | The bound caps the waste at two redraws per image. The rejection reason names the criterion, so a systematic misread is visible. Tune the instructions and bump `PROMPT_VERSION`. |
| The judge is swapped to the image model's family | `Settings.image_judge_is_a_different_family_than_the_image_model` refuses the config. |
| The sampling temperature is silently dropped | pydantic-ai 2.5.0 infers a reasoning profile from the `openai/` prefix of `openai/gpt-4.1-mini` and warns that `temperature` is ignored. This affects the text gate (the same model id) as much as this step. It is recorded as a follow-up, not fixed here. |

---

## Implementation Plan

1. **Models.** Drop `calm_pictures` from `SafetyRule`/`SAFETY_RULES`. Add `ImageSafetyCriterion`, `IMAGE_SAFETY_CRITERIA`, `ImageSafetyVerdict` and `ImageSafetyReport`. The report must cover each criterion exactly once, mirroring `SafetyReport`.
2. **Text gate.** Remove the rule from `SAFETY_INSTRUCTIONS` and bump `PROMPT_VERSION` to 2.
3. **Config.** Add `image_safety_model` (default `openai/gpt-4.1-mini`) and a cross-family validator against `image_model`.
4. **Illustrate.** Add a `regenerations` mapping keyed by slot (`page {id}`, `card {page}:{index}`, `cover`). A non-zero count adds `regeneration` to that image's cache inputs.
5. **The step.** Add `src/pipeline/steps/image_safety.py`: `judge_image` (cached on the image's SHA-256, the model, the temperature and the prompt version), `illustrate_safely` (the bounded loop) and `ImageSafetyRejectedError`.
6. **Wiring.** `generate_story` calls `illustrate_safely` and takes an injectable `image_safety_model` seam.

**Rollback plan**: point `generate_story` back at `illustrate_story`. The `regenerations` argument defaults to none, and the new setting is inert without the step. Do not restore `calm_pictures` to the text gate, because it would be vacuous again.

---

## Validation

- `tests/pipeline/test_image_safety.py` covers the following:
  - The text gate no longer carries the rule
  - A report needs all three criteria
  - The cross-family config is refused
  - A passing story is judged once per shown image, with no redraws
  - A rejected page is redrawn once and then passes
  - A page that keeps failing is generated 1 + 2 times, and then the story is rejected with a named reason
  - A re-run makes zero image calls and zero judge calls
  - On the wire, the judge sends the PNG as a `data:image/png;base64,` URL through the OpenAI-compatible client
- `tests/pipeline/test_generate.py` covers the run level: a judge that always fails makes `generate_story` raise, and nothing is staged.
- **Unverified, still to do**: run the judge over a sample of real renders, including deliberately text-bearing and dark ones, and record its pass/fail agreement with a human reviewer. Confirm the per-call token count and cost.

---

## Related Decisions

- [ADR-001](ADR-001-technology-stack.md): OpenRouter as the only model gateway, Pydantic AI, cross-family safety judging
- [ADR-007](ADR-007-langsmith-observability.md): judge calls go through `build_model`, so they are traced with the other Pydantic AI calls when tracing is on
- [ADR-008](ADR-008-narration-gemini-defaults-mistral-cloning.md): the single-OpenRouter-key posture this step keeps, with no new vendor key

---

## References

### Code

- `src/pipeline/steps/image_safety.py`: the judge, the bounded redraw loop and the rejection error
- `src/pipeline/steps/illustrate.py`: the `regenerations` slots and redraw cache keys
- `src/pipeline/steps/safety.py`: the eight-rule text gate (prompt v2)
- `src/pipeline/models.py`: `ImageSafetyReport`, `IMAGE_SAFETY_CRITERIA`
- `src/config.py`: `image_safety_model` and the cross-family validator
- `src/pipeline/generate.py`: the `illustrate_safely` wiring
- `docs/audits/release-readiness.md`: B4

### External

- [OpenRouter image generation guide](https://openrouter.ai/docs/guides/overview/multimodal/image-generation): documented request parameters, none of them a Gemini safety setting
- `GET https://openrouter.ai/api/v1/images/models/google/gemini-3.1-flash-lite-image/endpoints`: passthrough parameters are `cachedContent` only (checked 2026-09-28)
- `GET https://openrouter.ai/api/v1/models/openai/gpt-4.1-mini/endpoints`: image input modality and pricing (checked 2026-09-28)

---

## Metadata

- **ADR Number**: 011
- **Created**: 2026-09-28
- **Tags**: safety, pipeline, images, openrouter, release-blocker
