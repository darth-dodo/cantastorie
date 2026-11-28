"""Spike specs (ADR-012, tasks 1.4 and 1.5): one workflow, one activity.

A one-activity SpikeWorkflow runs on the Temporal time-skipping test server
through the client the app builds from Settings, with the AES-GCM codec and the
Pydantic data converter on. No Temporal Cloud call is made: the test server is
a local binary, downloaded once into TEMPORAL_TEST_SERVER_DIR (cached in CI).
"""

import asyncio
import base64
import importlib
import os
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import SecretStr
from temporalio.api.enums.v1 import EventType
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.config import Settings
from src.workshop.temporal.client import connect, data_converter
from src.workshop.temporal.codec import AesGcmPayloadCodec
from src.workshop.temporal.sandbox import WORKFLOW_RUNNER
from src.workshop.temporal.spike import SpikeRequest, SpikeResult, SpikeWorkflow, spike_step

pytestmark = pytest.mark.temporal

TASK_QUEUE = "cantastorie-spike"
SAMPLE_PREMISE = "Una volpe di nome Lucia trova una chiave dorata nel bosco"
PAYLOAD_KEY = os.urandom(32)


@pytest.fixture(scope="module")
def loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def env(loop: asyncio.AbstractEventLoop) -> Iterator[WorkflowEnvironment]:
    environment = loop.run_until_complete(
        WorkflowEnvironment.start_time_skipping(
            download_dest_dir=os.environ.get("TEMPORAL_TEST_SERVER_DIR")
        )
    )
    yield environment
    loop.run_until_complete(environment.shutdown())


def _settings(env: WorkflowEnvironment) -> Settings:
    return Settings(
        _env_file=None,
        pipeline_engine="temporal",
        temporal_address=env.client.service_client.config.target_host,
        temporal_namespace=env.client.namespace,
        temporal_api_key=SecretStr("local-test-server-ignores-this"),
        temporal_payload_key=SecretStr(base64.b64encode(PAYLOAD_KEY).decode()),
    )


async def _client(env: WorkflowEnvironment) -> Client:
    return await connect(_settings(env), tls=False)


async def _run(env: WorkflowEnvironment, request: SpikeRequest) -> tuple[SpikeResult, str]:
    client = await _client(env)
    workflow_id = f"spike-{uuid.uuid4().hex}"
    with ThreadPoolExecutor(max_workers=1) as executor:
        async with Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[SpikeWorkflow],
            activities=[spike_step],
            activity_executor=executor,
            workflow_runner=WORKFLOW_RUNNER,
        ):
            result = await client.execute_workflow(
                SpikeWorkflow.run, request, id=workflow_id, task_queue=TASK_QUEUE
            )
    return result, workflow_id


def test_the_app_client_carries_the_codec_and_the_pydantic_converter(
    env: WorkflowEnvironment, loop: asyncio.AbstractEventLoop
) -> None:
    client = loop.run_until_complete(_client(env))
    converter = client.data_converter
    assert isinstance(converter.payload_codec, AesGcmPayloadCodec)
    assert converter.payload_converter_class.__name__ == "PydanticPayloadConverter"
    assert converter.failure_converter_class.__name__ == (
        "DefaultFailureConverterWithEncodedAttributes"
    )
    assert client.namespace == env.client.namespace


def test_a_one_activity_spike_workflow_returns_the_activity_result(
    env: WorkflowEnvironment, loop: asyncio.AbstractEventLoop
) -> None:
    request = SpikeRequest(run_id="run-123", note=SAMPLE_PREMISE, beats=3)
    result, _ = loop.run_until_complete(_run(env, request))
    assert result == SpikeResult(run_id="run-123", beats=3, note_length=len(SAMPLE_PREMISE))


def test_the_spike_history_holds_no_input_text_in_clear(
    env: WorkflowEnvironment, loop: asyncio.AbstractEventLoop
) -> None:
    request = SpikeRequest(run_id="run-456", note=SAMPLE_PREMISE, beats=2)
    _, workflow_id = loop.run_until_complete(_run(env, request))
    history = loop.run_until_complete(env.client.get_workflow_handle(workflow_id).fetch_history())
    raw = history.to_json()
    assert "Lucia" not in raw
    assert "run-456" not in raw
    started = history.events[0].workflow_execution_started_event_attributes
    assert started.input.payloads[0].metadata["encoding"] == b"binary/encrypted-aes-gcm"


