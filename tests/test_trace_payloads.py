"""Traces stay minimal: no audio, image or base64 bytes reach LangSmith.

With tracing on, every narration WAV, every generated PNG and every base64
copy sent to the image judge used to ride along as a run input or output. The
SDK serialized them into its upload queue (about 128 MB at the peak of one
run), which pushed the 512 MB web service into out-of-memory kills. The trace
client now scrubs every run before it is serialized: step names, timings,
models, token usage, prompts and story text stay; binary payloads become a
short placeholder that records their type and size.
"""

from __future__ import annotations

import asyncio
import base64
import struct
import tracemalloc
from typing import Any

import httpx
import langsmith
import pytest
from pydantic import BaseModel, SecretStr

from src import observability
from src.config import Settings
from src.observability import (
    build_trace_client,
    build_traced_openai_client,
    init_observability,
    scrub_trace_payload,
)
from src.pipeline.providers import NarrationClient
from src.pipeline.steps.illustrate import ImageClient

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 400  # ~100 KB, PNG magic
WAV = b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt " + b"\x00" * 200_000


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-test"),
        langsmith_tracing=True,
        langsmith_api_key=SecretStr("lsv2-test"),
        langsmith_endpoint="https://langsmith.test",
    )


@pytest.fixture
def langsmith_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """init_observability writes these straight into os.environ; registering
    them with monkeypatch first restores them when the test ends."""
    for name in (
        "LANGSMITH_TRACING",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
        "LANGSMITH_ENDPOINT",
    ):
        monkeypatch.setenv(name, "")


# ── The scrubber ────────────────────────────────────────────────────────────


def test_bytes_become_a_placeholder_naming_type_and_size() -> None:
    scrubbed = scrub_trace_payload({"audio": WAV, "image": PNG})

    assert scrubbed["audio"].startswith("<audio/wav ")
    assert scrubbed["image"].startswith("<image/png ")
    assert "sha256:" in scrubbed["image"]
    assert isinstance(scrubbed["audio"], str)


def test_base64_data_urls_nested_in_chat_messages_are_dropped() -> None:
    data_url = "data:image/png;base64," + base64.b64encode(PNG).decode()
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Is this picture calm?"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]

    scrubbed = scrub_trace_payload({"messages": messages})

    content = scrubbed["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "Is this picture calm?"}
    assert content[1]["image_url"]["url"].startswith("<data:image/png;base64 ")
    assert "iVBORw0KGgo" not in str(scrubbed)


def test_models_holding_bytes_are_scrubbed_field_by_field() -> None:
    class Result(BaseModel):
        audio: bytes
        language: str

    scrubbed = scrub_trace_payload({"output": Result(audio=WAV, language="it")})

    assert scrubbed["output"]["language"] == "it"
    assert scrubbed["output"]["audio"].startswith("<audio/wav ")


def test_text_numbers_and_structure_are_left_as_they_are() -> None:
    payload = {
        "prompt": "A little boat counts the stars.",
        "pages": [{"id": "p1", "text": "Once upon a time."}],
        "tokens": 1234,
        "ok": True,
        "nothing": None,
    }

    assert scrub_trace_payload(payload) == payload


# ── End to end: what the trace client actually sends ────────────────────────


@pytest.fixture
def sent_bodies(monkeypatch: pytest.MonkeyPatch) -> tuple[list[bytes], Any]:
    """A scrubbing trace client whose HTTP posts are captured, not sent."""
    client = build_trace_client(_settings(), auto_batch_tracing=False)
    bodies: list[bytes] = []

    def capture(method: str, url: str, *args: Any, **kwargs: Any) -> Any:
        for key in ("data", "json", "files"):
            if key in kwargs and kwargs[key] is not None:
                value = kwargs[key]
                bodies.append(value if isinstance(value, bytes) else str(value).encode())
        response = httpx.Response(200, json={})
        response.raise_for_status = lambda: None  # type: ignore[method-assign]
        return response

    monkeypatch.setattr(client.session, "request", capture)
    return bodies, client


def _assert_no_binary(bodies: list[bytes]) -> None:
    assert bodies, "the trace client sent nothing — the test is not exercising it"
    joined = b"".join(bodies)
    for marker in (b"\x89PNG", b"RIFF", b"iVBORw0KGgo", b"UklGR"):
        assert marker not in joined, f"{marker!r} reached the trace upload"


def test_traced_narration_uploads_no_audio(sent_bodies: tuple[list[bytes], Any]) -> None:
    bodies, client = sent_bodies

    def tts(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"\x00\x01" * 24_000,  # one second of PCM
            headers={"Content-Type": "audio/pcm;rate=24000;channels=1"},
        )

    narrator = NarrationClient(_settings(), transport=httpx.MockTransport(tts))
    with langsmith.tracing_context(enabled=True, client=client):
        result = narrator.synthesize("C'era una volta una barchetta.", "it")
    client.flush()

    assert result.audio.startswith(b"RIFF")  # the caller still gets the real WAV
    _assert_no_binary(bodies)
    assert b"barchetta" in b"".join(bodies)  # the text is still traced


def test_traced_image_generation_uploads_no_image(
    sent_bodies: tuple[list[bytes], Any],
) -> None:
    bodies, client = sent_bodies
    data_url = "data:image/png;base64," + base64.b64encode(PNG).decode()

    def images(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "images": [{"image_url": {"url": data_url}}]}}
                ]
            },
        )

    generator = ImageClient(_settings(), transport=httpx.MockTransport(images))
    with langsmith.tracing_context(enabled=True, client=client):
        png = generator.generate("A calm sea at dusk.", reference_png=PNG)
    client.flush()

    assert png == PNG  # the caller still gets the real image
    _assert_no_binary(bodies)
    assert b"A calm sea at dusk." in b"".join(bodies)


