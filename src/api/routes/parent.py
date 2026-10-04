"""Parent-area routes (AI-410, ADR-003): the /parent pages and their actions.

Sign-in and provisioning, Your stories, the make-a-story form, and Being made.
Each run a family requests makes one story (AI-480): POST /parent/runs starts
it, /parent/runs/{id}/progress polls its row, and approve/reject settle it
after the review page at /parent/staged/{story_id}. Every read and write is
scoped to the session's family token.
"""

from __future__ import annotations

import secrets
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Protocol, get_args

from botocore.exceptions import ClientError
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Request,
    Response,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field, ValidationError

from src.api.auth import CandidateContext, ParentContext, require_parent, require_parent_candidate
from src.api.clerk import ClerkAPIError, set_family_token
from src.api.routes._nav import fapi_host, home_path
from src.api.routes._templates import templates
from src.api.routes.workshop import (  # shared DI seam, overridable in tests
    _checkpointed_steps,
    _staged_story_summary,
    get_run_manager,
)
from src.config import Settings, get_settings
from src.pipeline.models import PREMISE_MAX_LENGTH, Language, Story, Theme
from src.pipeline.publish import (
    STAGED_PREFIX,
    STORY_FILE,
    PublishedStory,
    StagedContentChangedError,
    _build_client,
    _content_type,
    list_family_shelf,
    publish_story,
    staged_digest,
    unpublish_story,
)
from src.workshop.manager import RunCapExceeded, RunManager, blocking_cap
from src.workshop.records import (
    ConcurrentModificationError,
    InvalidTransition,
    RunRecord,
    StoryRequest,
    story_request_error_message,
)

if TYPE_CHECKING:
    from datetime import datetime

router = APIRouter(prefix="/parent")


def _published_at(runs: list[RunRecord]) -> dict[str, datetime]:
    """The family's published story ids, each with its approval (publish) time."""
    return {
        record.story_id: record.updated_at
        for record in runs
        if record.state == "approved" and record.story_id is not None
    }


def _owned_story_ids(manager: RunManager, family_token: str) -> set[str]:
    return set(_published_at(manager.store.list_runs(family_token=family_token)))


@dataclass(frozen=True)
class FamilyView:
    """Everything a family's parent pages show, read once per request (AI-465)."""

    runs: list[RunRecord]  # newest first
    published_at: dict[str, datetime]
    owned: list[PublishedStory]  # this family's published stories
    shared: list[PublishedStory]  # the shared shelf

    @property
    def inflight_count(self) -> int:
        return sum(1 for r in self.runs if r.state in ("queued", "running", "staged"))


