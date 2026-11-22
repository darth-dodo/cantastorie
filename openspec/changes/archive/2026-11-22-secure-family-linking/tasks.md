# Tasks

## 1. Provisioning

- [x] 1.1 Test first: provisioning for an account with no family starts a new one; an account that already has a family is unchanged
- [x] 1.2 Test first: a body carrying a family token is refused (400) and attaches nothing
- [x] 1.3 Remove `existing_token` from the provision request and the "link" path; update the onboarding page and its JS (no IndexedDB read before provisioning)
- [x] 1.4 Test first: provisioning logs carry the family hash and no token

## 2. Rate limit

- [x] 2.1 Test first: the 6th provisioning attempt within 15 minutes for one account is refused and changes nothing
- [x] 2.2 Implement the in-process sliding-window limiter

## 3. Operator library

- [x] 3.1 Test first: a private story's library row shows the family hash and the page contains no family token
- [x] 3.2 Test first: deleting a private story by id removes it from the right family; a posted token field is ignored; an id found in two families is refused
- [x] 3.3 Update `library.html` and `delete_published_story` (and any other library action that posts a token)
- [x] 3.4 Test first: a private story's cover loads through an operator-only workshop route (its bucket URL carries the token); a parent gets 404

## 4. Docs and verify

- [x] 4.1 Amend ADR-003 (mint only; joining a family comes with #179); in `docs/setup.md`, how to retire a leaked token by hand
- [ ] 4.2 `make check`, `uv run pytest`, `npx vitest run`, Playwright green; parent onboarding E2E covers first sign-in

## Workflow follow-up

- Archive the change with `openspec-archive-change` in the build PR, which is also the first time this change is pushed
