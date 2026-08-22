"""The operator face at /workshop (AI-388, ADR-005, AI-426): start, watch, review, publish.

Server-rendered Jinja2 + HTMX, the settled non-child pattern. Access is a
Clerk session: with Clerk unconfigured every route here answers 404 (the
workshop does not exist); a signed-in operator (Clerk `public_metadata.role ==
"operator"`) works globally, while any other signed-in user sees a "coming
soon" page. Every request resolves a WorkshopScope from the verified JWT, and
store partitioning threads through `scope.store_token`.

Runs execute through the RunManager as FastAPI background tasks — in-process,
one at a time, durable in R2 before the first step (src/workshop/manager.py).
Progress is read from the run record plus the working folder's checkpoint
dirs; there is no parallel status store. Publish calls the pipeline's publish
step, which remains the only writer to published/.
"""

from __future__ import annotations

import json as _json
import shutil
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Protocol, get_args

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from src.api.auth import verify_clerk_session
from src.api.routes._nav import home_path
from src.config import Settings, get_settings
from src.pipeline.models import Language, Story, Theme
from src.pipeline.publish import (
    STAGED_PREFIX,
    STORY_FILE,
    _build_client,
    _content_type,
    delete_staged_story,
    publish_story,
    unpublish_story,
)
from src.workshop.manager import RunManager
from src.workshop.records import InvalidTransition, PackRequest, RunRecord, RunStore
from src.workshop.scope import WorkshopScope, resolve_scope

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"

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


def _require_workshop(settings: Annotated[Settings, Depends(get_settings)]) -> Settings:
    """404 unless Clerk is configured — the workshop's feature gate."""
    if not settings.clerk_publishable_key.get_secret_value() or not settings.clerk_jwks_url:
        raise HTTPException(status_code=404)
    return settings


WorkshopSettings = Annotated[Settings, Depends(_require_workshop)]
Manager = Annotated[RunManager, Depends(get_run_manager)]


def _base_ctx(settings: Settings, **extra: object) -> dict[str, object]:
    """Every workshop template needs the Clerk publishable key (ClerkJS init)."""
    return {
        "clerk_publishable_key": settings.clerk_publishable_key.get_secret_value(),
        **extra,
    }


async def _scope(request: Request, settings: Settings) -> WorkshopScope | None:
    """Resolve the caller's WorkshopScope, or None if unauthenticated.

    Raises 403 (via verify_clerk_session) for a disabled account.
    """
    claims = await verify_clerk_session(request, settings)
    if claims is None:
        return None
    return resolve_scope(claims)


def _sign_in_page(request: Request, settings: Settings, status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "workshop/login.html", _base_ctx(settings), status_code=status_code
    )


def _to_login() -> RedirectResponse:
    return RedirectResponse("/workshop", status_code=303)


def _record_or_404(manager: RunManager, scope: WorkshopScope, run_id: str) -> RunRecord:
    record = manager.store.load(scope.store_token, run_id)
    if record is None:
        raise HTTPException(status_code=404)
    return record


def _story_record_or_404(manager: RunManager, story_id: str) -> RunRecord:
    for record in manager.store.list_runs():
        if story_id in record.story_ids:
            return record
    raise HTTPException(status_code=404)


def _checkpointed_steps(record: RunRecord, settings: Settings) -> list[str]:
    steps: list[str] = []
    if not settings.content_dir.is_dir():
        return steps
    for story_dir in sorted(settings.content_dir.iterdir()):
        if story_dir.is_dir() and story_dir.name.startswith(
            f"{record.request.theme}-{record.request.language}"
        ):
            steps.extend(sorted(step.name for step in story_dir.iterdir() if step.is_dir()))
    return steps


def _staged_story_summaries(story_ids: list[str], settings: Settings) -> list[dict[str, object]]:
    """Load id, title, page_count for each staged story from the pending bucket."""
    summaries: list[dict[str, object]] = []
    client = _build_client(settings)
    bucket = settings.pending_bucket
    for story_id in story_ids:
        try:
            obj = client.get_object(Bucket=bucket, Key=f"{STAGED_PREFIX}/{story_id}/{STORY_FILE}")
            data = _json.loads(obj["Body"].read())
            summaries.append(
                {
                    "id": story_id,
                    "title": data.get("title", story_id),
                    "page_count": len(data.get("pages", [])),
                }
            )
        except Exception:
            summaries.append({"id": story_id, "title": story_id, "page_count": 0})
    return summaries


