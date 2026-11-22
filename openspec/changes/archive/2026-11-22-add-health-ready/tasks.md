# Tasks

## 1. Health router

- [x] 1.1 Move `/health` and `/health/version` into `src/api/routes/health.py` with a router included by `create_app()`; their existing tests stay green unchanged
- [x] 1.2 Test first: `/health` answers 200 `{"status": "ok"}` while R2 is unreachable

## 2. Readiness

- [x] 2.1 Test first (moto): both buckets present gives 200 `ready` with every check `ok`
- [x] 2.2 Test first: the pending bucket missing gives 503 `not_ready` with `r2_pending` `failed` and the other checks reported
- [x] 2.3 Test first: no R2 endpoint configured gives 200 `ready` with both R2 checks `skipped`
- [x] 2.4 Test first: a failing R2 call whose error text contains the endpoint and a key leaks none of the key, secret, endpoint, bucket names or message
- [x] 2.5 Test first: a hanging R2 call is reported `failed` within the timeout
- [x] 2.6 Test first: two calls inside the cache window make one round of R2 calls
- [x] 2.7 Implement `GET /health/ready` (head_bucket on both buckets, short timeouts, one attempt, run off the event loop, 10 s cache, fixed-vocabulary body, server-side log with the exception class only)

## 3. Render

- [x] 3.1 Test first: `render.yaml` `healthCheckPath` is `/health/ready` (in the `tests/test_deploy_pipeline.py` style)
- [x] 3.2 Change `healthCheckPath` in `render.yaml`
- [x] 3.3 Update `docs/setup.md` (health check section and rollback note) and the FACTORY.md secrets/runtime mention if it names `/health` as Render's check

## 4. Verify

- [ ] 4.1 `make check`, `uv run pytest`, `npx vitest run` green
- [ ] 4.2 After deploy: `curl https://cantastorie.onrender.com/health/ready` answers 200 `ready`, and the post-deploy smoke check stays green

## Workflow follow-up

- Archive the change with `openspec-archive-change` in the build PR
