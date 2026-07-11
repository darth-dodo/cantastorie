"""The operator face at /workshop (AI-388, ADR-004): start, watch, review, publish.

Server-rendered Jinja2 + HTMX, the settled non-child pattern. Access is one
env-var secret: with none configured every route here answers 404 (the
workshop does not exist); with one configured, a correct login sets a session
cookie holding the secret's SHA-256 — never the secret itself. There are no
accounts, matching the privacy architecture.

Runs execute through the RunManager as FastAPI background tasks — in-process,
one at a time, durable in R2 before the first step (src/workshop/manager.py).
Progress is read from the run record plus the working folder's checkpoint
dirs; there is no parallel status store. Publish calls the pipeline's publish
step, which remains the only writer to published/.
"""

from __future__ import annotations

import hashlib
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Protocol, get_args

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.config import Settings, get_settings
from src.pipeline.models import Language, Story, Theme
from src.pipeline.publish import publish_story
from src.workshop.manager import RunManager
from src.workshop.records import PackRequest, RunRecord, RunStore

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
SESSION_COOKIE = "workshop_session"

# Operator-initiated runs are keyed like any pack, under the operator's own
# token; parent-requested packs (AI-389) will carry real family tokens.
OPERATOR_TOKEN = "operator"

# States the fragment keeps polling for; the rest are settled.
LIVE_STATES = frozenset({"queued", "running"})

router = APIRouter(prefix="/workshop")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


class Publisher(Protocol):
    def __call__(self, story_id: str) -> None: ...


@lru_cache
def _default_manager() -> RunManager:
    settings = get_settings()
    return RunManager(RunStore(settings), settings)


def get_run_manager() -> RunManager:
    return _default_manager()


def get_publisher() -> Publisher:
    def publish(story_id: str) -> None:
        publish_story(story_id, get_settings())

    return publish


def _session_token(settings: Settings) -> str:
    return hashlib.sha256(settings.workshop_secret.get_secret_value().encode()).hexdigest()


def _authed(request: Request, settings: Settings) -> bool:
    cookie = request.cookies.get(SESSION_COOKIE, "")
    return bool(cookie) and secrets.compare_digest(cookie, _session_token(settings))


def _require_workshop(settings: Annotated[Settings, Depends(get_settings)]) -> Settings:
    if not settings.workshop_secret.get_secret_value():
        raise HTTPException(status_code=404)  # unconfigured → the workshop does not exist
    return settings


WorkshopSettings = Annotated[Settings, Depends(_require_workshop)]
Manager = Annotated[RunManager, Depends(get_run_manager)]


def _to_login() -> RedirectResponse:
    return RedirectResponse("/workshop", status_code=303)


def _record_or_404(manager: RunManager, run_id: str) -> RunRecord:
    record = manager.store.load(OPERATOR_TOKEN, run_id)
    if record is None:
        raise HTTPException(status_code=404)
    return record


def _checkpointed_steps(record: RunRecord, settings: Settings) -> list[str]:
    """Step names with artifacts on disk — progress derived from the working
    folder's checkpoint dirs, never a parallel store. Best-effort: the story
    folder may not exist yet (queued) or ever (records survive restarts that
    working folders on ephemeral disk do not)."""
    steps: list[str] = []
    if not settings.content_dir.is_dir():
        return steps
    for story_dir in sorted(settings.content_dir.iterdir()):
        if story_dir.is_dir() and story_dir.name.startswith(
            f"{record.request.theme}-{record.request.language}"
        ):
            steps.extend(sorted(step.name for step in story_dir.iterdir() if step.is_dir()))
    return steps


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, settings: WorkshopSettings, manager: Manager) -> HTMLResponse:
    if not _authed(request, settings):
        return templates.TemplateResponse(request, "workshop/login.html", {})
    runs = sorted(manager.store.list_runs(), key=lambda r: r.created_at, reverse=True)
    return templates.TemplateResponse(
        request,
        "workshop/dashboard.html",
        {"runs": runs, "themes": get_args(Theme), "languages": get_args(Language)},
    )


@router.post("/login")
async def login(settings: WorkshopSettings, secret: Annotated[str, Form()]) -> RedirectResponse:
    if not secrets.compare_digest(secret, settings.workshop_secret.get_secret_value()):
        raise HTTPException(status_code=401, detail="wrong secret")
    response = _to_login()
    response.set_cookie(SESSION_COOKIE, _session_token(settings), httponly=True, samesite="strict")
    return response


@router.post("/runs")
async def start_run(
    request: Request,
    settings: WorkshopSettings,
    manager: Manager,
    background: BackgroundTasks,
    theme: Annotated[str, Form()],
    language: Annotated[str, Form()],
    count: Annotated[int, Form()] = 1,
    premise: Annotated[str, Form()] = "",
) -> RedirectResponse:
    if not _authed(request, settings):
        return _to_login()
    pack = PackRequest(theme=theme, language=language, count=count, premise=premise or None)  # type: ignore[arg-type]
    record = await manager.submit(OPERATOR_TOKEN, pack)
    background.add_task(manager.execute, record)
    return RedirectResponse(f"/workshop/runs/{record.id}", status_code=303)


@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_page(
    request: Request, settings: WorkshopSettings, manager: Manager, run_id: str
) -> HTMLResponse:
    if not _authed(request, settings):
        return templates.TemplateResponse(request, "workshop/login.html", {})
    record = _record_or_404(manager, run_id)
    return templates.TemplateResponse(
        request,
        "workshop/run.html",
        {"record": record, "steps": _checkpointed_steps(record, settings), "live": LIVE_STATES},
    )


@router.get("/runs/{run_id}/progress", response_class=HTMLResponse)
async def run_progress(
    request: Request, settings: WorkshopSettings, manager: Manager, run_id: str
) -> HTMLResponse:
    if not _authed(request, settings):
        raise HTTPException(status_code=404)
    record = _record_or_404(manager, run_id)
    return templates.TemplateResponse(
        request,
        "workshop/_progress.html",
        {"record": record, "steps": _checkpointed_steps(record, settings), "live": LIVE_STATES},
    )


@router.post("/runs/{run_id}/approve")
async def approve_run(
    request: Request,
    settings: WorkshopSettings,
    manager: Manager,
    publisher: Annotated[Publisher, Depends(get_publisher)],
    run_id: str,
) -> RedirectResponse:
    if not _authed(request, settings):
        return _to_login()
    record = _record_or_404(manager, run_id)
    for story_id in record.story_ids:
        publisher(story_id)
    manager.store.save(record.advance("approved"))
    return _to_login()


@router.get("/staged/{story_id}", response_class=HTMLResponse)
async def staged_story(request: Request, settings: WorkshopSettings, story_id: str) -> HTMLResponse:
    if not _authed(request, settings):
        return templates.TemplateResponse(request, "workshop/login.html", {})
    story_file = settings.staging_dir / story_id / "story.json"
    if not story_file.is_file():
        raise HTTPException(status_code=404)
    story = Story.model_validate_json(story_file.read_text())
    return templates.TemplateResponse(request, "workshop/story.html", {"story": story})


@router.get("/staged/{story_id}/assets/{name}")
async def staged_asset(
    request: Request, settings: WorkshopSettings, story_id: str, name: str
) -> FileResponse:
    if not _authed(request, settings):
        raise HTTPException(status_code=404)
    story_dir = (settings.staging_dir / story_id).resolve()
    asset = (story_dir / name).resolve()
    # The asset must resolve inside the story's staging folder — no traversal.
    if not asset.is_relative_to(story_dir) or not asset.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(asset)