def _rel_time(dt: datetime) -> str:
    """Human-readable relative time."""
    now = datetime.now(UTC)
    aware_dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
    diff = now - aware_dt
    secs = int(diff.total_seconds())
    if secs < 90:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins} min ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours} h ago"
    if hours < 48:
        return "yesterday"
    return dt.strftime("%b %-d")


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, settings: WorkshopSettings, manager: Manager) -> Response:
    scope = await _scope(request, settings)
    if scope is None:
        return _sign_in_page(request, settings)
    if not scope.is_operator:
        # No dead-end: a signed-in parent belongs in the parent area.
        return RedirectResponse(home_path(scope.is_operator), status_code=303)
    manager.reap_stale()  # retire zombie runs before the bench renders them (AI-417)
    runs = sorted(manager.store.list_runs(), key=lambda r: r.created_at, reverse=True)
    step_order = ["write", "revise", "safety", "narrate", "illustrate", "assemble"]

    def _fail_step(record: RunRecord) -> str | None:
        if record.state != "failed":
            return None
        done = set(_checkpointed_steps(record, settings))
        for s in step_order:
            if s not in done:
                return s
        return None

    run_extras = {
        r.id: {
            "fail_step": _fail_step(r),
            "rel_time_str": _rel_time(r.created_at),
        }
        for r in runs
    }
    return templates.TemplateResponse(
        request,
        "workshop/dashboard.html",
        _base_ctx(
            settings,
            runs=runs,
            run_extras=run_extras,
            themes=get_args(Theme),
            languages=get_args(Language),
            live=LIVE_STATES,
        ),
    )


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
    scope = await _scope(request, settings)
    if scope is None:
        return _to_login()
    if not scope.is_operator:
        raise HTTPException(status_code=403)
    pack = PackRequest(theme=theme, language=language, count=count, premise=premise or None)  # type: ignore[arg-type]
    record = await manager.submit(scope.store_token, pack)
    background.add_task(manager.execute, record)
    return RedirectResponse(f"/workshop/runs/{record.id}", status_code=303)


@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_page(
    request: Request, settings: WorkshopSettings, manager: Manager, run_id: str
) -> Response:
    scope = await _scope(request, settings)
    if scope is None:
        return _sign_in_page(request, settings)
    if not scope.is_operator:
        return RedirectResponse(home_path(scope.is_operator), status_code=303)
    record = _record_or_404(manager, scope, run_id)
    staged_stories = _staged_story_summaries(record.story_ids, settings)
    return templates.TemplateResponse(
        request,
        "workshop/run.html",
        _base_ctx(
            settings,
            record=record,
            steps=_checkpointed_steps(record, settings),
            live=LIVE_STATES,
            staged_stories=staged_stories,
            rel_time=_rel_time,
        ),
    )


@router.get("/runs/{run_id}/progress", response_class=HTMLResponse)
async def run_progress(
    request: Request, settings: WorkshopSettings, manager: Manager, run_id: str
) -> HTMLResponse:
    scope = await _scope(request, settings)
    if scope is None or not scope.is_operator:
        raise HTTPException(status_code=404)
    manager.reap_stale()  # a stale run's own poll heals it, so it stops polling (AI-417)
    record = _record_or_404(manager, scope, run_id)
    staged_stories = _staged_story_summaries(record.story_ids, settings)
    return templates.TemplateResponse(
        request,
        "workshop/_progress.html",
        _base_ctx(
            settings,
            record=record,
            steps=_checkpointed_steps(record, settings),
            live=LIVE_STATES,
            staged_stories=staged_stories,
        ),
    )


@router.post("/runs/{run_id}/approve")
async def approve_run(
    request: Request,
    settings: WorkshopSettings,
    manager: Manager,
    publisher: Annotated[Publisher, Depends(get_publisher)],
    run_id: str,
) -> RedirectResponse:
    scope = await _scope(request, settings)
    if scope is None:
        return _to_login()
    if not scope.is_operator:
        raise HTTPException(status_code=403)
    record = _record_or_404(manager, scope, run_id)
    if record.state != "staged":
        raise HTTPException(
            status_code=400,
            detail=f"Run is in {record.state} state, must be staged to approve",
        )
    for story_id in record.story_ids:
        publisher(story_id)
    manager.store.save(record.advance("approved"))
    return _to_login()


@router.post("/runs/{run_id}/reject")
async def reject_run(
    request: Request, settings: WorkshopSettings, manager: Manager, run_id: str
) -> RedirectResponse:
    scope = await _scope(request, settings)
    if scope is None:
        return _to_login()
    if not scope.is_operator:
        raise HTTPException(status_code=403)
    record = _record_or_404(manager, scope, run_id)
    try:
        manager.store.save(record.advance("rejected"))
    except InvalidTransition:
        raise HTTPException(status_code=400) from None
    return _to_login()


