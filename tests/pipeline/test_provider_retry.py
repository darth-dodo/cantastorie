"""Behavior specs for provider retries (M8, AI-495).

One transient 429 or 5xx from OpenRouter used to fail a whole paid run. Every
provider HTTP client now goes through a retrying transport: a bounded number of
attempts, exponential backoff with jitter, Retry-After honoured up to a cap,
and never a retry on any other 4xx or on a request that may have been served.
Sleeps are injected so these specs run instantly.
"""

import asyncio
import json
import logging

import httpx
import pytest
from pydantic import SecretStr

from src.config import Settings
from src.pipeline.providers import NarrationClient, build_model
from src.pipeline.retry import (
    MAX_ATTEMPTS,
    MAX_RETRY_AFTER_SECONDS,
    AsyncRetryTransport,
    RetryTransport,
)
from src.pipeline.steps.illustrate import ImageClient


def _settings() -> Settings:
    return Settings(_env_file=None, openrouter_api_key=SecretStr("sk-or-test"))


def _scripted(*responses: httpx.Response | Exception) -> tuple[httpx.MockTransport, list[int]]:
    """A MockTransport answering with each scripted outcome in turn; counts calls."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        outcome = responses[min(len(calls), len(responses)) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return httpx.MockTransport(handler), calls


def _client(transport: httpx.BaseTransport) -> httpx.Client:
    return httpx.Client(base_url="https://openrouter.test", transport=transport)


def test_a_429_then_200_succeeds_after_one_retry() -> None:
    """Given the provider rate-limits once and then serves the request,
    When a call goes through the retrying transport,
    Then it is retried once, sleeps once, and the 200 comes back.
    """
    inner, calls = _scripted(httpx.Response(429), httpx.Response(200, content=b"ok"))
    sleeps: list[float] = []

    response = _client(RetryTransport(inner, sleep=sleeps.append)).post("/audio/speech")

    assert response.status_code == 200
    assert response.content == b"ok"
    assert len(calls) == 2
    assert len(sleeps) == 1


def test_retry_after_seconds_is_honoured_exactly() -> None:
    """Given a 429 carrying Retry-After: 7,
    When the transport backs off,
    Then it sleeps exactly the seconds the provider asked for.
    """
    inner, _ = _scripted(httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200))
    sleeps: list[float] = []

    _client(RetryTransport(inner, sleep=sleeps.append)).post("/chat/completions")

    assert sleeps == [7.0]


def test_an_absurd_retry_after_is_capped() -> None:
    """Given a 503 asking the caller to come back in an hour,
    When the transport backs off,
    Then it waits no longer than the cap — a paid run never stalls for an hour.
    """
    inner, _ = _scripted(httpx.Response(503, headers={"Retry-After": "3600"}), httpx.Response(200))
    sleeps: list[float] = []

    _client(RetryTransport(inner, sleep=sleeps.append)).post("/chat/completions")

    assert sleeps == [MAX_RETRY_AFTER_SECONDS]


def test_backoff_grows_exponentially_with_jitter_when_no_retry_after() -> None:
    """Given repeated 502s with no Retry-After,
    When the transport backs off,
    Then each wait is jittered and the second is longer than the first's floor.
    """
    inner, _ = _scripted(httpx.Response(502), httpx.Response(504), httpx.Response(200))
    sleeps: list[float] = []

    _client(RetryTransport(inner, sleep=sleeps.append)).post("/chat/completions")

    assert len(sleeps) == 2
    assert 1.0 <= sleeps[0] <= 2.0
    assert 2.0 <= sleeps[1] <= 4.0


def test_repeated_503s_give_up_after_the_bound_and_surface_the_error() -> None:
    """Given the provider stays unavailable,
    When narration synthesizes through the real client,
    Then it stops after the bounded attempts and raises the provider's error.
    """
    inner, calls = _scripted(httpx.Response(503, text="overloaded"))
    sleeps: list[float] = []
    client = NarrationClient(_settings(), transport=RetryTransport(inner, sleep=sleeps.append))

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        client.synthesize("Ciao!", "it")

    assert "503" in str(excinfo.value)
    assert len(calls) == MAX_ATTEMPTS
    assert len(sleeps) == MAX_ATTEMPTS - 1


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 422, 500])
def test_other_errors_are_not_retried(status: int) -> None:
    """Given a 4xx other than 429 (or a plain 500, which may have been served),
    When the call goes through the retrying transport,
    Then it is sent exactly once and the error comes straight back.
    """
    inner, calls = _scripted(httpx.Response(status))
    sleeps: list[float] = []

    response = _client(RetryTransport(inner, sleep=sleeps.append)).post("/chat/completions")

    assert response.status_code == status
    assert len(calls) == 1
    assert sleeps == []


def test_a_connect_error_is_retried_because_nothing_was_sent() -> None:
    """Given the connection could not be opened,
    When the call goes through the retrying transport,
    Then it is retried — the provider never saw the request, so it cannot bill it.
    """
    inner, calls = _scripted(httpx.ConnectError("refused"), httpx.Response(200))
    sleeps: list[float] = []

    response = _client(RetryTransport(inner, sleep=sleeps.append)).post("/chat/completions")

    assert response.status_code == 200
    assert len(calls) == 2


def test_a_read_timeout_is_not_retried_because_it_may_have_been_billed() -> None:
    """Given the request was sent and the response never arrived,
    When the call goes through the retrying transport,
    Then it is not retried — the provider may already be generating (and
    charging for) the image or audio, so a retry could double-charge.
    """
    inner, calls = _scripted(httpx.ReadTimeout("slow"), httpx.Response(200))

    with pytest.raises(httpx.ReadTimeout):
        _client(RetryTransport(inner, sleep=lambda _s: None)).post("/chat/completions")

    assert len(calls) == 1


def test_each_retry_is_logged_without_body_or_key(caplog: pytest.LogCaptureFixture) -> None:
    """Given a retried call carrying a prompt and a bearer key,
    When the transport retries,
    Then one structured provider_retry record is logged, and neither the
    prompt nor the key appears anywhere in it.
    """
    inner, _ = _scripted(httpx.Response(429), httpx.Response(200))
    client = httpx.Client(
        base_url="https://openrouter.test",
        headers={"Authorization": "Bearer sk-or-secret"},
        transport=RetryTransport(inner, sleep=lambda _s: None),
    )

    with caplog.at_level(logging.INFO, logger="src.pipeline.retry"):
        client.post("/chat/completions", json={"prompt": "a dragon named Pip"})

    records = [r for r in caplog.records if getattr(r, "event", None) == "provider_retry"]
    assert len(records) == 1
    assert records[0].__dict__["status"] == 429
    assert records[0].__dict__["attempt"] == 1
    rendered = json.dumps({k: str(v) for k, v in records[0].__dict__.items()})
    assert "sk-or-secret" not in rendered
    assert "Pip" not in rendered


def test_the_async_transport_retries_a_429_too() -> None:
    """Given the async path the LLM steps use,
    When the provider rate-limits once,
    Then the async transport retries and returns the 200.
    """
    inner, calls = _scripted(httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200))
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def call() -> httpx.Response:
        async with httpx.AsyncClient(
            base_url="https://openrouter.test",
            transport=AsyncRetryTransport(inner, sleep=fake_sleep),
        ) as client:
            return await client.post("/chat/completions")

    response = asyncio.run(call())

    assert response.status_code == 200
    assert len(calls) == 2
    assert sleeps == [2.0]


def test_narration_client_wraps_an_injected_transport_in_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given a test-style injected transport that rate-limits once,
    When the real NarrationClient builder is used,
    Then the injected transport is wrapped, so the call survives the 429.
    """
    monkeypatch.setattr("src.pipeline.retry.time.sleep", lambda _s: None)
    inner, calls = _scripted(
        httpx.Response(429),
        httpx.Response(200, content=b"audio", headers={"Content-Type": "audio/mpeg"}),
    )

    result = NarrationClient(_settings(), transport=inner).synthesize("Ciao!", "it")

    assert result.audio == b"audio"
    assert len(calls) == 2


