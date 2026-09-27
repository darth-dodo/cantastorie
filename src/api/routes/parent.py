"""Parent-area API routes (AI-410, ADR-003).

Only the provision endpoint lives here for now; the /parent pages (sign-in,
pack request form, my-packs) arrive in the next step of the design.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Annotated, Protocol, get_args

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
from pydantic import BaseModel, Field

from src.api.auth import CandidateContext, ParentContext, require_parent, require_parent_candidate
from src.api.clerk import ClerkAPIError, set_family_token
from src.api.routes._nav import fapi_host, home_path
from src.api.routes._templates import templates
from src.api.routes.workshop import (  # shared DI seam, overridable in tests
    _checkpointed_steps,
    _staged_story_summaries,
    get_run_manager,
)
from src.config import Settings, get_settings
from src.pipeline.models import Language, Story, Theme
from src.pipeline.publish import (
    STAGED_PREFIX,
    STORY_FILE,
    _build_client,
    _content_type,
    list_published_stories,
    publish_story,
    unpublish_story,
)
from src.workshop.manager import RunCapExceeded, RunManager
from src.workshop.records import InvalidTransition, PackRequest

if TYPE_CHECKING:
    from datetime import datetime

router = APIRouter(prefix="/parent")


def _published_at(manager: RunManager, family_token: str) -> dict[str, datetime]:
    """The family's published story ids, each with its approval (publish) time."""
    return {
        story_id: record.updated_at
        for record in manager.store.list_runs(family_token=family_token)
        if record.state == "approved"
        for story_id in record.story_ids
    }


def _owned_story_ids(manager: RunManager, family_token: str) -> set[str]:
    return set(_published_at(manager, family_token))


Manager = Annotated[RunManager, Depends(get_run_manager)]


class FamilyPublisher(Protocol):
    def __call__(self, story_id: str, family_token: str) -> None: ...


def get_family_publisher() -> FamilyPublisher:
    """Publish a family's approved story to its private overlay lane.

    The family_token is the tenancy boundary and the R2 prefix: publish_story
    validates it before it becomes a key (src/pipeline/publish.py). Overridable
    in tests, mirroring workshop.get_publisher.
    """

    def publish(story_id: str, family_token: str) -> None:
        publish_story(story_id, get_settings(), family_token=family_token)

    return publish


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
    # Count in-flight (queued/running/staged) runs for tab badge.
    inflight_count = sum(1 for r in runs if r.state in ("queued", "running", "staged"))
    # Count owned published stories for tab badge.
    owned = _owned_story_ids(manager, ctx.family_token)
    owned_count = sum(
        1
        for s in list_published_stories(settings)
        if s.family_token == ctx.family_token and s.id in owned
    )
    return templates.TemplateResponse(
        request,
        "parent/packs.html",
        {
            **context,
            "family_token": ctx.family_token,  # seeds same-device overlay adoption
            "runs": runs,
            "cap_message": None,
            "live": ["queued", "running"],
            "inflight_count": inflight_count,
            "owned_count": owned_count,
        },
    )


