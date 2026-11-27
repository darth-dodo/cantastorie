# security-headers Specification

## Purpose
The hardening headers every Cantastorie response carries, including the Content-Security-Policy that names where each kind of page may load scripts, styles, fonts, images, audio and network requests from.

## Requirements

### Requirement: Every response carries the hardening headers

Every HTTP response SHALL carry `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-Frame-Options: DENY` and a Content-Security-Policy header (`Content-Security-Policy`, or `Content-Security-Policy-Report-Only` in report-only mode), whatever its status code.

#### Scenario: A page
- **WHEN** a browser requests `/play`, `/`, `/parent` or `/health`
- **THEN** the response carries all four headers

#### Scenario: A not-found page
- **WHEN** a browser requests a path that does not exist
- **THEN** the 404 response carries all four headers

#### Scenario: A server error
- **WHEN** a route raises an unhandled error
- **THEN** the 500 response carries all four headers, with the policy that path would get on success, and its body is still the plain "Internal Server Error" text

### Requirement: Every policy sets the same baseline directives

Every Content-Security-Policy SHALL include `default-src 'self'`, `base-uri 'self'`, `object-src 'none'`, `frame-ancestors 'none'` and `form-action 'self'`, and no `script-src` SHALL contain `'unsafe-inline'` or `'unsafe-eval'`.

#### Scenario: Baseline on public and admin pages
- **WHEN** a browser requests `/play` and, with Clerk configured, `/parent`
- **THEN** both policies contain each baseline directive, and neither `script-src` contains `'unsafe-inline'` or `'unsafe-eval'`

### Requirement: The child player loads no third-party script

On every path outside `/parent` and `/workshop`, including `/play`, `/` and the 404 page, `script-src` SHALL be `'self'` alone, and the policy SHALL name no Clerk host and no Google host.

#### Scenario: Player policy with Clerk configured
- **WHEN** Clerk is configured and a browser requests `/play`
- **THEN** `script-src` is exactly `'self'` and no directive names the Clerk Frontend API host or any other Clerk host

#### Scenario: The player renders under its policy
- **WHEN** the player loads the shelf and plays a story in a real browser with the policy enforced
- **THEN** the browser reports no Content-Security-Policy violation

#### Scenario: The player makes no third-party request
- **WHEN** the player loads the shelf and plays a story with same-origin assets
- **THEN** the browser requests nothing from any origin other than the app's own

### Requirement: Story assets load from the published asset origin

The public and admin policies SHALL allow `img-src`, `media-src` and `connect-src` from `'self'` and from the origin of `ASSET_BASE` and of `R2_PUBLIC_BASE` when either is an absolute URL, and SHALL NOT allow scripts from those origins.

#### Scenario: Bucket-direct playback
- **WHEN** `ASSET_BASE` is `https://pub-example.r2.dev/published`
- **THEN** `img-src`, `media-src` and `connect-src` include `https://pub-example.r2.dev`, and `script-src` does not

#### Scenario: Same-origin assets
- **WHEN** `ASSET_BASE` is a path such as `/static/content` or `/published` and `R2_PUBLIC_BASE` is unset
- **THEN** `img-src`, `media-src` and `connect-src` name no source other than `'self'`

### Requirement: Fonts are served from this origin

Baloo 2 and Literata SHALL be served from the app's own `/static/fonts/` with their licence files, no page or stylesheet SHALL reference `fonts.googleapis.com` or `fonts.gstatic.com`, and `style-src` and `font-src` SHALL be `'self'` on public paths. On public paths `style-src` SHALL NOT contain `'unsafe-inline'`.

#### Scenario: No Google Fonts reference
- **WHEN** any template or stylesheet is scanned
- **THEN** none references `fonts.googleapis.com` or `fonts.gstatic.com`, and no policy names a Google host

#### Scenario: Both families render
- **WHEN** the shelf greeting (Baloo 2) and a Literata string (the 404 heading or the landing tagline) render in a real browser
- **THEN** both families load from `/static/fonts/` and the computed font is the named family, not a fallback

#### Scenario: Landing page styles
- **WHEN** the landing page loads in a real browser with the policy enforced
- **THEN** its fonts, stylesheets and step illustrations render and the browser reports no violation

### Requirement: Parent and workshop pages allow Clerk sign-in

On `/parent` and `/workshop` paths only, the policy SHALL add the Clerk Frontend API host to `script-src` and `connect-src`, Clerk's bot and fraud protection hosts (`https://challenges.cloudflare.com`, `https://*.protect.clerk.com`), and the image, worker and inline-style sources Clerk's sign-in requires, and no other third-party host.

#### Scenario: Parent sign-in page policy
- **WHEN** Clerk is configured and a browser requests `/parent`
- **THEN** `script-src` and `connect-src` include `https://<Frontend API host>`, the same host the page's Clerk script tags load from

#### Scenario: Clerk not configured
- **WHEN** Clerk is not configured and a browser requests `/parent`
- **THEN** the 404 response carries the public policy

### Requirement: Report-only mode is a setting

With `CSP_REPORT_ONLY` true the app SHALL send the same policy in a `Content-Security-Policy-Report-Only` header and no `Content-Security-Policy` header; by default it SHALL send `Content-Security-Policy`.

#### Scenario: Default enforcement
- **WHEN** `CSP_REPORT_ONLY` is unset, as on the first deploy
- **THEN** responses carry `Content-Security-Policy` and no `Content-Security-Policy-Report-Only`

#### Scenario: Report-only
- **WHEN** `CSP_REPORT_ONLY` is true
- **THEN** responses carry `Content-Security-Policy-Report-Only` with the same value and no `Content-Security-Policy`