# ── Wiring ──────────────────────────────────────────────────────────────────


def test_enabling_tracing_installs_the_scrubbing_client(
    monkeypatch: pytest.MonkeyPatch, langsmith_env: None
) -> None:
    configured: list[Any] = []
    monkeypatch.setattr(observability.langsmith, "configure", lambda **kw: configured.append(kw))

    init_observability(_settings())

    [call] = configured
    trace_client = call["client"]
    assert trace_client._hide_inputs is scrub_trace_payload
    assert trace_client._hide_outputs is scrub_trace_payload


def test_tracing_off_installs_no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    configured: list[Any] = []
    monkeypatch.setattr(observability.langsmith, "configure", lambda **kw: configured.append(kw))

    init_observability(Settings(_env_file=None, langsmith_tracing=False))

    assert configured == []


def test_scrubbing_does_not_hold_extra_copies_of_large_bytes() -> None:
    """The placeholder is built from a hash, not a copy: scrubbing a 1 MB WAV
    must not allocate anything near its size."""
    big = WAV * 5
    tracemalloc.start()
    scrub_trace_payload({"audio": big})
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert peak < len(big) // 10


def test_the_installed_client_scrubs_the_traced_openai_judge(
    monkeypatch: pytest.MonkeyPatch, langsmith_env: None
) -> None:
    """Production installs the client with langsmith.configure at startup, and
    the image judge is traced through wrap_openai, not @traceable. A judge call
    carrying a base64 picture must still upload none of it."""
    installed: list[Any] = []
    real_build = observability.build_trace_client

    def build(settings: Settings, **kwargs: Any) -> Any:
        client = real_build(settings, auto_batch_tracing=False, **kwargs)
        installed.append(client)
        return client

    monkeypatch.setattr(observability, "build_trace_client", build)
    init_observability(_settings())
    bodies: list[bytes] = []

    def capture(method: str, url: str, *args: Any, **kwargs: Any) -> Any:
        for key in ("data", "json", "files"):
            if kwargs.get(key) is not None:
                value = kwargs[key]
                bodies.append(value if isinstance(value, bytes) else str(value).encode())
        response = httpx.Response(200, json={})
        response.raise_for_status = lambda: None  # type: ignore[method-assign]
        return response

    [trace_client] = installed
    monkeypatch.setattr(trace_client.session, "request", capture)

    def completion(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "c1",
                "object": "chat.completion",
                "created": 0,
                "model": "openai/gpt-4.1-mini",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "calm"},
                    }
                ],
            },
        )

    openai = build_traced_openai_client(
        _settings(),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(completion)),
    )
    data_url = "data:image/png;base64," + base64.b64encode(PNG).decode()
    try:
        # Enabled explicitly: the SDK caches its env check, so an earlier test
        # that ran with tracing off would leave it off here. No client is
        # passed, so the one init_observability installed is what's tested.
        with langsmith.tracing_context(enabled=True):
            asyncio.run(
                openai.chat.completions.create(
                    model="openai/gpt-4.1-mini",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Is this picture calm?"},
                                {"type": "image_url", "image_url": {"url": data_url}},
                            ],
                        }
                    ],
                )
            )
        trace_client.flush()
    finally:
        langsmith.configure(client=None)

    _assert_no_binary(bodies)
    assert b"Is this picture calm?" in b"".join(bodies)
