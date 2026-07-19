"""FastAPI app factory."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from langsmith.middleware import TracingMiddleware

from src.api.routes.parent import router as parent_router
from src.api.routes.player import router as player_router
from src.api.routes.published import router as published_router
from src.api.routes.workshop import router as workshop_router
from src.config import get_settings
from src.observability import init_observability

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app() -> FastAPI:
    init_observability(get_settings())
    app = FastAPI(title="Cantastorie")
    app.add_middleware(TracingMiddleware)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(parent_router)
    app.include_router(player_router)
    app.include_router(published_router)
    app.include_router(workshop_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
