"""The child player page — a lean full-screen shell that talks to R2 and IndexedDB."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"

router = APIRouter()
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@router.get("/", response_class=HTMLResponse)
async def player_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")
