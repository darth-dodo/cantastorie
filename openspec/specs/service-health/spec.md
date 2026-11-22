# service-health Specification

## Purpose
The health endpoints that Render and the post-deploy smoke check rely on: one that says the process is up, one that names the deployed commit, and one that says the service can actually do its job.

## Requirements

### Requirement: Liveness stays cheap and always answers

`GET /health` SHALL answer 200 with `{"status": "ok"}` whenever the process is serving requests, without calling any external service.

#### Scenario: Liveness with R2 unreachable
- **WHEN** R2 is configured but unreachable
- **THEN** `GET /health` answers 200 with `{"status": "ok"}`

### Requirement: Readiness reports every check by name

`GET /health/ready` SHALL answer with a JSON body `{"status": "ready" | "not_ready", "checks": {<name>: "ok" | "failed" | "skipped"}}` that names each check it ran: `settings`, `r2_published` and `r2_pending`.

#### Scenario: All checks pass
- **WHEN** settings load and both R2 buckets answer
- **THEN** the response is 200 with `status` `ready` and every check `ok`

#### Scenario: A bucket is missing
- **WHEN** the pending bucket does not exist or cannot be reached
- **THEN** the response is 503 with `status` `not_ready`, `r2_pending` `failed`, and the other checks still reported

#### Scenario: R2 is not configured
- **WHEN** no R2 endpoint is configured (local development and tests)
- **THEN** the response is 200 with `status` `ready` and both R2 checks `skipped`

### Requirement: Readiness never leaks configuration or errors

The `/health/ready` response SHALL contain only the status and the per-check results. It SHALL NOT contain credentials, bucket names, endpoint URLs or exception text, in either the success or the failure case.

#### Scenario: A failing check stays opaque
- **WHEN** an R2 call fails with an error whose message contains the endpoint URL or a key
- **THEN** the response body contains none of the access key, the secret, the endpoint URL, the bucket names, or the error message

### Requirement: Readiness is bounded in time and cost

`/health/ready` SHALL return within a few seconds even when R2 hangs, and repeated calls within a short window SHALL reuse the last result instead of calling R2 again.

#### Scenario: R2 hangs
- **WHEN** an R2 call does not answer
- **THEN** that check is reported `failed` after the timeout and the response still returns

#### Scenario: Rapid polling
- **WHEN** `/health/ready` is called twice within the cache window
- **THEN** R2 is called only for the first request

### Requirement: Render gates on readiness

The Render service's health check SHALL target `/health/ready`.

#### Scenario: Deploy configuration
- **WHEN** `render.yaml` is read
- **THEN** the web service's `healthCheckPath` is `/health/ready`
