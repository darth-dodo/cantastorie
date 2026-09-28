"""FastAPI app factory."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler as _default_http_handler
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from langsmith.middleware import TracingMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api.routes._templates import templates
from src.api.routes.landing import router as landing_router
from src.api.routes.parent import router as parent_router
from src.api.routes.player import router as player_router
from src.api.routes.published import router as published_router
from src.api.routes.workshop import get_run_manager
from src.api.routes.workshop import router as workshop_router
from src.config import get_settings
from src.observability import init_error_monitoring, init_observability
from src.workshop.manager import RunManager

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


async def _reap_and_resume(manager: RunManager) -> None:
    """Boot recovery (B5, AI-477): retire stranded runs, then re-enter any
    still queued/running. `reap_stale` does its R2 sweep via `asyncio.to_thread`
    (it's sync, boto3-backed I/O) so it never blocks the event loop, and it
    runs here — inside the scheduled task — rather than before it, so it
    cannot delay startup either. Reap must finish before resume reads the
    store, so a run it just retired to `failed` is never re-entered.
    """
    await asyncio.to_thread(manager.reap_stale)
    await manager.resume_on_boot()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Same DI seam the routes use, so a test that overrides get_settings /
    # get_run_manager on `app.dependency_overrides` before entering
    # `TestClient(app)` as a context manager gets exercised here too.
    settings = app.dependency_overrides.get(get_settings, get_settings)()
    resume_task: asyncio.Task[None] | None = None
    # Gated on R2 being configured — the same precondition RunStore itself
    # needs (src/api/routes/workshop.py builds it the same way). With no pending
    # bucket there is nothing to scan, and building a manager/store here
    # unconditionally would mean every app built without R2 config (most
    # tests, and any deploy that never configures the workshop) pays for a
    # boto3 client and a doomed scan for nothing.
    if settings.pending_bucket:
        manager = app.dependency_overrides.get(get_run_manager, get_run_manager)()
        # Scheduled, never awaited: resume_on_boot can take minutes, and
        # awaiting it here would hold /health unresponsive until it finished —
        # Render would see a failed health check and roll the deploy back in
        # a loop (docs/audits/release-readiness.md → B5).
        resume_task = asyncio.create_task(_reap_and_resume(manager))
        app.state.resume_task = resume_task  # keep a reference so it can't be GC'd
    yield
    if resume_task is not None:
        resume_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await resume_task


def create_app() -> FastAPI:
    settings = get_settings()
    # Sentry and LangSmith are wired up here, before the FastAPI app (and so
    # before `lifespan` can ever run) is even constructed — a background-task
    # failure during boot resume is always reported to an already-initialized
    # Sentry (src/workshop/manager.py's execute() calls sentry_sdk.capture_exception).
    init_observability(settings)
    init_error_monitoring(settings)
    app = FastAPI(title="Cantastorie", lifespan=lifespan)
    app.add_middleware(TracingMiddleware)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(landing_router)
    app.include_router(parent_router)
    app.include_router(player_router)
    app.include_router(published_router)
    app.include_router(workshop_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # Browsers request /favicon.ico whether or not a page links it (AI-467).
    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "icons" / "favicon.ico",
            media_type="image/x-icon",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code == 404:
            return templates.TemplateResponse(request, "404.html", status_code=404)
        # For all other HTTP errors, delegate to FastAPI's built-in handler
        # which returns the standard JSON error response.
        return await _default_http_handler(request, exc)

    return app


app = create_app()