def _family_view(manager: RunManager, settings: Settings, family_token: str) -> FamilyView:
    """Read the family's runs and its shelf together: two independent R2 reads
    overlap, so the page waits for the slower one, not their sum. Blocking —
    callers run it off the event loop."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs_read = pool.submit(manager.store.list_runs, family_token=family_token)
        shelf_read = pool.submit(list_family_shelf, settings, family_token)
        runs = sorted(runs_read.result(), key=lambda r: r.created_at, reverse=True)
        shelf = shelf_read.result()
    published_at = _published_at(runs)
    return FamilyView(
        runs=runs,
        published_at=published_at,
        # A family sees only its own overlay lane — never another family's.
        owned=[s for s in shelf if s.family_token == family_token and s.id in published_at],
        shared=[s for s in shelf if s.family_token is None],
    )


Manager = Annotated[RunManager, Depends(get_run_manager)]


class FamilyPublisher(Protocol):
    def __call__(self, story_id: str, family_token: str, expected_digest: str) -> None: ...


def get_family_publisher() -> FamilyPublisher:
    """Publish a family's approved story to its private overlay lane.

    The family_token is the tenancy boundary and the R2 prefix: publish_story
    validates it before it becomes a key (src/pipeline/publish.py). Overridable
    in tests, mirroring workshop.get_publisher. ``expected_digest`` is the
    reviewed digest: the publish refuses staged bytes that no longer match it
    (H1).
    """

    def publish(story_id: str, family_token: str, expected_digest: str) -> None:
        publish_story(
            story_id, get_settings(), family_token=family_token, expected_digest=expected_digest
        )

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
    # Provisioned parents land on Being made with their own runs, newest first.
    view = await run_in_threadpool(_family_view, manager, settings, ctx.family_token)
    return templates.TemplateResponse(
        request,
        "parent/being_made.html",
        {
            **context,
            "family_token": ctx.family_token,  # seeds same-device overlay adoption
            "runs": view.runs,
            "cap_message": None,
            "live": ["queued", "running"],
            "inflight_count": view.inflight_count,
            "owned_count": len(view.owned),
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
        "door": "parent",
        "fapi_host": fapi_host(settings),
        "publishable_key": settings.clerk_publishable_key.get_secret_value(),
    }
    if ctx is None:
        return templates.TemplateResponse(request, "auth/sign_in.html", context)
    if ctx.family_token is None:
        context["onboarding"] = True
        return templates.TemplateResponse(request, "auth/sign_in.html", context)
    view = await run_in_threadpool(_family_view, manager, settings, ctx.family_token)
    published_at = view.published_at
    all_stories = view.owned
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
    return templates.TemplateResponse(
        request,
        "parent/stories.html",
        {
            **context,
            "stories": stories,
            "shared_stories": view.shared,
            "lang_counts": lang_counts,
            "active_lang": active_lang,
            "sort": sort or "newest",
            "owned_count": len(all_stories),
            "inflight_count": view.inflight_count,
        },
    )


async def _make_ctx(
    family_token: str,
    settings: Settings,
    manager: RunManager,
    *,
    form_error: str | None = None,
) -> dict[str, object]:
    # One story at a time: show the cap up front, not after the form is filled.
    runs = await run_in_threadpool(manager.store.list_runs, family_token=family_token)
    cap = blocking_cap(runs, settings.parent_daily_run_cap)
    return {
        "door": "parent",
        "fapi_host": fapi_host(settings),
        "publishable_key": settings.clerk_publishable_key.get_secret_value(),
        "themes": THEMES,
        "languages": LANGUAGES,
        "cap_message": str(cap) if cap else None,
        "cap_active": cap.active if cap else None,
        "form_error": form_error,
        "premise_max_length": PREMISE_MAX_LENGTH,
    }


@router.get("/make", response_class=HTMLResponse)
async def parent_make(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    manager: Manager,
) -> Response:
    """Make-a-story screen — form on its own page, back-button → /parent/stories."""
    ctx = await _page_identity(request, settings)
    if ctx is not None and ctx.is_operator:
        return RedirectResponse(home_path(True), status_code=303)
    context: dict[str, object] = {
        "door": "parent",
        "fapi_host": fapi_host(settings),
        "publishable_key": settings.clerk_publishable_key.get_secret_value(),
    }
    if ctx is None:
        return templates.TemplateResponse(request, "auth/sign_in.html", context)
    if ctx.family_token is None:
        context["onboarding"] = True
        return templates.TemplateResponse(request, "auth/sign_in.html", context)
    return templates.TemplateResponse(
        request, "parent/make.html", await _make_ctx(ctx.family_token, settings, manager)
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


@router.post("/runs")
async def parent_request_story(
    request: Request,
    ctx: Annotated[ParentContext, Depends(require_parent)],
    settings: Annotated[Settings, Depends(get_settings)],
    manager: Manager,
    background: BackgroundTasks,
    theme: Annotated[str, Form()],
    language: Annotated[str, Form()],
    premise: Annotated[str, Form()] = "",
) -> Response:
    try:
        story_request = StoryRequest(theme=theme, language=language, premise=premise or None)  # type: ignore[arg-type]
    except ValidationError as error:
        ctx_dict = await _make_ctx(
            ctx.family_token, settings, manager, form_error=story_request_error_message(error)
        )
        return templates.TemplateResponse(request, "parent/make.html", ctx_dict, status_code=422)
    try:
        record = await manager.submit(ctx.family_token, story_request)
    except RunCapExceeded as cap:
        view = await run_in_threadpool(_family_view, manager, settings, ctx.family_token)
        context: dict[str, object] = {
            "door": "parent",
            "fapi_host": fapi_host(settings),
            "publishable_key": settings.clerk_publishable_key.get_secret_value(),
            "family_token": ctx.family_token,  # seeds same-device overlay adoption
            "runs": view.runs,
            "cap_message": str(cap),
            "cap_active": cap.active,
            "inflight_count": view.inflight_count,
            "owned_count": len(view.owned),
        }
        return templates.TemplateResponse(request, "parent/being_made.html", context)
    background.add_task(manager.execute, record)
    return RedirectResponse("/parent", status_code=303)


@router.get("/runs/{run_id}/progress", response_class=HTMLResponse)
async def parent_run_progress(
    request: Request,
    run_id: str,
    ctx: Annotated[ParentContext, Depends(require_parent)],
    settings: Annotated[Settings, Depends(get_settings)],
    manager: Manager,
) -> HTMLResponse:
    # Polled every 2 s while a run is live: keep its R2 reads off the event loop.
    def read_record() -> RunRecord | None:
        manager.reap_stale()  # a stale run's own poll heals it, so a family
        # is never stuck waiting on an operator to notice (AI-417, M9).
        return manager.store.load(ctx.family_token, run_id)  # tenancy

    record = await run_in_threadpool(read_record)
    if record is None:
        raise HTTPException(status_code=404)
    staged_story = (
        await run_in_threadpool(_staged_story_summary, record.story_id, settings)
        if record.state == "staged"
        else None
    )
    return templates.TemplateResponse(
        request,
        "parent/_run_row.html",
        {
            "record": record,
            "live": ["queued", "running"],
            "steps": _checkpointed_steps(record, settings),
            "staged_story": staged_story,
            "base_url": "/parent/runs",
        },
    )


@router.post("/runs/{run_id}/approve")
async def parent_approve_run(
    request: Request,
    run_id: str,
    ctx: Annotated[ParentContext, Depends(require_parent)],
    settings: Annotated[Settings, Depends(get_settings)],
    manager: Manager,
    publisher: Annotated[FamilyPublisher, Depends(get_family_publisher)],
) -> Response:
    """A family approves its own staged story → publish to its private overlay.

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
    # No approve without review (B2): the run's story must still exist and
    # have been opened on the review page, which renders all of its pages.
    story_id = record.story_id
    if story_id is None:
        raise HTTPException(status_code=409, detail="The run has no staged story to publish")
    if not record.fully_reviewed or record.reviewed_digest is None:
        raise HTTPException(status_code=409, detail="Review the story before approving")
    # The reviewed bytes, and only those, may be published (H1): the staged
    # story is checked here for a clear answer, and again inside the publish
    # for a write that lands in between.
    current = await run_in_threadpool(_current_staged_digest, settings, story_id)
    if current is None:
        raise HTTPException(status_code=409, detail="The staged story is missing")
    if current != record.reviewed_digest:
        raise HTTPException(status_code=409, detail=_CHANGED_SINCE_REVIEW)
    try:
        publisher(story_id, ctx.family_token, record.reviewed_digest)
    except StagedContentChangedError:
        raise HTTPException(status_code=409, detail=_CHANGED_SINCE_REVIEW) from None
    manager.store.save(record.advance("approved"))
    if request.headers.get("HX-Request"):
        return HTMLResponse("")
    return RedirectResponse("/parent/stories", status_code=303)


