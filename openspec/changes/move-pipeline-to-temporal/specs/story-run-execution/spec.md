# Spec Delta

## Purpose

Defines how a requested story run executes: which engine runs it, how it survives restarts and deploys without paying twice, how it retries and reports progress and failure, and that it always stops at the parent review gate.

## ADDED Requirements

### Requirement: The engine is chosen per run by a flag

Each new run SHALL record the engine that executes it, taken from the `PIPELINE_ENGINE` setting at submit time (`inprocess` by default, or `temporal`). A run SHALL finish on the engine it started on, and a record without an engine SHALL be treated as in-process.

#### Scenario: Default stays in-process
- **WHEN** a run is submitted with `PIPELINE_ENGINE` unset
- **THEN** the run record's engine is `inprocess` and the run executes in the web process as before

#### Scenario: Flag on starts a workflow
- **WHEN** a run is submitted with `PIPELINE_ENGINE=temporal`
- **THEN** the run record's engine is `temporal`, one workflow is started for it, and the web process runs no pipeline step

#### Scenario: Switching the flag back does not move a live run
- **WHEN** the flag changes from `temporal` to `inprocess` while a Temporal run is live
- **THEN** that run finishes on the worker and new runs execute in-process

#### Scenario: Old records load as in-process
- **WHEN** a run record saved before this change is loaded
- **THEN** its engine is `inprocess`

### Requirement: Each pipeline step is its own durable unit

A Temporal run SHALL execute write, safety, revise (when the gate asks for it), narrate, illustrate with image safety, assemble, narrate prompts (Italian only) and stage as separate steps, in the existing order and with the existing revise and redraw bounds, producing the same staged story as the in-process engine for the same inputs.

#### Scenario: Same inputs, same staged story
- **WHEN** the same request runs once on each engine with the same cached artifacts
- **THEN** both stage byte-identical `story.json` files under the same story id

#### Scenario: Revise runs only when the gate fails
- **WHEN** the safety gate passes on the first draft
- **THEN** no revise step runs and the completed steps do not include revise

### Requirement: A restart or deploy never re-buys a finished step

When the worker stops in the middle of a run (deploy, crash or restart), the run SHALL continue from the step that was in progress, on any worker, without calling a provider again for any artifact a finished step already produced.

#### Scenario: Worker restarted during illustrate
- **WHEN** the worker is stopped while illustrate is running and a new worker starts with an empty local disk
- **THEN** the run completes, and the narration and text provider calls are not repeated

#### Scenario: Artifacts survive the container
- **WHEN** a step produces an artifact
- **THEN** the artifact is stored in the private pending bucket as well as the local disk, and a later lookup with an empty local disk returns it without a provider call

### Requirement: One run executes at most once at a time

Starting a run SHALL be idempotent across web instances: a second start for a run that is already executing or finished SHALL be refused by the engine, and SHALL NOT start a second execution.

#### Scenario: Two web instances start the same run
- **WHEN** two web instances alive during a rolling deploy both try to start the same queued run
- **THEN** exactly one workflow executes and the other start is refused without error to the user

#### Scenario: A failed run can be started again
- **WHEN** a failed run is re-queued
- **THEN** a new execution starts for it and reuses the artifacts its earlier attempt stored

### Requirement: Transient failures retry per step and gate rejections never retry

A step that fails with a retryable error (rate limit, gateway error, connection failure, storage error) SHALL be retried on its own, a bounded number of times. A text safety, image safety or content-rules rejection, a plain provider 500 and a provider read timeout SHALL NOT be retried, and SHALL fail the run.

#### Scenario: Gateway error on narrate
- **WHEN** narrate raises a retryable provider error once and then succeeds
- **THEN** the run completes and only narrate ran twice

#### Scenario: Safety rejection is final
- **WHEN** the safety gate rejects the revised story
- **THEN** the run fails with the rejected criteria, and the judge is called once per verdict, with no retry

#### Scenario: Read timeout is not resent
- **WHEN** an illustrate provider call times out while reading the response
- **THEN** the step is not retried by the engine and the run fails naming illustrate

### Requirement: Progress and failure are read from the run record

The run record SHALL hold the current step, the completed steps and, for a failed run, the step that failed. The workshop and parent progress views SHALL show these without reading the worker's disk or querying the worker.

#### Scenario: Progress during narrate
- **WHEN** a Temporal run is in narrate
- **THEN** the progress view shows write and safety done and narrate current

#### Scenario: Failed step is named
- **WHEN** a run fails in narrate
- **THEN** the failed screen says the run rested at narrate, not at write

#### Scenario: Worker down while queued
- **WHEN** a Temporal run is queued and no worker is running
- **THEN** the progress view shows the run waiting in the queue, and the run starts when a worker comes up

#### Scenario: Workflow ended without updating the record
- **WHEN** a run's workflow has closed as failed, timed out or terminated while its record still says running
- **THEN** the next progress read marks the record failed with the reason

### Requirement: A run stops at the review gate

A run SHALL end when its story is staged in the private pending bucket, with the record `staged` and unreviewed. No part of the run SHALL publish; publishing SHALL happen only through the approve routes, and the family lane SHALL keep its reviewed-digest check.

#### Scenario: Workflow completes at staged
- **WHEN** a Temporal run completes
- **THEN** the record is `staged`, `reviewed` is false, and nothing new exists under `published/` in the public bucket

#### Scenario: The worker cannot publish
- **WHEN** the worker's registered activities are listed
- **THEN** none of them publishes, unpublishes or writes a manifest

#### Scenario: Approve still needs the reviewed bytes
- **WHEN** a family approves a Temporal-staged story it has not reviewed
- **THEN** approve is refused and nothing is published

### Requirement: No child or family data in clear in the engine

Workflow ids SHALL contain only the random run id. Workflow inputs and results SHALL be encrypted before they leave the app, and SHALL carry references to artifacts, never image or audio bytes.

#### Scenario: History holds no premise or token in clear
- **WHEN** a run with a premise completes and its workflow history is fetched
- **THEN** the history contains neither the premise text nor the family token in clear

#### Scenario: Workflow id is the run id
- **WHEN** a Temporal run is started
- **THEN** its workflow id is derived from the run id alone

### Requirement: Previews never run the engine

Preview environments SHALL NOT run a worker or hold Temporal credentials, and SHALL keep the in-process default.

#### Scenario: Preview blueprint
- **WHEN** the Render blueprint's preview values are read
- **THEN** the worker is not part of previews, `PIPELINE_ENGINE` is `inprocess` and the Temporal secrets are blank
