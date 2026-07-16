"""Clerk Backend API client (AI-410, ADR-003).

The ONLY module in the codebase that calls Clerk's REST API. One operation:
writing the family token into a user's public_metadata at provision time.
Plain httpx — no Clerk SDK (settled: the request path verifies JWTs locally
via JWKS and never talks to Clerk; this single write is the exception).

Endpoint verified against Clerk docs 2026-07-15: PATCH
/v1/users/{user_id}/metadata deep-merges public_metadata. (Clerk's
2026-05-12 API change removed metadata fields from PATCH /v1/users/{id}.)
"""

from __future__ import annotations

import httpx

from src.config import Settings  # noqa: TC001

CLERK_API_BASE = "https://api.clerk.com/v1"

_TIMEOUT_SECONDS = 10.0


class ClerkAPIError(Exception):
    """Clerk REST call failed. Message carries the status, never the key."""


async def set_family_token(
    user_id: str,
    family_token: str,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Write family_token into the Clerk user's public_metadata.

    Deep-merge PATCH: only the family_token key is touched; other metadata
    (e.g. the disabled kill switch) is preserved. The *transport* parameter
    is the test seam (httpx.MockTransport), matching the NarrationClient
    pattern in the pipeline tests.
    """
    url = f"{CLERK_API_BASE}/users/{user_id}/metadata"
    headers = {
        "Authorization": f"Bearer {settings.clerk_secret_key.get_secret_value()}",
    }
    payload = {"public_metadata": {"family_token": family_token}}
    try:
        async with httpx.AsyncClient(transport=transport, timeout=_TIMEOUT_SECONDS) as client:
            response = await client.patch(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise ClerkAPIError(f"Clerk metadata update failed: {type(exc).__name__}") from exc
    if response.is_error:
        raise ClerkAPIError(f"Clerk metadata update failed with status {response.status_code}")