_CHANGED_SINCE_REVIEW = "The story changed since you reviewed it. Open it again to review it."


def _current_staged_digest(settings: Settings, story_id: str) -> str | None:
    """The staged story's digest now, or None when it is gone."""
    try:
        return staged_digest(story_id, settings, client=_build_client(settings))
    except ClientError:
        return None


def _record_review(manager: RunManager, record: RunRecord, story_id: str, digest: str) -> RunRecord:
    """The parent has been served the run's staged story on its review page —
    every page, picture and sound on one screen — so the run counts as
    reviewed (B2), bound to the digest of exactly those bytes (H1). Blocking."""
    if record.state != "staged" or record.story_id != story_id:
        return record
    if record.reviewed and record.reviewed_digest == digest:
        return record
    try:
        reviewed = record.mark_reviewed(digest)
        manager.store.save(reviewed)
    except ConcurrentModificationError:
        # A concurrent write (another tab's review) moved the record on: redo it once.
        fresh = manager.store.load(record.family_token, record.id)
        if fresh is None or fresh.state != "staged":
            return fresh or record
        reviewed = fresh.mark_reviewed(digest)
        manager.store.save(reviewed)
    return reviewed


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
    run_id = request.query_params.get("run")

    def read_owned_story() -> tuple[RunRecord, Story] | None:
        # Find the run that contains this story, then confirm it belongs to this family.
        record = None
        if run_id:
            candidate = manager.store.load(ctx.family_token, run_id)
            if candidate and candidate.story_id == story_id:
                record = candidate
        if record is None:
            # Fall back: scan this family's runs only.
            for candidate in manager.store.list_runs(family_token=ctx.family_token):
                if candidate.story_id == story_id:
                    record = candidate
                    break
        if record is None:
            return None
        # Load the staged story from the pending bucket.
        client = _build_client(settings)
        try:
            obj = client.get_object(
                Bucket=settings.pending_bucket, Key=f"{STAGED_PREFIX}/{story_id}/{STORY_FILE}"
            )
        except Exception:
            return None
        story_bytes = obj["Body"].read()
        story = Story.model_validate_json(story_bytes)
        # The digest of exactly the bytes this page renders (H1).
        digest = staged_digest(story_id, settings, client=client, story_bytes=story_bytes)
        return _record_review(manager, record, story_id, digest), story

    found = await run_in_threadpool(read_owned_story)
    if found is None:
        raise HTTPException(status_code=404)
    record, story = found
    return templates.TemplateResponse(
        request,
        "parent/review.html",
        {
            "door": "parent",  # sign-out returns to the parent door
            "fapi_host": fapi_host(settings),
            "publishable_key": settings.clerk_publishable_key.get_secret_value(),
            "story": story,
            "record": record,
            "base_url": "/parent/runs",
        },
    )


