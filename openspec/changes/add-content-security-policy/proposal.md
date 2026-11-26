# Proposal

## Why

Every response already carries a small set of security headers and a short Content-Security-Policy (`frame-ancestors`, `base-uri`, `object-src`). This change completes that work: each page gets a full policy that names exactly where its scripts, styles, fonts, images, audio and network requests may come from, and server error responses carry the same headers as every other response. It also self-hosts the two font families (folding in #164, previously Linear AI-384), so the child player makes no third-party request at all and no policy needs to name a font host.

## What Changes

- Every response carries a full Content-Security-Policy with `default-src 'self'` and explicit `script-src`, `style-src`, `font-src`, `img-src`, `media-src`, `connect-src`, `form-action`, `base-uri`, `object-src` and `frame-ancestors` directives.
- Two policies, chosen by path:
  - **Public policy** for the child player at `/play`, the landing page at `/`, the 404 page and every other non-admin response: scripts from this origin only, no third-party script at all (ADR-003). Story images, audio and manifests may come from the published asset origin (the origin of `ASSET_BASE` and `R2_PUBLIC_BASE`), so bucket-direct playback keeps working. Styles and fonts come from this origin only.
  - **Admin policy** for `/parent` and `/workshop`: the public policy plus the Clerk Frontend API host (derived from the same settings that render the Clerk script tags) and the hosts Clerk's sign-in needs (its image host, its bot-protection frame and fraud-protection hosts, and `blob:` workers).
- Both policies are built from settings at app start, so local dev (same-origin `/static/content`), the `/published` proxy and production R2 each get a policy naming only their own asset origin.
- The two page scripts that are written inline (the landing page's scroll reveal and the parent stories page's filter and delete toggles) move into static JS files, so `script-src` carries no `'unsafe-inline'` on any page. The 404 page's inline `<style>` and the landing page's inline `style` attributes move into the existing stylesheets, so the public policy's `style-src` carries no `'unsafe-inline'` either.
- Baloo 2 and Literata are served from `/static/fonts/` as woff2 with local `@font-face` rules and their SIL Open Font License files beside them. Every Google Fonts `<link>` (player, landing, 404, parent and workshop) is removed, and no Google host appears in any policy. The e2e font-origin allowance in `tests/e2e/two-tap.spec.js` is deleted: the child player's off-origin request list must be empty.
- Server error (500) responses carry the same security headers and the same path-based policy as every other response, and keep their current plain "Internal Server Error" body.
- The policy is enforced from the first deploy. A `CSP_REPORT_ONLY` setting (default off) sends the same policy as `Content-Security-Policy-Report-Only` instead; it is the documented rollback switch and needs only an env change.

## Capabilities

### New Capabilities
- `security-headers`: the hardening headers every response carries, the Content-Security-Policy each kind of page gets, and the guarantee that error responses carry them too.

### Modified Capabilities
None.

## Impact

- Code: `src/api/security.py` (policy builder, middleware), `src/api/main.py` (500 handler, middleware wiring), `src/config.py` (`csp_report_only`), `src/templates/landing.html`, `src/templates/parent/stories.html`, `src/templates/404.html`, `src/templates/index.html`, `src/templates/auth/base.html`, two new static JS files, `src/static/css/landing.css`, the 404 styles, a new `src/static/css/fonts.css` and `src/static/fonts/` (woff2 files plus `OFL.txt` per family).
- Tests: `tests/api/test_security_headers.py` grows; a Playwright check that the player and landing page raise no CSP violations; `tests/e2e/two-tap.spec.js` drops its font-origin allowance; a pytest scan that no template or stylesheet references `fonts.googleapis.com` or `fonts.gstatic.com`; the existing ADR-003 guard test (`test_clerk_loads_nowhere_in_the_child_player`) stays green and gains a header-level twin.
- Docs: `docs/architecture.md` (Privacy Architecture section) and `docs/system-overview.md` describe the two policies; `.env.example` gains `CSP_REPORT_ONLY`.
- No new dependency or service; one fewer third-party service on every page. The font files are OFL licensed, which permits self-hosting and redistribution. Clerk's extra hosts belong to the Clerk integration already in use (ADR-003).
- Safety and review gate: no generation, review or publishing code changes. The parent review and approve pages keep working under the admin policy (staged art and audio are same-origin), so the approval gate is untouched and nothing unreviewed can reach the shelf.
