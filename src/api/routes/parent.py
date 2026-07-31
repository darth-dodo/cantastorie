"""Parent-area API routes (AI-410, ADR-003).

Only the provision endpoint lives here for now; the /parent pages (sign-in,
pack request form, my-packs) arrive in the next step of the design.
"""

from __future__ import annotations

import secrets
from base64 import b64decode
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
from src.api.routes.workshop import get_run_manager  # shared DI seam, overridable in tests
from src.config import Settings, get_settings
from src.pipeline.models import Language, Theme
from src.workshop.manager import RunCapExceeded, RunManager
from src.workshop.records import PackRequest

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

router = APIRouter(prefix="/parent")

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


def _fapi_host(settings: Settings) -> str:
    """Frontend-API host for the ClerkJS CDN script tags.

    Prefer the configured issuer (it IS the frontend API origin); fall back to
    decoding the publishable key (base64 of the host + '$', after the last '_').
    """
    if settings.clerk_issuer:
        return settings.clerk_issuer.removeprefix("https://").removeprefix("http://")
    pk = settings.clerk_publishable_key.get_secret_value()
    encoded = pk.rsplit("_", 1)[-1]
    padded = encoded + "=" * (-len(encoded) % 4)
    return b64decode(padded).decode().rstrip("$")


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
) -> HTMLResponse:
    ctx = await _page_identity(request, settings)
    context: dict[str, object] = {
        "fapi_host": _fapi_host(settings),
        "publishable_key": settings.clerk_publishable_key.get_secret_value(),
    }
    if ctx is None:
        return templates.TemplateResponse(request, "parent/signin.html", context)
    if ctx.family_token is None:
        # First sign-in: page JS POSTs /parent/api/provision then reloads.
        context["onboarding"] = True
        return templates.TemplateResponse(request, "parent/signin.html", context)
    # Provisioned parents get the packs page — Task 4 fills in the run list;
    # until then render it with an empty runs list.
    return templates.TemplateResponse(
        request,
        "parent/packs.html",
        {
            **context,
            "runs": [],
            "cap_message": None,
            "themes": THEMES,
            "languages": LANGUAGES,
        },
    )


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
            "fapi_host": _fapi_host(settings),
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
