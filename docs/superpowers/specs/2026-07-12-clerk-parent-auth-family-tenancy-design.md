# Clerk Parent Authentication + Family Story Tenancy — Design

**Date**: 2026-07-12
**Status**: Approved design, pre-implementation
**Resolves**: AI-393 (ADR-003 decide-and-design) · **Implements**: AI-389 (parent face) plus the minimal child-shelf overlay
**Authority chain**: [ADR-003](../../adr/ADR-003-parent-authentication-clerk.md) (flips to Accepted as step 1) · [ADR-004](../../adr/ADR-004-workshop-area.md) (Accepted — the run machinery this builds on)

---

## Goal

Parents sign in with Clerk (magic link / OAuth) to request story packs, review every page, and approve or reject — from any device. Approved packs publish to a **family-keyed overlay** that the child's shelf merges in anonymously. The child player stays account-free, Clerk-free, and IndexedDB-only.

## Decisions resolved in brainstorming (2026-07-12)

| Question | Decision |
|----------|----------|
| Scope | Auth + parent face + **minimal child overlay** (approve must land somewhere real) |
| Family model | **One parent account = one family.** Family token lives in that Clerk user's metadata. Multi-parent support deferred; export/import remains the escape hatch. |
| Approve target | Publish to `published/families/{family_token}/…` + family overlay manifest — never the shared shelf |
| Child-device linking | **Same device**: parent signs in at `/parent` on the iPad; same origin ⇒ the family token is written directly into the player's IndexedDB `family` store. No QR/codes/links. |
| Cookie guarantee | **Refined** (revises ADR-003's wording — recorded in the acceptance commit): the player page loads no third-party script, sets no cookies, and story-time R2 fetches carry no credentials. A signed-in parent's Clerk session cookie existing on the shared origin is parent data and does not violate the child-privacy pillar ("nothing about the child leaves the browser"). |
| Architecture shape | Same origin for player and parent area (Approach 1). A parent subdomain was rejected: it kills same-device IndexedDB linking and buys purity with ceremony. |

## Out of scope

- Multi-parent families (Clerk Organizations or token-sharing between accounts)
- Regenerate-with-cap in the parent review queue (approve/reject only; follow-up)
- Token rotation **UI** (the procedure is documented in the ADR amendment; nothing in the schema may assume the token is immutable)
- Any change to the operator face (`/workshop` keeps the env-secret, sees all runs)

---

## 1. Decision layer — the acceptance commit (docs only, first)

One commit, no code:

- **ADR-003 → Accepted**, amended with the resolved decisions above, the refined cookie guarantee, the token rotation/leak procedure (¶ below), and the account-deletion policy.
- **`docs/product.md`**: privacy pillar reworded ("no *child* accounts — nothing about the child ever leaves the device; a parent signs in only to request and review stories"); locked tenancy entry amended (family token now anchored to a recoverable parent identity).
- **`docs/architecture.md`**: parent area section, stack table row (Clerk), privacy table row.
- **README** privacy copy: child/parent split.
- **`settled-architecture` skill**: settled row for Clerk (parent area only) + red-flag line ("Clerk script or cookie logic on any child path").

**Token rotation paragraph (goes in ADR-003):** the family token appears in public-bucket URLs; unguessable but leakable (history, screenshots). Rotation procedure: mint a new token in Clerk metadata → republish the overlay under the new prefix (content-addressed cache makes this cheap) → delete the old prefix → parent re-visits `/parent` on the child device once to relink IndexedDB. No button yet; the schema must allow it.

**Account deletion policy:** deleting the Clerk account unlinks the identity and **purges the family prefix** (pending and published) — consistent with ADR-005's GDPR Art. 17 posture. Implemented as a documented manual/script procedure now, webhook automation later.

**Pre-acceptance verifications** (recorded in the ADR when confirmed): EU data residency posture; free-tier limits vs expected family counts; session-token custom-claim syntax; Clerk bot protection enabled on sign-up (sign-up now guards a wallet).

## 2. Identity layer (server)

- **Config** (`src/config.py`, following the `workshop_secret` pattern): `clerk_publishable_key`, `clerk_secret_key` (SecretStr), `clerk_jwks_url`. Unset ⇒ `/parent` routes 404, exactly like the workshop today.
- **`require_parent` dependency** (`src/api/auth.py`): reads the Clerk session JWT (cookie), verifies signature via **PyJWT + cached JWKS** (TTL + stale-if-error; no Clerk server SDK), returns `ParentContext(user_id, family_token, disabled)`.
- **Family token in the JWT, not fetched per request**: Clerk's session token template is customized to include `{{user.public_metadata.family_token}}` and `{{user.public_metadata.disabled}}` as claims. The request path never calls Clerk's REST API. Clerk session JWTs are short-lived (~60 s refresh), so metadata changes (kill switch!) propagate within a minute. *(Template syntax verified during implementation.)*
- **Mint-or-link at first sign-in**: onboarding page posts the browser's existing IndexedDB token (if any — same origin, so a child device's token is adoptable); server links it, else mints fresh; written to Clerk `public_metadata` via **one** REST call. Public metadata is readable by the parent's own browser — acceptable: it is their token and it already lives in the child's IndexedDB.
- **Kill switch / abuse**: `disabled: true` in metadata ⇒ `require_parent` rejects; per-family run cap enforced in `run_manager.submit` — **one active run per family** and a **daily cap (default 3, configurable)**. This closes the "pack-request rate limiting" item architecture.md deferred to Phase 2.

