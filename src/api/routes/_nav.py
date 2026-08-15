"""Shared post-sign-in navigation: where each role belongs.

The one source of truth for a role's home route. Every authed page entry
point (workshop, parent) serves its own role and 303-redirects the other
role here — so no role ever hits a dead-end. Because each role has exactly
one *serving* home, redirects can never loop.
"""

from __future__ import annotations


def home_path(is_operator: bool) -> str:
    """The route a signed-in user belongs on: operators author in the
    workshop; everyone else lives in the parent area."""
    return "/workshop" if is_operator else "/parent"
