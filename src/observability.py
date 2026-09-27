"""Observability wiring for the FastAPI app and the authoring pipeline.

LangSmith traces (ADR-007); Sentry reports exceptions (ADR-009).

Called once at startup (create_app, CLI generate) to sync the Pydantic settings
into the env vars the LangSmith SDK reads. When tracing is off (the default),
the SDK is inert: wrap_openai passes through, @traceable runs the function
unchanged, TracingMiddleware adds no overhead.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, ParamSpec, TypeVar, cast

import sentry_sdk
from langsmith import traceable
from langsmith.wrappers import wrap_openai
from openai import AsyncOpenAI

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.config import Settings

P = ParamSpec("P")
R = TypeVar("R")


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
    )


def build_traced_openai_client(settings: Settings) -> AsyncOpenAI:
    return wrap_openai(
        AsyncOpenAI(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key.get_secret_value(),
        )
    )


def typed_traceable(name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        return cast("Callable[P, R]", traceable(name=name)(fn))

    return decorator