@router.get("/staged/{story_id}/assets/{name}")
async def parent_staged_asset(
    story_id: str,
    name: str,
    ctx: Annotated[ParentContext, Depends(require_parent)],
    settings: Annotated[Settings, Depends(get_settings)],
    manager: Manager,
    run: str | None = None,
) -> Response:
    """Serve a staged asset (image/audio) family-scoped.

    The review page passes ``?run=`` so ownership is one family-scoped record
    read instead of a scan of every run the family has — per asset, that is
    the difference between one R2 call and a dozen (AI-465).
    """
    if "/" in name or ".." in name:
        raise HTTPException(status_code=404)

    def read_owned_asset() -> bytes | None:
        # Tenancy: load() and list_runs() are both scoped to the session's family.
        if run:
            record = manager.store.load(ctx.family_token, run)
            owned = record is not None and record.story_id == story_id
        else:
            owned = any(
                r.story_id == story_id
                for r in manager.store.list_runs(family_token=ctx.family_token)
            )
        if not owned:
            return None
        try:
            obj = _build_client(settings).get_object(
                Bucket=settings.pending_bucket, Key=f"{STAGED_PREFIX}/{story_id}/{name}"
            )
        except Exception:
            return None
        return obj["Body"].read()

    body = await run_in_threadpool(read_owned_asset)
    if body is None:
        raise HTTPException(status_code=404)
    return Response(
        content=body,
        media_type=_content_type(name),
        # Asset names embed a content hash, so a name's bytes never change:
        # the browser may keep them (privately) instead of refetching each visit.
        headers={"Cache-Control": "private, max-age=86400, immutable"},
    )


@router.post("/runs/{run_id}/reject")
async def parent_reject_run(
    request: Request,
    run_id: str,
    ctx: Annotated[ParentContext, Depends(require_parent)],
    manager: Manager,
) -> Response:
    """A family rejects its own staged story."""
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
