"""Parent-area API routes (AI-410, ADR-003).

Only the provision endpoint lives here for now; the /parent pages (sign-in,
pack request form, my-packs) arrive in the next step of the design.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Annotated, get_args

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Request,
    Response,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from src.api.auth import CandidateContext, ParentContext, require_parent, require_parent_candidate
from src.api.clerk import ClerkAPIError, set_family_token
from src.api.routes._nav import fapi_host, home_path
from src.api.routes.workshop import (  # shared DI seam, overridable in tests
    _checkpointed_steps,
    get_run_manager,
)
from src.config import Settings, get_settings
from src.pipeline.models import Language, Theme
from src.pipeline.publish import list_published_stories, unpublish_story
from src.workshop.manager import RunCapExceeded, RunManager
from src.workshop.records import PackRequest

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

router = APIRouter(prefix="/parent")


def _owned_story_ids(manager: RunManager, family_token: str) -> set[str]:
    return {
        story_id
        for record in manager.store.list_runs(family_token=family_token)
        if record.state == "approved"
        for story_id in record.story_ids
    }


Manager = Annotated[RunManager, Depends(get_run_manager)]

# The form's theme/language choices come straight from the pipeline literals,
# exactly like the workshop dashboard (routes/workshop.py builds these with
# get_args too).
THEMES = get_args(Theme)
LANGUAGES = get_args(Language)

# Canonical family-token format: 32 lowercase hex chars (secrets.token_hex(16)).
# Strict validation is a security boundary — the token becomes an R2 key
# prefix (pending/{family_token}/…), so posted strings must never smuggle
# path separators or casing variants into bucket keys.
FAMILY_TOKEN_PATTERN = r"^[0-9a-f]{32}$"


def mint_family_token() -> str:
    """128 bits of randomness, matching FAMILY_TOKEN_PATTERN."""
    return secrets.token_hex(16)


class ProvisionRequest(BaseModel):
    """Body posted by the onboarding page.

    existing_token is the browser's IndexedDB family token if one exists
    (same origin, so a child device's token is adoptable — the "link" path).
    """

    existing_token: str | None = Field(default=None, pattern=FAMILY_TOKEN_PATTERN)


class ProvisionResponse(BaseModel):
    family_token: str
    action: str  # "already" | "linked" | "minted"


async def _page_identity(request: Request, settings: Settings) -> CandidateContext | None:
    """Candidate identity for page routes: 401 → None (render sign-in);
    404 (feature unset) and 403 (disabled) propagate unchanged."""
    try:
        return await require_parent_candidate(request, settings)
    except HTTPException as error:
        if error.status_code == 401:
            return None
        raise


@router.get("", response_class=HTMLResponse)
async def parent_home(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    manager: Manager,
) -> Response:
    ctx = await _page_identity(request, settings)
    if ctx is not None and ctx.is_operator:
        # Superusers author in the workshop — they have no parent view here.
        return RedirectResponse(home_path(True), status_code=303)
    context: dict[str, object] = {
        "door": "parent",
        "fapi_host": fapi_host(settings),
        "publishable_key": settings.clerk_publishable_key.get_secret_value(),
    }
    if ctx is None:
        return templates.TemplateResponse(request, "auth/sign_in.html", context)
    if ctx.family_token is None:
        # First sign-in: page JS POSTs /parent/api/provision then reloads.
        context["onboarding"] = True
        return templates.TemplateResponse(request, "auth/sign_in.html", context)
    # Provisioned parents get the packs page with their own runs, newest first.
    runs = manager.store.list_runs(family_token=ctx.family_token)
    runs.sort(key=lambda r: r.created_at, reverse=True)
    return templates.TemplateResponse(
        request,
        "parent/packs.html",
        {
            **context,
            "runs": runs,
            "cap_message": None,
            "live": ["queued", "running"],
            "themes": THEMES,
            "languages": LANGUAGES,
        },
    )


@router.get("/stories", response_class=HTMLResponse)
async def parent_stories(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    manager: Manager,
) -> Response:
    ctx = await _page_identity(request, settings)
    if ctx is not None and ctx.is_operator:
        return RedirectResponse(home_path(True), status_code=303)
    context: dict[str, object] = {
        "fapi_host": fapi_host(settings),
        "publishable_key": settings.clerk_publishable_key.get_secret_value(),
    }
    if ctx is None:
        return templates.TemplateResponse(request, "auth/sign_in.html", context)
    if ctx.family_token is None:
        context["onboarding"] = True
        return templates.TemplateResponse(request, "auth/sign_in.html", context)
    owned = _owned_story_ids(manager, ctx.family_token)
    stories = [s for s in list_published_stories(settings) if s.id in owned]
    return templates.TemplateResponse(
        request, "parent/stories.html", {**context, "stories": stories}
    )


@router.post("/stories/{story_id}/delete")
async def delete_parent_story(
    request: Request,
    story_id: str,
    ctx: Annotated[ParentContext, Depends(require_parent)],
    settings: Annotated[Settings, Depends(get_settings)],
    manager: Manager,
) -> Response:
    if story_id not in _owned_story_ids(manager, ctx.family_token):
        raise HTTPException(status_code=404)
    unpublish_story(story_id, settings)
    if request.headers.get("HX-Request"):
        return HTMLResponse("")
    return RedirectResponse("/parent/stories", status_code=303)


@router.post("/packs")
async def request_pack(
    request: Request,
    ctx: Annotated[ParentContext, Depends(require_parent)],
    settings: Annotated[Settings, Depends(get_settings)],
    manager: Manager,
    background: BackgroundTasks,
    theme: Annotated[str, Form()],
    language: Annotated[str, Form()],
    count: Annotated[int, Form()] = 1,
    premise: Annotated[str, Form()] = "",
) -> Response:
    pack = PackRequest(theme=theme, language=language, count=count, premise=premise or None)  # type: ignore[arg-type]
    try:
        record = await manager.submit(ctx.family_token, pack)
    except RunCapExceeded as cap:
        context: dict[str, object] = {
            "door": "parent",
            "fapi_host": fapi_host(settings),
            "publishable_key": settings.clerk_publishable_key.get_secret_value(),
            "runs": [],
            "cap_message": str(cap),
            "cap_active": cap.active,
            "themes": THEMES,
            "languages": LANGUAGES,
        }
        return templates.TemplateResponse(request, "parent/packs.html", context)
    background.add_task(manager.execute, record)
    return RedirectResponse("/parent", status_code=303)


@router.get("/packs/{run_id}/progress", response_class=HTMLResponse)
async def pack_progress(
    request: Request,
    run_id: str,
    ctx: Annotated[ParentContext, Depends(require_parent)],
    settings: Annotated[Settings, Depends(get_settings)],
    manager: Manager,
) -> HTMLResponse:
    record = manager.store.load(ctx.family_token, run_id)  # tenancy: load is family-scoped
    if record is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "workshop/_progress.html",
        {
            "record": record,
            "live": ["queued", "running"],
            "steps": _checkpointed_steps(record, settings),
            "staged_stories": [],
            "base_url": "/parent/packs",
            "is_operator": False,
        },
    )


@router.post("/api/provision")
async def provision(
    body: ProvisionRequest,
    ctx: Annotated[CandidateContext, Depends(require_parent_candidate)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ProvisionResponse:
    """Mint-or-link the family token at first sign-in.

    Idempotent: if the session claims already carry a token, return it and
    make no Clerk call — a provisioned account cannot overwrite its token
    (rotation is a documented manual procedure, ADR-003).
    """
    if ctx.family_token is not None:
        return ProvisionResponse(family_token=ctx.family_token, action="already")

    if body.existing_token is not None:
        family_token, action = body.existing_token, "linked"
    else:
        family_token, action = mint_family_token(), "minted"

    try:
        await set_family_token(ctx.user_id, family_token, settings)
    except ClerkAPIError:
        # No partial state: nothing was stored locally, and Clerk either
        # rejected or never received the write. The client may simply retry.
        raise HTTPException(
            status_code=502, detail="could not save the family token; try again"
        ) from None

    return ProvisionResponse(family_token=family_token, action=action)
