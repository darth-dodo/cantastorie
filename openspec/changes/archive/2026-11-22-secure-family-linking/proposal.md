# Proposal

## Why

A new account can join a family by presenting the family token at first sign-in, and the operator library prints each family's token. The token should identify a family's private content, not act as a reusable way to join it or appear on screens (release-readiness audit items M6 and M5, Linear AI-497).

## What Changes

- **BREAKING (parent API):** `/parent/api/provision` no longer accepts a family token. First sign-in always starts a new family for the account.
- New: a per-account rate limit on provisioning.
- The operator library shows each private story's family hash instead of its token; library actions look the family up server-side by story id.
- Out of scope: a second grown-up or device joining an existing family (comes with the cross-device work, #179), and token rotation (stays the manual ADR-003 procedure; there are no real families yet).

## Capabilities

### New Capabilities

- `family-linking`: how a parent account gets its family on first sign-in, and the limit on provisioning.
- `operator-library`: what the operator library shows about each story and how its actions identify a family.

### Modified Capabilities

(none)

## Impact

- `src/api/routes/parent.py` (provision), the parent onboarding page and its JS, `src/api/routes/workshop.py` and `src/templates/workshop/library.html`.
- No new dependency or env var.
- `docs/adr/ADR-003-parent-authentication-clerk.md`: the mint-or-link description becomes mint only.