## 3. Parent surface (`/parent`, Jinja2 + HTMX + Tailwind — AI-389)

- **Sign-in page**: loads ClerkJS from a plain `<script>` tag — the *only* pages that ever load it.
- **Pack request form** (theme, language, count 1–3) → `run_manager.submit(family_token, request)` — the existing manager signature, unchanged.
- **My packs + review queue**: run list and staged-story viewer (full text, per-page audio, image strip) filtered strictly by the session's `family_token`. Every R2 read stays under `pending/{family_token}/` — that prefix **is** the tenancy boundary. Reuses the workshop's staged-story template with parent actions (approve / reject).
- **Connect-this-device**: post-sign-in fragment writes `family_token` into the shared-origin IndexedDB `family` store (the same-device linking decision).

## 4. Tenancy layer (publish + child shelf)

- **Parent approve** runs the existing publish step targeting `published/families/{family_token}/stories/{story_id}/…` (immutable hashed asset names, unchanged) and writes/updates the **family overlay manifest** `published/families/{family_token}/{lang}/manifest.json` (same schema as the shared manifest, short TTL).
- **Player merge** (small, vanilla — `main.js`): if IndexedDB holds a family token, `fetchManifest` additionally fetches the overlay and appends its stories to the shelf. Anonymous, bucket-direct, no credentials; no token ⇒ zero extra requests; overlay fetch failure ⇒ shared shelf renders normally (never blocks bedtime).
- **Audit script** learns the families prefix: a family asset may be listed **only** in that family's overlay; zero unapproved assets reachable from any manifest, shared or family.
- **Referrer hygiene**: `<meta name="referrer" content="no-referrer">` on the player page so overlay URLs never leak via Referer.

## 5. Guards

- **CSP on the player route**: `script-src 'self'` (+ `connect-src` for the R2 origin) — the browser itself refuses third-party JS on the child page; the runtime enforcement of the settled decision.
- **Guard test refined** (`two-tap.spec.js`): (a) player module graph loads no Clerk/third-party script, (b) story-time fetches carry no credentials, (c) the player sets no cookies. Replaces the blanket "zero cookies in the browser context" assertion, which a signed-in parent on a shared device legitimately breaks.

## 6. Error handling

| Failure | Behavior |
|---------|----------|
| Clerk outage | `/parent` shows "can't sign in right now"; player and `/workshop` unaffected |
| JWT invalid/expired | 401 → sign-in redirect; JWKS cache serves stale on fetch error |
| Overlay fetch fails (child) | Shared shelf only; silent |
| Re-approve | Idempotent — content-addressed cache republishes identical hashes for free |
| Run cap hit | Friendly message with the active run's status |

## 7. Testing

- **pytest**: `require_parent` at the transport seam — keyless, JWTs minted against a local test JWKS (the pipeline's provider-test pattern); mint-or-link; the **cross-tenant test** (family A cannot list, view, approve, or fetch family B's runs/assets); publish-to-family-prefix; run cap; disabled flag.
- **Playwright**: full parent journey with **Clerk test mode** (`+clerk_test` emails, fixed verification code): sign-in → request → review → approve → story appears on the same device's shelf. Refined privacy guard on the child flow. CSP header present on the player route.
- **Audit**: extended families-prefix rules in the CI audit run.

## 8. Implementation order

1. Acceptance commit (docs only — section 1)
2. Config + `require_parent` + JWKS verification, with pytest seam tests
3. Mint-or-link + session-claim template (Clerk dashboard config recorded in `docs/setup.md`)
4. `/parent` routes: sign-in, request form, my-packs list (run cap enforced)
5. Review queue (staged viewer reuse) + approve → family publish + overlay manifest + audit extension
6. Player overlay merge + connect-this-device + CSP + refined guard test
7. Playwright end-to-end with Clerk test mode

Each step lands independently deployable; the child player changes only at step 6.
