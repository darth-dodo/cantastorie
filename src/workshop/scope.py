"""WorkshopScope: what a verified Clerk session is allowed to do in the workshop.

Resolved from JWT claims (never a per-request Clerk API call). Operators work
globally and publish to the shared shelf; everyone else is a parent, confined
to their own family_token partition and publishing to a family overlay. The
security boundary lives here — keep it a pure, exhaustively tested function.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

OPERATOR_STORE_TOKEN = "operator"

OPERATOR_ROLE = "operator"


@dataclass(frozen=True)
class WorkshopScope:
    user_id: str
    is_operator: bool
    store_token: str
    publish_target: str  # "shared" for operators, "overlay" for families


def resolve_scope(claims: dict[str, Any]) -> WorkshopScope:
    user_id = str(claims.get("sub", ""))
    if claims.get("role") == OPERATOR_ROLE:
        return WorkshopScope(
            user_id=user_id,
            is_operator=True,
            store_token=OPERATOR_STORE_TOKEN,
            publish_target="shared",
        )
    family_token = claims.get("family_token")
    return WorkshopScope(
        user_id=user_id,
        is_operator=False,
        store_token=family_token if isinstance(family_token, str) and family_token else "",
        publish_target="overlay",
    )
