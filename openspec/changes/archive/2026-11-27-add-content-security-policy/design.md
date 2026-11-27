# Design

## Context

See proposal.md for motivation. What the code does today:

- `src/api/security.py` holds `SECURITY_HEADERS` (a fixed dict) and `SecurityHeadersMiddleware`, a pure ASGI middleware added last in `create_app()` so it wraps every route, the 404 handler and `StaticFiles`. It uses `setdefault`, so a route could pre-set a header.
- Starlette's `ServerErrorMiddleware` always sits outside user middleware. An unhandled exception becomes a 500 there, after `SecurityHeadersMiddleware` has already been unwound, so that response carries none of the headers.
- Page inventory (every template under `src/templates/`):
  - `index.html` (`/play`): `palette.js` (classic, same-origin), `main.js` (module, same-origin), `tokens.css` and `player.css`, and a Google Fonts stylesheet whose font files come from `fonts.gstatic.com` (the only third-party request the child's browser makes today; `tests/e2e/two-tap.spec.js` allows exactly those two origins). Manifests, `story.json`, art and audio are fetched with `fetch()` from `ASSET_BASE` (read from a `<meta name="asset-base">` tag) and decoded with Web Audio, so they are `connect-src` loads; cover and card art are `<img>` and CSS `background-image` from the manifest URLs (`img-src`), which point at `R2_PUBLIC_BASE`. The offline prompt is same-origin. No `<audio>` element, no inline script, no inline style; JS sets styles through the CSSOM (`el.style.x = ...`), which CSP does not restrict.
  - `landing.html` (`/`): `palette.js`, one inline `<script>` (scroll reveal), four `style="background-image:url(/static/img/...)"` attributes and one `style="margin-top:8px"`, same-origin images, Google Fonts.
  - `404.html`: `palette.js`, one inline `<style>` block, Google Fonts.
  - `auth/base.html` (every `/parent` and `/workshop` page, sign-in included): `palette.js`, `vendor/htmx.min.js` (2.0.4), two Clerk scripts from `https://{{ fapi_host }}/npm/...`, `workshop.js`, `auth.js`, Google Fonts. `parent/stories.html` adds one inline `<script>` (filter toggle, delete arming); `parent/being_made.html` loads `family-adopt.js` (same-origin module). Staged art and audio (`/parent/staged/...`, `/workshop/staged/...`) are same-origin `<img>` and `<audio>`; library covers use the manifest URL, so the asset origin. HTMX attributes in use are `hx-get`, `hx-post`, `hx-trigger` (`every 2s`, `load`), `hx-swap`, `hx-target`, `hx-confirm`; none needs `eval`.
- Settings: `asset_base` (default `/static/content`; production is the R2 public URL), `r2_public_base`, and `fapi_host(settings)` in `src/api/routes/_nav.py`, which already derives the Clerk Frontend API host for the script tags.
- Fonts: `tokens.css` sets `--font-app: "Baloo 2"` and `--font-story: "Literata"`; the Google Fonts URL asks for Baloo 2 at weights 400 to 700 and Literata at optical sizes 7 to 72 and weights 400 to 600. The ten languages (`it es en el de bg ru mr hi ja`) need Latin, Greek, Cyrillic and Devanagari glyphs; Baloo 2 covers Latin and Devanagari, Literata covers Latin, Greek and Cyrillic, and Japanese falls back to a system font today. The Dockerfile copies all of `src/`, so files under `src/static/fonts/` ship without a Dockerfile change.
- Clerk documents its CSP needs: Frontend API host in `script-src` and `connect-src`; `https://challenges.cloudflare.com` and `https://*.protect.clerk.com` in `script-src` and `frame-src`; `https://*.protect.clerk.com:*` in `connect-src`; `https://img.clerk.com` in `img-src`; `worker-src 'self' blob:`; `'unsafe-inline'` in `style-src` (runtime CSS-in-JS).

## Goals / Non-Goals

**Goals:**
- One place builds both policies from `Settings`, tested as plain strings.
- No `'unsafe-inline'` or `'unsafe-eval'` in any `script-src`.
- The 500 path carries the same headers as every other response.

**Non-Goals:**
- A CSP violation report endpoint (`report-uri` / `report-to`). Report-only mode shows violations in the browser console, which is enough for a manual check.
- Changing how any script renders: Japanese keeps its system fallback, as today.
- Removing `'unsafe-inline'` from the admin `style-src`: Clerk's UI needs it.
- `Strict-Transport-Security` or other new headers.

## Decisions

### Two policies chosen by path prefix
`/parent` and `/workshop` (and their subpaths, the same prefixes `CrossOriginWriteGuard` already uses) get the admin policy; everything else gets the public policy. Alternative: per-route headers set in each handler. Rejected: easy to miss a route, and `StaticFiles`, the 404 handler and the 500 handler have no route to hook. A prefix rule also means the child player can never inherit a Clerk host by accident.

Public policy, with `<assets>` the asset origins:
```
default-src 'self'; script-src 'self';
style-src 'self'; font-src 'self';
img-src 'self' <assets>; media-src 'self' <assets>; connect-src 'self' <assets>;
base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'
```
Admin policy adds: `script-src https://<fapi> https://challenges.cloudflare.com https://*.protect.clerk.com`; `connect-src https://<fapi> https://*.protect.clerk.com:*`; `img-src https://img.clerk.com data:`; `frame-src https://challenges.cloudflare.com https://*.protect.clerk.com`; `worker-src 'self' blob:`; `style-src 'unsafe-inline'`. Clerk telemetry hosts are left out: the beacons are optional and a blocked one changes nothing a parent sees.

### Built once at app start from Settings
`create_app()` already reads `get_settings()`. It calls a pure function `build_policies(settings) -> (public, admin)` and passes the two strings to `SecurityHeadersMiddleware`. Asset origins: for each of `asset_base` and `r2_public_base`, if it parses as an absolute `http(s)` URL take `scheme://host[:port]`, de-duplicated; a relative path adds nothing. When Clerk is not configured (`clerk_jwks_url` empty) the admin policy equals the public one, since every admin path is a 404. Alternative: compute per request. Rejected: settings are fixed for the process, and a string lookup keeps the hot path (story asset proxy, static files) free of work. Tests build the app with env vars set and `get_settings.cache_clear()`, the pattern other config tests use.

### Fonts self-hosted as variable woff2 with unicode-range subsets
Download the variable woff2 files Google Fonts serves today (Baloo 2 `wght` 400 to 700; Literata `opsz` 7 to 72 and `wght` 400 to 600, normal style) for the subsets the roster needs: Baloo 2 `latin`, `latin-ext`, `devanagari`; Literata `latin`, `latin-ext`, `greek`, `cyrillic`, `cyrillic-ext`. Save them under `src/static/fonts/<family>/` with that family's `OFL.txt` (from the `google/fonts` repo) beside them, and copy the `@font-face` rules, `unicode-range` values included, into a new `src/static/css/fonts.css` with `src: url(/static/fonts/...)` and `font-display: swap`. Every page links `fonts.css` before `tokens.css` in place of the Google `<link>` and both `preconnect` tags. With `unicode-range`, a browser fetches only the subsets a page's text uses, as it does from Google today, so a Latin-only shelf still downloads only the Latin files. Alternative: one full static file per weight. Rejected: larger downloads and more files for the same glyphs. Alternative: a font package from npm. Rejected: a new dependency for files that never change.

### Inline scripts move to static files, not nonces
`landing.html`'s scroll reveal moves to `src/static/js/landing.js` and `parent/stories.html`'s toggles to `src/static/js/parent-stories.js`, each loaded with `defer`. Alternative: per-request nonces. Rejected: nonces need per-request state threaded through every `TemplateResponse`, defeat HTML caching, and buy nothing for two small scripts. Inline styles move the same way: the 404 `<style>` block into a new `src/static/css/404.css` linked from `404.html`, and the landing `style=` attributes into modifier classes in `landing.css` (one per step illustration, plus one for the ghost button margin). Both pages must look identical before and after; the Playwright check proves no violation and a screenshot in the PR shows the look.

### HTMX indicator styles on admin pages
HTMX 2 injects a small `<style>` element for `.htmx-indicator`. The admin `style-src` already has `'unsafe-inline'` for Clerk, so nothing changes; a `<meta name="htmx-config" content='{"allowEval":false}'>` in `auth/base.html` makes HTMX's own eval paths fail closed rather than raise CSP violations. No current attribute uses eval.

### 500s: a handler for `Exception` that stamps the headers itself
Register `@app.exception_handler(Exception)` in `create_app()`. Starlette hands that handler to `ServerErrorMiddleware`, so its response is the one sent; the handler returns `PlainTextResponse("Internal Server Error", status_code=500)` with the security headers and the path's policy applied directly (one shared `apply_security_headers(headers, path)` used by both the middleware and the handler). Starlette still re-raises after the handler, so Sentry's Starlette integration and the server log still see the exception. Alternative: subclass `FastAPI` to wrap `ServerErrorMiddleware` in `build_middleware_stack`. Rejected: overrides a private-ish method that changes between Starlette releases.

### `CSP_REPORT_ONLY` setting
`csp_report_only: bool = False` in `Settings`. When true, the header name becomes `Content-Security-Policy-Report-Only`; the value is identical. It exists so a production surprise is an env change and restart on Render, not a code revert. The `frame-ancestors` directive is ignored in report-only mode, but `X-Frame-Options: DENY` still covers framing.

## Risks / Trade-offs

- [Clerk's needs change in a new Clerk JS major version] → the admin policy is one function with a unit test listing the hosts; the Clerk scripts are pinned to `@clerk/ui@1` and `clerk-js@6` in `auth/base.html`, so a major bump is a visible template change.
- [Parent and workshop pages cannot be driven in Playwright: CI has no Clerk instance] → pytest asserts the admin header; the Done when adds a manual sign-in on the deployed app with the browser console open; `CSP_REPORT_ONLY` is the fast lever if it fails.
- [A missed inline style or script on a public page] → the Playwright check fails on any `securitypolicyviolation` event on `/play` (shelf, story playback, offline screen) and `/`.
- [A missed subset shows a fallback font for one language] → the files and `unicode-range` values come straight from the Google CSS the app loads today; a Playwright check asserts the computed family for a Latin and a Devanagari string, and the PR carries a screenshot of the Greek, Russian and Hindi shelves.
- [Font files add weight to the repo and image] → about a dozen woff2 files, each tens of kilobytes; `check-added-large-files` in pre-commit still passes.
- [The 500 handler must not swallow errors] → a test forces a 500 and asserts Sentry capture (mocked) and the log still fire.

## Migration Plan

Ship enforced from the first deploy; `CSP_REPORT_ONLY` is the documented rollback switch only. After deploy, open `/`, `/play` (play one page of a story) and sign in at `/parent` and `/workshop` with the console open. If anything is blocked, set `CSP_REPORT_ONLY=true` on Render (restart only), fix, then unset. Rollback of the code is a plain revert.