@router.post("/runs/{run_id}/again")
async def run_again(
    request: Request,
    settings: WorkshopSettings,
    manager: Manager,
    background: BackgroundTasks,
    run_id: str,
) -> RedirectResponse:
    scope = await _scope(request, settings)
    if scope is None:
        return _to_login()
    if not scope.is_operator:
        raise HTTPException(status_code=403)
    record = _record_or_404(manager, scope, run_id)
    new_record = await manager.submit(scope.store_token, record.request)
    background.add_task(manager.execute, new_record)
    return RedirectResponse(f"/workshop/runs/{new_record.id}", status_code=303)


@router.post("/runs/{run_id}/delete")
async def delete_run(
    request: Request, settings: WorkshopSettings, manager: Manager, run_id: str
) -> Response:
    scope = await _scope(request, settings)
    if scope is None:
        return _to_login()
    if not scope.is_operator:
        raise HTTPException(status_code=403)
    record = _record_or_404(manager, scope, run_id)
    if record.state in LIVE_STATES:
        raise HTTPException(status_code=400)
    runs = manager.store.list_runs()
    for story_id in record.story_ids:
        other_records = [
            other for other in runs if other.id != record.id and story_id in other.story_ids
        ]
        if not other_records:
            delete_staged_story(story_id, settings)
            shutil.rmtree(settings.content_dir / story_id, ignore_errors=True)
        if record.state == "approved" and not any(
            other.state == "approved" for other in other_records
        ):
            unpublish_story(story_id, settings)
    manager.store.delete(scope.store_token, run_id)
    if request.headers.get("HX-Request"):
        return HTMLResponse("")
    return _to_login()


@router.post("/staged/{story_id}/delete")
async def delete_staged_story_route(
    request: Request, settings: WorkshopSettings, manager: Manager, story_id: str
) -> Response:
    scope = await _scope(request, settings)
    if scope is None:
        return _to_login()
    if not scope.is_operator:
        raise HTTPException(status_code=403)
    record = _story_record_or_404(manager, story_id)
    if record.state in LIVE_STATES:
        raise HTTPException(status_code=400)
    if record.state not in {"staged", "failed", "rejected", "approved"}:
        raise HTTPException(status_code=400)
    if record.state == "approved":
        unpublish_story(story_id, settings)
    delete_staged_story(story_id, settings)
    shutil.rmtree(settings.content_dir / story_id, ignore_errors=True)
    updated = record.model_copy(
        update={"story_ids": [s for s in record.story_ids if s != story_id]}
    )
    manager.store.save(updated)
    if request.headers.get("HX-Request"):
        return HTMLResponse("")
    return _to_login()


@router.get("/staged/{story_id}", response_class=HTMLResponse)
async def staged_story(
    request: Request, settings: WorkshopSettings, manager: Manager, story_id: str
) -> Response:
    scope = await _scope(request, settings)
    if scope is None:
        return _sign_in_page(request, settings)
    if not scope.is_operator:
        return RedirectResponse(home_path(scope.is_operator), status_code=303)
    client = _build_client(settings)
    bucket = settings.pending_bucket
    try:
        obj = client.get_object(Bucket=bucket, Key=f"{STAGED_PREFIX}/{story_id}/{STORY_FILE}")
    except Exception:
        raise HTTPException(status_code=404) from None
    story = Story.model_validate_json(obj["Body"].read())
    # Load run: prefer ?run= param, fall back to finding by story_id
    record = None
    run_id = request.query_params.get("run")
    if run_id:
        record = manager.store.load(scope.store_token, run_id)
    if record is None:
        for candidate in manager.store.list_runs():
            if story_id in candidate.story_ids:
                record = candidate
                break
    return templates.TemplateResponse(
        request,
        "workshop/story.html",
        _base_ctx(
            settings,
            story=story,
            record=record,
            live=LIVE_STATES,
            rel_time=_rel_time,
        ),
    )


@router.get("/staged/{story_id}/assets/{name}")
async def staged_asset(
    request: Request, settings: WorkshopSettings, story_id: str, name: str
) -> Response:
    scope = await _scope(request, settings)
    if scope is None or not scope.is_operator:
        raise HTTPException(status_code=404)
    if "/" in name or ".." in name:
        raise HTTPException(status_code=404)
    client = _build_client(settings)
    bucket = settings.pending_bucket
    try:
        obj = client.get_object(Bucket=bucket, Key=f"{STAGED_PREFIX}/{story_id}/{name}")
    except Exception:
        raise HTTPException(status_code=404) from None
    return Response(
        content=obj["Body"].read(),
        media_type=_content_type(name),
        headers={"Cache-Control": "private, no-cache"},
    )
