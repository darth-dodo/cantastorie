"""Shared post-sign-in navigation: where each role belongs.

The one source of truth for a role's home route. Every authed page entry
point (workshop, parent) serves its own role and 303-redirects the other
role here — so no role ever hits a dead-end. Because each role has exactly
one *serving* home, redirects can never loop.
"""

from __future__ import annotations

from base64 import b64decode
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import Settings


def home_path(is_operator: bool) -> str:
    """The route a signed-in user belongs on: operators author in the
    workshop; everyone else lives in the parent area."""
    return "/workshop" if is_operator else "/parent"


def fapi_host(settings: Settings) -> str:
    """The Clerk Frontend API host scripts load from: the issuer host when
    set, else the domain encoded in the publishable key's suffix."""
    if settings.clerk_issuer:
        return settings.clerk_issuer.removeprefix("https://").removeprefix("http://")
    pk = settings.clerk_publishable_key.get_secret_value()
    encoded = pk.rsplit("_", 1)[-1]
    padded = encoded + "=" * (-len(encoded) % 4)
    return b64decode(padded).decode().rstrip("$")
