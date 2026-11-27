# Tasks

Test first throughout: each task writes its failing test, then the code that makes it pass.

## 1. Policy builder

- [x] 1.1 Write failing unit tests in `tests/api/test_security_headers.py` for a pure `build_policies(settings)`: public policy has `script-src 'self'` exactly and every baseline directive (`default-src 'self'`, `base-uri 'self'`, `object-src 'none'`, `frame-ancestors 'none'`, `form-action 'self'`); `style-src` and `font-src` exactly `'self'` and no Google host in either policy; no `'unsafe-inline'` or `'unsafe-eval'` in either `script-src`; no `'unsafe-inline'` in the public `style-src`. Verify they fail with `uv run pytest tests/api/test_security_headers.py`
- [x] 1.2 Write failing tests for asset origins: `ASSET_BASE=https://pub-example.r2.dev/published` puts `https://pub-example.r2.dev` in `img-src`, `media-src` and `connect-src` and not in `script-src`; `R2_PUBLIC_BASE` on another origin adds that origin too, de-duplicated; relative `ASSET_BASE` (`/static/content`, `/published`) with no `R2_PUBLIC_BASE` adds nothing beyond `'self'`
- [x] 1.3 Write failing tests for the admin policy: with Clerk configured it adds `https://<fapi_host(settings)>` to `script-src` and `connect-src`, plus `https://challenges.cloudflare.com` and `https://*.protect.clerk.com` (`:*` in `connect-src`) on admin paths only, `https://img.clerk.com`, `data:` in `img-src`, `frame-src`, `worker-src 'self' blob:` and `'unsafe-inline'` in `style-src`; with Clerk unset the admin policy equals the public one
- [x] 1.4 Implement `build_policies` in `src/api/security.py`, reusing `fapi_host` from `src/api/routes/_nav.py`; verify 1.1 to 1.3 pass

## 2. Headers on every response

- [x] 2.1 Write failing tests that build the app through `create_app()` with env set (then `get_settings.cache_clear()`): `/play`, `/`, `/health` and the 404 page carry the public policy; `/parent` (Clerk configured) carries the admin policy; `/parent` with Clerk unset (404) carries the public policy; Clerk configured, `/play`'s policy names no Clerk, `challenges.cloudflare.com` or `protect.clerk.com` host (header twin of the ADR-003 guard)
- [x] 2.2 Add `csp_report_only: bool = False` to `Settings` with failing tests: default sends `Content-Security-Policy` only; `CSP_REPORT_ONLY=true` sends `Content-Security-Policy-Report-Only` with the same value and no `Content-Security-Policy`
- [x] 2.3 Change `SecurityHeadersMiddleware` to take the two policies and the header name, choosing by the `/parent` and `/workshop` prefixes (shared with `CrossOriginWriteGuard`), through one `apply_security_headers(headers, path)` helper; wire it in `create_app()`; verify 2.1 and 2.2 pass and the existing M16 tests stay green
- [x] 2.4 Write a failing test that forces a 500 (a test-only route raising `RuntimeError`, `TestClient(raise_server_exceptions=False)`) and asserts all four headers, the path's policy, the `Internal Server Error` body, and that Sentry capture (mocked) and the error log still fire
- [x] 2.5 Register an `Exception` handler in `create_app()` that returns the plain 500 response with `apply_security_headers` applied; verify 2.4 passes
- [x] 2.6 Add `CSP_REPORT_ONLY` to `.env.example` with a one-line comment; verify `make check` passes

## 3. No inline script or style on public pages

- [x] 3.1 Write a failing pytest that scans `src/templates/` for `<script>` tags without `src` and for `on*=` handler attributes, and scans every template outside `parent/`, `workshop/` and `auth/` for `<style>` blocks and `style=` attributes; verify it fails on `landing.html`, `parent/stories.html` and `404.html`
- [x] 3.2 Move the landing scroll reveal to `src/static/js/landing.js` and the parent stories filter and delete toggles to `src/static/js/parent-stories.js`, loaded with `defer`; verify the filter panel and two-step delete still work (existing parent stories tests plus a Vitest test for `parent-stories.js`)
- [x] 3.3 Move the 404 `<style>` block to `src/static/css/404.css` and the landing `style=` attributes to modifier classes in `landing.css`; verify 3.1 passes and before and after screenshots of `/` and the 404 page match
- [x] 3.4 Add `<meta name="htmx-config" content='{"allowEval":false}'>` to `auth/base.html`; verify the workshop progress poll and parent being-made poll tests still pass

## 4. Self-hosted fonts (closes #164)

- [x] 4.1 Write a failing pytest that scans every template and stylesheet under `src/` for `fonts.googleapis.com` and `fonts.gstatic.com`, and asserts `src/static/fonts/baloo-2/OFL.txt` and `src/static/fonts/literata/OFL.txt` exist; verify it fails today
- [x] 4.2 Download the variable woff2 files for Baloo 2 (`latin`, `latin-ext`, `devanagari`) and Literata (`latin`, `latin-ext`, `greek`, `cyrillic`, `cyrillic-ext`) into `src/static/fonts/<family>/`, with each family's `OFL.txt` from the `google/fonts` repo; verify the files are woff2 (`file` output) and the licence names the family
- [x] 4.3 Write `src/static/css/fonts.css` with the `@font-face` rules (same `unicode-range` values, `font-display: swap`, local `url(/static/fonts/...)`), link it from `index.html`, `landing.html`, `404.html` and `auth/base.html`, and remove every Google Fonts `<link>` and `preconnect`; verify 4.1 passes
- [x] 4.4 Delete `FONT_ORIGINS` from `tests/e2e/two-tap.spec.js` so the off-origin request list must equal `[]`; add a Playwright check that the shelf greeting computes to `Baloo 2` and a reading-mode string to `Literata`, both loaded from `/static/fonts/`; verify both pass, and attach screenshots of the Italian, Greek, Russian and Hindi shelves to the PR

## 5. Browser proof

- [x] 5.1 Add a Playwright spec that records `securitypolicyviolation` events (via `addInitScript`) and console CSP errors and asserts none on `/` (scroll to the steps), on `/play` through shelf, story open, a page turn with audio, and the offline screen, and on the 404 page; verify `E2E_PORT=<free port> npx playwright test` passes with the policy enforced

## 6. Docs

- [x] 6.1 Add a "Response headers" paragraph to `docs/architecture.md` (Privacy Architecture) and the as-built entry in `docs/system-overview.md`: the two policies, the prefix rule, asset origins from settings, self-hosted fonts, `CSP_REPORT_ONLY` as the rollback switch; update the `docs/product.md` Privacy & Data note so it no longer mentions a font exception, if it does; verify the new text has no em or en dashes

## 7. Integration

- [x] 7.1 Run `make check`, `uv run pytest` (coverage floor), `npx vitest run` and `E2E_PORT=<free port> npx playwright test`; all exit 0
- [x] 7.2 Confirm `test_clerk_loads_nowhere_in_the_child_player` still passes and the `/play` header names no third-party source in any directive
- [x] 7.3 Put the post-deploy check (below) in the PR's "How verified" list as an unchecked item for Abhishek, with `CSP_REPORT_ONLY=true` named as the rollback switch; verify it is in the PR body

## Workflow follow-up

- Archive the change with `openspec-archive-change` in the same PR.
- The policy is enforced on the first deploy (`CSP_REPORT_ONLY` unset).
- Post-deploy check: open `/` and `/play` (play one page from R2), then sign in to `/parent` and `/workshop` and check the browser console for CSP violations; none expected. If any, set `CSP_REPORT_ONLY=true` on Render as the rollback switch, then fix.