@router.get("/stories", response_class=HTMLResponse)
async def parent_stories(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    manager: Manager,
    lang: str | None = None,
    sort: str | None = None,
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
    published_at = _published_at(manager, ctx.family_token)
    all_published = list_published_stories(settings)
    # A family sees only its own overlay lane — never another family's overlay.
    all_stories = [
        s for s in all_published if s.family_token == ctx.family_token and s.id in published_at
    ]
    # Shared shelf stories (family_token is None) visible to every family.
    shared_stories = [s for s in all_published if s.family_token is None]
    # Collect available languages with counts for the filter panel.
    lang_counts: dict[str, int] = {}
    for s in all_stories:
        lang_counts[s.language] = lang_counts.get(s.language, 0) + 1
    # Apply language filter.
    active_lang = lang if lang and lang in lang_counts else None
    stories = [s for s in all_stories if active_lang is None or s.language == active_lang]
    # Sort: "az" = A-Z by title; default = newest published first.
    if sort == "az":
        stories = sorted(stories, key=lambda s: s.title.lower())
    else:
        stories = sorted(stories, key=lambda s: published_at[s.id], reverse=True)
    # Count in-flight runs for the Being Made tab badge.
    all_runs = manager.store.list_runs(family_token=ctx.family_token)
    inflight_count = sum(1 for r in all_runs if r.state in ("queued", "running", "staged"))
    return templates.TemplateResponse(
        request,
        "parent/stories.html",
        {
            **context,
            "stories": stories,
            "shared_stories": shared_stories,
            "lang_counts": lang_counts,
            "active_lang": active_lang,
            "sort": sort or "newest",
            "owned_count": len(all_stories),
            "inflight_count": inflight_count,
        },
    )


@router.get("/make", response_class=HTMLResponse)
async def parent_make(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Make-a-story screen — form on its own page, back-button → /parent/stories."""
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
    return templates.TemplateResponse(
        request,
        "parent/make.html",
        {
            **context,
            "themes": THEMES,
            "languages": LANGUAGES,
        },
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
    # A family delete stays scoped to its own overlay lane — never the shared
    # shelf, never another family. The token is the session's, never the URL's.
    unpublish_story(story_id, settings, family_token=ctx.family_token)
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
    premise: Annotated[str, Form()] = "",
) -> Response:
    # One story at a time: the parent surface never batches a pack, so count is
    # fixed at 1 here rather than read from the form.
    pack = PackRequest(theme=theme, language=language, count=1, premise=premise or None)  # type: ignore[arg-type]
    try:
        record = await manager.submit(ctx.family_token, pack)
    except RunCapExceeded as cap:
        runs = manager.store.list_runs(family_token=ctx.family_token)
        runs.sort(key=lambda r: r.created_at, reverse=True)
        inflight_count = sum(1 for r in runs if r.state in ("queued", "running", "staged"))
        owned = _owned_story_ids(manager, ctx.family_token)
        owned_count = sum(
            1
            for s in list_published_stories(settings)
            if s.family_token == ctx.family_token and s.id in owned
        )
        context: dict[str, object] = {
            "door": "parent",
            "fapi_host": fapi_host(settings),
            "publishable_key": settings.clerk_publishable_key.get_secret_value(),
            "family_token": ctx.family_token,  # seeds same-device overlay adoption
            "runs": runs,
            "cap_message": str(cap),
            "cap_active": cap.active,
            "inflight_count": inflight_count,
            "owned_count": owned_count,
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
    staged_stories = (
        _staged_story_summaries(record.story_ids, settings) if record.state == "staged" else []
    )
    return templates.TemplateResponse(
        request,
        "parent/_run_row.html",
        {
            "record": record,
            "live": ["queued", "running"],
            "steps": _checkpointed_steps(record, settings),
            "staged_stories": staged_stories,
            "base_url": "/parent/packs",
        },
    )


@router.post("/packs/{run_id}/approve")
async def approve_pack(
    request: Request,
    run_id: str,
    ctx: Annotated[ParentContext, Depends(require_parent)],
    manager: Manager,
    publisher: Annotated[FamilyPublisher, Depends(get_family_publisher)],
) -> Response:
    """A family approves its own staged pack → publish to its private overlay.

    Tenancy: the run is loaded family-scoped, so another family's run 404s.
    There is no shared shelf here — every story lands under the family's own
    overlay (published/families/{token}/…), reaching only that family's child.
    """
    record = manager.store.load(ctx.family_token, run_id)  # family-scoped load
    if record is None:
        raise HTTPException(status_code=404)
    if record.state != "staged":
        raise HTTPException(
            status_code=400,
            detail=f"Run is in {record.state} state, must be staged to approve",
        )
    for story_id in record.story_ids:
        publisher(story_id, ctx.family_token)
    manager.store.save(record.advance("approved"))
    if request.headers.get("HX-Request"):
        return HTMLResponse("")
    return RedirectResponse("/parent/stories", status_code=303)


@router.get("/staged/{story_id}", response_class=HTMLResponse)
async def parent_staged_story(
    request: Request,
    story_id: str,
    ctx: Annotated[ParentContext, Depends(require_parent)],
    settings: Annotated[Settings, Depends(get_settings)],
    manager: Manager,
) -> Response:
    """Parent review page: see story pages before approving.

    Tenancy: the run is located by story_id and then confirmed family-scoped —
    a parent cannot reach another family's staged story (returns 404).
    """
    # Find the run that contains this story, then confirm it belongs to this family.
    record = None
    run_id = request.query_params.get("run")
    if run_id:
        candidate = manager.store.load(ctx.family_token, run_id)
        if candidate and story_id in candidate.story_ids:
            record = candidate
    if record is None:
        # Fall back: scan this family's runs only.
        for candidate in manager.store.list_runs(family_token=ctx.family_token):
            if story_id in candidate.story_ids:
                record = candidate
                break
    if record is None:
        raise HTTPException(status_code=404)
    # Load the staged story from the pending bucket.
    client = _build_client(settings)
    bucket = settings.pending_bucket
    try:
        obj = client.get_object(Bucket=bucket, Key=f"{STAGED_PREFIX}/{story_id}/{STORY_FILE}")
    except Exception:
        raise HTTPException(status_code=404) from None
    story = Story.model_validate_json(obj["Body"].read())
    return templates.TemplateResponse(
        request,
        "parent/review.html",
        {
            "fapi_host": fapi_host(settings),
            "publishable_key": settings.clerk_publishable_key.get_secret_value(),
            "story": story,
            "record": record,
            "base_url": "/parent/packs",
        },
    )


@router.get("/staged/{story_id}/assets/{name}")
async def parent_staged_asset(
    story_id: str,
    name: str,
    ctx: Annotated[ParentContext, Depends(require_parent)],
    settings: Annotated[Settings, Depends(get_settings)],
    manager: Manager,
) -> Response:
    """Serve a staged asset (image/audio) family-scoped."""
    if "/" in name or ".." in name:
        raise HTTPException(status_code=404)
    # Confirm family owns a run containing this story.
    owned = any(
        story_id in r.story_ids for r in manager.store.list_runs(family_token=ctx.family_token)
    )
    if not owned:
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


@router.post("/packs/{run_id}/reject")
async def reject_pack(
    request: Request,
    run_id: str,
    ctx: Annotated[ParentContext, Depends(require_parent)],
    manager: Manager,
) -> Response:
    """A family rejects its own staged pack."""
    record = manager.store.load(ctx.family_token, run_id)
    if record is None:
        raise HTTPException(status_code=404)
    try:
        manager.store.save(record.advance("rejected"))
    except InvalidTransition:
        raise HTTPException(status_code=400) from None
    if request.headers.get("HX-Request"):
        return HTMLResponse("")
    return RedirectResponse("/parent", status_code=303)


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
