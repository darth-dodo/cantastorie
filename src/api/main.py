"""FastAPI app factory.

For child mode the server is a static-file waiter: it hands over the player
page and its assets, then steps aside — story bytes stream bucket-direct
from R2 and all child state lives in IndexedDB. No cookies, no sessions,
no server-side state. The parent area (Jinja2 + HTMX) arrives in Phase 2.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"


def create_app(static_dir: Path = STATIC_DIR) -> FastAPI:
    app = FastAPI(title="Cantastorie", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/", include_in_schema=False)
    async def player() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    # css/, js/, and the local dev content fixtures (stand-in for R2 until
    # the bucket exists; the player fetches by URL either way).
    app.mount("/", StaticFiles(directory=static_dir), name="static")

    return app


app = create_app()