def test_a_client_with_another_key_cannot_read_the_spike_result(
    env: WorkflowEnvironment, loop: asyncio.AbstractEventLoop
) -> None:
    request = SpikeRequest(run_id="run-789", note=SAMPLE_PREMISE, beats=1)
    _, workflow_id = loop.run_until_complete(_run(env, request))

    async def read_with_another_key() -> object:
        stranger = Client(
            env.client.service_client,
            namespace=env.client.namespace,
            data_converter=data_converter(os.urandom(32)),
        )
        return await stranger.get_workflow_handle(workflow_id).result()

    with pytest.raises(Exception, match="decrypt"):
        loop.run_until_complete(read_with_another_key())


def test_spike_history_counts_one_workflow_start_and_one_activity_start(
    env: WorkflowEnvironment, loop: asyncio.AbstractEventLoop
) -> None:
    request = SpikeRequest(run_id="run-actions", note="", beats=3)
    _, workflow_id = loop.run_until_complete(_run(env, request))
    history = loop.run_until_complete(env.client.get_workflow_handle(workflow_id).fetch_history())
    kinds = [event.event_type for event in history.events]
    assert kinds.count(EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED) == 1
    assert kinds.count(EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED) == 1
    assert kinds.count(EventType.EVENT_TYPE_TIMER_STARTED) == 0
    assert kinds.count(EventType.EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED) == 0


async def _heartbeats_the_server_saw(env: WorkflowEnvironment, request: SpikeRequest) -> list[int]:
    client = await _client(env)
    seen: list[int] = []
    with ThreadPoolExecutor(max_workers=1) as executor:
        async with Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[SpikeWorkflow],
            activities=[spike_step],
            activity_executor=executor,
            workflow_runner=WORKFLOW_RUNNER,
        ):
            handle = await client.start_workflow(
                SpikeWorkflow.run, request, id=f"spike-{uuid.uuid4().hex}", task_queue=TASK_QUEUE
            )
            while (await handle.describe()).status == WorkflowExecutionStatus.RUNNING:
                pending = (await handle.describe()).raw_description.pending_activities
                if pending and pending[0].heartbeat_details.payloads:
                    details = pending[0].heartbeat_details.payloads
                    beat = (await client.data_converter.decode(details))[0]
                    if not seen or seen[-1] != beat:
                        seen.append(beat)
                await asyncio.sleep(0.05)
            await handle.result()
    return seen


def test_heartbeats_reach_the_server_throttled_not_once_per_call(
    env: WorkflowEnvironment, loop: asyncio.AbstractEventLoop
) -> None:
    """30 heartbeat calls over about 3 s with a 2 s heartbeat timeout.

    The SDK sends at most one heartbeat per 0.8 x heartbeat timeout (1.6 s
    here), and each one that reaches the server is a billable Action. Polling
    describe every 50 ms sees each delivered heartbeat's details.
    """
    request = SpikeRequest(
        run_id="run-beats", note="", beats=30, beat_seconds=0.1, heartbeat_timeout_seconds=2
    )
    seen = loop.run_until_complete(_heartbeats_the_server_saw(env, request))
    assert seen[0] == 1
    assert 1 <= len(seen) <= 3


def test_the_spike_runs_in_a_process_that_imported_the_pipeline(
    env: WorkflowEnvironment, loop: asyncio.AbstractEventLoop
) -> None:
    """The worker will import the pipeline, and with it pydantic-ai's
    py-key-value dependency, which installs a beartype import hook. The
    workflow sandbox must still import the workflow module cleanly.
    """
    importlib.import_module("src.pipeline.generate")
    request = SpikeRequest(run_id="run-pipeline", note="", beats=1)
    result, _ = loop.run_until_complete(_run(env, request))
    assert result.run_id == "run-pipeline"
