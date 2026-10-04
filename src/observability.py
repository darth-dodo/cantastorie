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
import re
import sys
import time
import traceback
from typing import TYPE_CHECKING, Any, ParamSpec, TextIO, TypeVar, cast

import sentry_sdk
from langsmith import traceable
from langsmith.wrappers import wrap_openai
from openai import DEFAULT_MAX_RETRIES, AsyncOpenAI
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

# The canonical family-token mint (secrets.token_hex(16)) wherever it can reach
# a URL: the overlay path segment and a ``family_token`` query/form field.
_TOKEN_IN_URL = re.compile(r"(family_token=|/families/)([0-9a-f]{32})")

_STANDARD_RECORD_KEYS = frozenset(
    set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "event"}
)

_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}

_current_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "cantastorie_run_id", default=None
)

_step_logger = logging.getLogger("src.pipeline.steps")


def family_hash(family_token: str) -> str:
    """The only form of a family token that may appear in a log line."""
    return hashlib.sha256(_FAMILY_HASH_SALT + family_token.encode()).hexdigest()[:12]


def redact_family_tokens(text: str) -> str:
    """Replace every canonical family token in a URL-ish string by its hash."""
    return _TOKEN_IN_URL.sub(lambda m: m.group(1) + family_hash(m.group(2)), text)


def _quote(value: object) -> str:
    text = str(value)
    if text == "" or any(c in text for c in ' "=\n\r\t'):
        return '"' + "".join(_ESCAPES.get(c, c) for c in text) + '"'
    return text


def _exception_chain(exc: BaseException | None) -> list[BaseException]:
    """Oldest first, as Python prints them; guards against cycles."""
    chain: list[BaseException] = []
    while exc is not None and exc not in chain:
        chain.append(exc)
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return list(reversed(chain))


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

    def formatException(self, ei: Any) -> str:
        """Frames and exception type names only — never ``str(exc)``.

        An exception message can quote model output (a pydantic ValidationError
        echoes its input, a judge's reason rides on a rejection), so stdout gets
        where it broke and what kind of error, not what it said. Sentry still
        receives the full exception through capture_exception.
        """
        blocks: list[str] = []
        for index, exc in enumerate(_exception_chain(ei[1])):
            if index:
                blocks.append(
                    "\nThe above exception was the direct cause of the following exception:\n"
                    if exc.__cause__ is not None
                    else "\nDuring handling of the above exception, another exception occurred:\n"
                )
            frames = traceback.format_tb(exc.__traceback__)
            if frames:
                blocks.append("Traceback (most recent call last):\n" + "".join(frames).rstrip())
            kind = type(exc)
            blocks.append(f"{kind.__module__}.{kind.__qualname__}")
        return "\n".join(blocks)


class RunIdFilter(logging.Filter):
    """Stamp ``run_id`` from the active run context on any record lacking one,
    so every pipeline event — not just step timings — names its run."""

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "run_id", None) is None:
            run_id = _current_run_id.get()
            if run_id is not None:
                record.run_id = run_id
        return True


class AccessLogRedactor(logging.Filter):
    """Rewrite uvicorn access-log args so a family token in a request path
    (``/published/families/{token}/…``, ``?family_token=…``) logs as its hash."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_family_tokens(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        if isinstance(record.msg, str):
            record.msg = redact_family_tokens(record.msg)
        return True


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
    handlers (they do not propagate to root); ``uvicorn.access`` gains one
    filter that hashes any family token in a request path.
    """
    root = logging.getLogger()
    for existing in [h for h in root.handlers if h.get_name() == LOG_HANDLER_NAME]:
        root.removeHandler(existing)
    handler = _StdoutHandler()
    handler.set_name(LOG_HANDLER_NAME)
    handler.setFormatter(KeyValueFormatter())
    handler.addFilter(RunIdFilter())
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.WARNING:
        root.setLevel(logging.WARNING)
    logging.getLogger("src").setLevel(settings.log_level.upper())

    access = logging.getLogger("uvicorn.access")
    for stale in [f for f in access.filters if isinstance(f, AccessLogRedactor)]:
        access.removeFilter(stale)
    access.addFilter(AccessLogRedactor())


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
        **fields,
    }
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
    settings: Settings,
    *,
    http_client: httpx.AsyncClient | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> AsyncOpenAI:
    return wrap_openai(
        AsyncOpenAI(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key.get_secret_value(),
            http_client=http_client,
            max_retries=max_retries,
        )
    )


def typed_traceable(name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        return cast("Callable[P, R]", traceable(name=name)(fn))

    return decorator
