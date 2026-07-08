"""The child player page — a lean full-screen shell that talks to R2 and IndexedDB."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.config import Settings, get_settings

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"

router = APIRouter()
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@router.get("/", response_class=HTMLResponse)
async def player_page(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> HTMLResponse:
    # asset_base tells the shell where published stories live — the static
    # mount in dev, the R2 public bucket in production (AI-365).
    return templates.TemplateResponse(request, "index.html", {"asset_base": settings.asset_base})
