"""The landing page — a static marketing + showcase home at "/".

Server-rendered like the parent and workshop areas, but public and
Clerk-free. It carries no child data and makes no server calls; the child
player self-serves from "/play" (AI-433)."""

from pathlib import Path
from typing import get_args

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.pipeline.models import Language

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"

router = APIRouter()
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@router.get("/", response_class=HTMLResponse)
async def landing_page(request: Request) -> HTMLResponse:
    # The trust pill's count comes from the roster, so it can't drift (it
    # once read 8 after Hindi and Japanese made it 10).
    return templates.TemplateResponse(
        request, "landing.html", {"language_count": len(get_args(Language))}
    )
