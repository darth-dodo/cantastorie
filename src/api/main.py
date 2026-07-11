"""FastAPI app factory."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.api.routes.player import router as player_router
from src.api.routes.workshop import router as workshop_router

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="Cantastorie")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(player_router)
    app.include_router(workshop_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
