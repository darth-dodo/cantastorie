"""Observability wiring for the FastAPI app and the authoring pipeline.

LangSmith traces (ADR-007); Sentry reports exceptions (ADR-009); stdlib logging
writes one key=value line per event to stdout, which Render collects (B6, AI-485).

Called once at startup (create_app, CLI generate) to sync the Pydantic settings
into the env vars the LangSmith SDK reads. When tracing is off (the default),
the SDK is inert: wrap_openai passes through, @traceable runs the function
unchanged, TracingMiddleware adds no overhead.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import logging
import os
import sys
import time
from typing import TYPE_CHECKING, ParamSpec, TextIO, TypeVar, cast

import sentry_sdk
from langsmith import traceable
from langsmith.wrappers import wrap_openai
from openai import AsyncOpenAI
from sentry_sdk.integrations.logging import LoggingIntegration

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    import httpx

    from src.config import Settings

P = ParamSpec("P")
R = TypeVar("R")

LOG_HANDLER_NAME = "cantastorie-stdout"

# Fixed salt so a family's hash is stable across processes and deploys (a log
# search can follow one family) while never being the token itself.
_FAMILY_HASH_SALT = b"cantastorie-log-family-v1:"

_STANDARD_RECORD_KEYS = frozenset(
    set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "event"}
)

_current_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "cantastorie_run_id", default=None
)

_step_logger = logging.getLogger("src.pipeline.steps")


def family_hash(family_token: str) -> str:
    """The only form of a family token that may appear in a log line."""
    return hashlib.sha256(_FAMILY_HASH_SALT + family_token.encode()).hexdigest()[:12]


def _quote(value: object) -> str:
    text = str(value)
    if text == "" or any(c in text for c in ' "=\n\t'):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    return text


class KeyValueFormatter(logging.Formatter):
    """logfmt-style lines: ``ts=… level=… logger=… event=… key=value…``.

    Fields passed via ``extra=`` become ``key=value`` pairs; a traceback follows
    on its own lines so Render's viewer shows it readably under the event.
    """

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        event = getattr(record, "event", None) or message
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
        parts = [
            f"ts={stamp}.{int(record.msecs):03d}Z",
            f"level={record.levelname}",
            f"logger={record.name}",
            f"event={_quote(event)}",
        ]
        if message != event:
            parts.append(f"msg={_quote(message)}")
        for key, value in vars(record).items():
            if key not in _STANDARD_RECORD_KEYS and not key.startswith("_"):
                parts.append(f"{key}={_quote(value)}")
        line = " ".join(parts)
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


class _StdoutHandler(logging.StreamHandler[TextIO]):
    """Writes to whatever ``sys.stdout`` is at emit time, not at install time,
    so a swapped stdout (pytest capture, a CLI redirect) never goes stale."""

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)


def configure_logging(settings: Settings) -> None:
    """Install the stdout handler once; calling again replaces it, never stacks.

    The handler sits on the root logger at WARNING so third-party chatter stays
    quiet, while ``src.*`` runs at LOG_LEVEL. uvicorn's own loggers keep their
    handlers (they do not propagate to root), so its access lines are untouched.
    """
    root = logging.getLogger()
    for existing in [h for h in root.handlers if h.get_name() == LOG_HANDLER_NAME]:
        root.removeHandler(existing)
    handler = _StdoutHandler()
    handler.set_name(LOG_HANDLER_NAME)
    handler.setFormatter(KeyValueFormatter())
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.WARNING:
        root.setLevel(logging.WARNING)
    logging.getLogger("src").setLevel(settings.log_level.upper())


@contextlib.contextmanager
def run_context(run_id: str) -> Iterator[None]:
    """Tag pipeline step logs with the run they belong to; ``asyncio.to_thread``
    copies the context, so steps running in the generation thread see it."""
    token = _current_run_id.set(run_id)
    try:
        yield
    finally:
        _current_run_id.reset(token)


@contextlib.contextmanager
def timed_step(step: str, **fields: object) -> Iterator[None]:
    """Log ``step_finished`` with ``duration_ms`` when the step returns."""
    started = time.perf_counter()
    yield
    extra: dict[str, object] = {
        "event": "step_finished",
        "step": step,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    run_id = _current_run_id.get()
    if run_id is not None:
        extra["run_id"] = run_id
    extra.update(fields)
    _step_logger.info("step_finished", extra=extra)


def init_observability(settings: Settings) -> None:
    if settings.langsmith_tracing and settings.langsmith_api_key.get_secret_value():
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key.get_secret_value()
        os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
        os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    else:
        os.environ.pop("LANGSMITH_TRACING", None)
        os.environ.pop("LANGSMITH_API_KEY", None)


def init_error_monitoring(settings: Settings) -> None:
    dsn = settings.sentry_dsn.get_secret_value()
    if not dsn:
        return
    sentry_sdk.init(
        dsn=dsn,
        environment=settings.sentry_environment,
        release=settings.sentry_release or None,
        # Errors only — LangSmith is the tracing layer (ADR-007).
        traces_sample_rate=0.0,
        # Parent requests and pipeline prompts can carry a child's name: no
        # PII headers/IPs, no request bodies, no stack-frame locals (ADR-009).
        send_default_pii=False,
        max_request_body_size="never",
        include_local_variables=False,
        # Log records become breadcrumbs only, never events: every failure path
        # already calls capture_exception, so the logger.exception beside it
        # must not file a second Sentry event (B6, AI-485).
        integrations=[LoggingIntegration(level=logging.INFO, event_level=None)],
    )


def build_traced_openai_client(
    settings: Settings, *, http_client: httpx.AsyncClient | None = None
) -> AsyncOpenAI:
    return wrap_openai(
        AsyncOpenAI(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key.get_secret_value(),
            http_client=http_client,
        )
    )


def typed_traceable(name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        return cast("Callable[P, R]", traceable(name=name)(fn))

    return decorator
