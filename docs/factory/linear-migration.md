# Linear to GitHub Issues

On 2026-11-22 open Cantastorie work moved from Linear to GitHub Issues. Linear's history was not copied: Linear stays as the archive for done, canceled and duplicate work.

Each moved issue is a fresh GitHub Issue with the Linear title and description, ending `Previously Linear AI-NNN.` It carries `bug` or `feature` and a `priority:*` label and sits on the "Cantastorie" board. See Part 2 of `docs/superpowers/specs/2026-11-21-software-factory-design.md`.

## Moved

| Linear | GitHub | Title |
|---|---|---|
| AI-369 | #153 | Pipeline: branching topology + choice cards |
| AI-371 | #154 | Author & approve the first branching story (Italian) |
| AI-372 | #155 | Parent gate: hold + keypad addition + persistent lockout |
| AI-373 | #156 | Settings: languages, reading-mode toggle, first-run rule |
| AI-375 | #157 | Spanish content + all ten prompts (it, es) |
| AI-376 | #158 | Pipeline batch mode + staging review workflow |
| AI-377 | #159 | The 19-story launch library |
| AI-379 | #160 | Gloss step: word-to-English maps |
| AI-380 | #161 | Reading mode: text panel, karaoke, gloss bubbles |
| AI-381 | #162 | Export/import + family token, schema pinned |
| AI-382 | #163 | Phase 2 design: packs, review queue, per-family publishing |
| AI-384 | #164 | Self-host fonts: remove the last third-party request from child mode |
| AI-390 | #165 | Audit script: R2 pending/ prefix vs published - zero child-reachable unapproved assets |
| AI-392 | #166 | Word timings: Deepgram STT transcription pass over narrated audio (ADR-004) |
| AI-394 | #167 | Nonna Narrates: family voice narration (ADR-005, Proposed) - decide and design |
| AI-403 | #168 | Design refresh docs: journey screenshots (before/after) + design-system palette section |
| AI-413 | #169 | Player: family overlay merge, connect-this-device, CSP on player route, refined privacy guard |
| AI-414 | #170 | Playwright E2E: full parent journey with Clerk test mode (sign-in → request → approve → shelf) |
| AI-416 | #171 | Add token economics and voice support data to ADR-008 |
| AI-418 | #172 | Rested screen mislabels the failed step as "write" for mid-pipeline failures |
| AI-420 | #173 | require_parent: log a warning when JWKS cache serves stale keys (outage observability) |
| AI-421 | #174 | Update stale E2E: two-tap "parent corner leads nowhere yet" now links /workshop |
| AI-422 | #175 | Test hygiene follow-ups from AI-410 review: shared conftest, empty-string-claim test, signed-client kwargs |
| AI-423 | #176 | Remove or repurpose the structurally-dead ParentContext.disabled field |
| AI-424 | #177 | Privacy notice: disclose Clerk US data residency (DPF + SCCs, no EU pinning) |
| AI-425 | #178 | Buy domain + Clerk production cutover (DNS, production OAuth client, setup.md production section) |
| AI-439 | #190 | Parent run never reaches "staged" → no approve button, cannot publish |
| AI-440 | #179 | Cross-device family overlay: connect-this-device linking flow |
| AI-463 | #180 | Shelf cover layout regressions from #82 (wobble shapes, art width, dead .cover span CSS) |
| AI-478 | #181 | Manifest create path: add IfNoneMatch "*" so a fresh manifest can't clobber a concurrent create |
| AI-484 | #182 | Image safety redraws: steer by the failed criterion (+ optional seed) |
| AI-490 | #183 | M1: /health/ready that checks config and R2; point Render's health check at it |
| AI-491 | #184 | M11: make the single worker and single instance explicit, or make the family cap a conditional write |
| AI-492 | #185 | M13: a recovery path for R2 manifests (object versioning or nightly snapshots) |
| AI-499 | #186 | Choice overlay: wire the 30s nudge and auto-continue that product.md promises (M31) |
| AI-500 | #187 | Player memory and loading on budget tablets: evict audio buffers, throttle prefetch (M19, M21) |
| AI-502 | #188 | Lower-risk audit leftovers: OpenRouter key fail-fast, family lanes in repair tools, Italian prompt re-buy (M3, M12, M17) |
| AI-504 | #189 | Resume overlay "restart" keeps a rebuilt branching path |

## Kept in Linear

Security-sensitive, chosen by Abhishek; not copied to this public repo: AI-399, AI-469, AI-471, AI-472, AI-493, AI-496, AI-497, AI-505, AI-506.
