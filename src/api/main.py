"""FastAPI app factory."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler as _default_http_handler
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from langsmith.middleware import TracingMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api.routes._templates import templates
from src.api.routes.landing import router as landing_router
from src.api.routes.parent import router as parent_router
from src.api.routes.player import router as player_router
from src.api.routes.published import router as published_router
from src.api.routes.workshop import router as workshop_router
from src.config import get_settings
from src.observability import init_error_monitoring, init_observability

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app() -> FastAPI:
    settings = get_settings()
    init_observability(settings)
    init_error_monitoring(settings)
    app = FastAPI(title="Cantastorie")
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

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code == 404:
            return templates.TemplateResponse(request, "404.html", status_code=404)
        # For all other HTTP errors, delegate to FastAPI's built-in handler
        # which returns the standard JSON error response.
        return await _default_http_handler(request, exc)

    return app


app = create_app()
