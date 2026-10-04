"""Bounded retries for provider HTTP calls (M8, AI-495).

Every provider client (text models, images, narration, the image judge) is
built on one of these transports, so a single transient 429 or 5xx no longer
fails a whole paid run. The policy lives here, once:

- at most ``MAX_ATTEMPTS`` sends per request;
- retried only on 429/502/503/504 and on connect-phase failures — answers that
  mean the provider never served the request, so a retry cannot double-charge;
- never on any other 4xx, a plain 500, or a read/write timeout: the request
  may already be generating (and billing) an image or audio clip;
- waits ``Retry-After`` seconds when the provider sends it, capped at
  ``MAX_RETRY_AFTER_SECONDS``; otherwise exponential backoff with jitter.

Retries are logged as ``provider_retry`` with method, host, path and status —
never the body, prompt or Authorization header.
"""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_STATUSES = frozenset({429, 502, 503, 504})
BACKOFF_BASE_SECONDS = 1.0
MAX_RETRY_AFTER_SECONDS = 30.0

# Failures raised before the request left this process: nothing was served.
_UNSENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None  # an HTTP-date; fall back to our own backoff
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)


def _delay(attempt: int, response: httpx.Response | None) -> float:
    """Seconds to wait before resending; ``attempt`` is the 1-based failed send."""
    if response is not None and (after := _retry_after(response)) is not None:
        return after
    # Jitter spreads parallel page/narration workers apart: [b, 2b], [2b, 4b], ...
    return BACKOFF_BASE_SECONDS * 2.0 ** (attempt - 1) * (1.0 + random.random())


def _log_retry(
    request: httpx.Request,
    attempt: int,
    delay: float,
    response: httpx.Response | None,
    error: Exception | None,
) -> None:
    logger.warning(
        "provider_retry",
        extra={
            "event": "provider_retry",
            "method": request.method,
            "host": request.url.host,
            "path": request.url.path,
            "attempt": attempt,
            "status": response.status_code if response is not None else None,
            "error": type(error).__name__ if error is not None else None,
            "delay_s": round(delay, 2),
        },
    )


class RetryTransport(httpx.BaseTransport):
    """Wraps a sync transport with the provider retry policy."""

    def __init__(
        self,
        inner: httpx.BaseTransport | None = None,
        *,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._inner = inner if inner is not None else httpx.HTTPTransport()
        self._sleep = sleep if sleep is not None else time.sleep

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self._inner.handle_request(request)
            except _UNSENT_ERRORS as error:
                if attempt == MAX_ATTEMPTS:
                    raise
                delay = _delay(attempt, None)
                _log_retry(request, attempt, delay, None, error)
                self._sleep(delay)
                continue
            if response.status_code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                return response
            delay = _delay(attempt, response)
            response.close()
            _log_retry(request, attempt, delay, response, None)
            self._sleep(delay)
        raise AssertionError("unreachable: the last attempt always returns or raises")

    def close(self) -> None:
        self._inner.close()


class AsyncRetryTransport(httpx.AsyncBaseTransport):
    """Wraps an async transport with the provider retry policy."""

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._inner = inner if inner is not None else httpx.AsyncHTTPTransport()
        self._sleep = sleep if sleep is not None else asyncio.sleep

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await self._inner.handle_async_request(request)
            except _UNSENT_ERRORS as error:
                if attempt == MAX_ATTEMPTS:
                    raise
                delay = _delay(attempt, None)
                _log_retry(request, attempt, delay, None, error)
                await self._sleep(delay)
                continue
            if response.status_code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                return response
            delay = _delay(attempt, response)
            await response.aclose()
            _log_retry(request, attempt, delay, response, None)
            await self._sleep(delay)
        raise AssertionError("unreachable: the last attempt always returns or raises")

    async def aclose(self) -> None:
        await self._inner.aclose()


def with_retries(transport: httpx.BaseTransport | None) -> RetryTransport:
    """The retrying transport for a provider client; never wraps twice."""
    if isinstance(transport, RetryTransport):
        return transport
    return RetryTransport(transport)
