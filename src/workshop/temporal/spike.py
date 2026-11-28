"""The spike: one workflow, one heartbeating activity (ADR-012, task group 1).

It proves the client, codec and converter end to end and gives the billable
Actions count recorded in ADR-012. The real story workflow replaces it.
"""

import time
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from pydantic import BaseModel


class SpikeRequest(BaseModel):
    run_id: str
    note: str
    beats: int
    beat_seconds: float = 0.0
    heartbeat_timeout_seconds: float = 60.0


class SpikeResult(BaseModel):
    run_id: str
    beats: int
    note_length: int


@activity.defn
def spike_step(request: SpikeRequest) -> SpikeResult:
    for beat in range(1, request.beats + 1):
        activity.heartbeat(beat)
        time.sleep(request.beat_seconds)
    return SpikeResult(run_id=request.run_id, beats=request.beats, note_length=len(request.note))


@workflow.defn
class SpikeWorkflow:
    @workflow.run
    async def run(self, request: SpikeRequest) -> SpikeResult:
        return await workflow.execute_activity(
            spike_step,
            request,
            start_to_close_timeout=timedelta(minutes=5),
            heartbeat_timeout=timedelta(seconds=request.heartbeat_timeout_seconds),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=5), backoff_coefficient=2, maximum_attempts=3
            ),
        )