def test_an_already_retrying_transport_is_not_wrapped_twice() -> None:
    """Given a caller passing a transport that already retries,
    When the client is built,
    Then the attempts stay bounded at MAX_ATTEMPTS, not MAX_ATTEMPTS squared.
    """
    inner, calls = _scripted(httpx.Response(503))
    client = NarrationClient(_settings(), transport=RetryTransport(inner, sleep=lambda _s: None))

    with pytest.raises(httpx.HTTPStatusError):
        client.synthesize("Ciao!", "it")

    assert len(calls) == MAX_ATTEMPTS


def test_image_client_wraps_an_injected_transport_in_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given the image endpoint answering 502 once,
    When the real ImageClient builder is used,
    Then the image still comes back after one retry.
    """
    monkeypatch.setattr("src.pipeline.retry.time.sleep", lambda _s: None)
    image = {
        "choices": [{"message": {"images": [{"image_url": {"url": "data:image/png;base64,UE5H"}}]}}]
    }
    inner, calls = _scripted(httpx.Response(502), httpx.Response(200, json=image))

    client = ImageClient(_settings(), transport=inner)
    try:
        assert client.generate("a fox") == b"PNG"
    finally:
        client.close()
    assert len(calls) == 2


def test_default_built_clients_carry_the_retrying_transport() -> None:
    """Given no injected transport (the production path),
    When each provider client is built,
    Then its HTTP transport is the retrying one — and the LLM client's own
    SDK retries are off, so the two layers never multiply.
    """
    narration = NarrationClient(_settings())
    image = ImageClient(_settings())
    model = build_model("openai/gpt-4o-mini", _settings())
    try:
        assert isinstance(narration._client._transport, RetryTransport)
        assert isinstance(image._client._transport, RetryTransport)
        assert isinstance(model.client._client._transport, AsyncRetryTransport)
        assert model.client.max_retries == 0
    finally:
        image.close()


def test_the_traced_llm_client_also_carries_the_retrying_transport() -> None:
    """Given LangSmith tracing on (the traced OpenAI client path),
    When the model is built,
    Then it too goes through the retrying transport with SDK retries off.
    """
    settings = Settings(
        _env_file=None, openrouter_api_key=SecretStr("sk-or-test"), langsmith_tracing=True
    )
    model = build_model("openai/gpt-4o-mini", settings)

    assert isinstance(model.client._client._transport, AsyncRetryTransport)
    assert model.client.max_retries == 0
