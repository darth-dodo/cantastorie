"""Response-hardening middleware for the whole app (AI-496).

Pure ASGI rather than BaseHTTPMiddleware, so streamed responses (story audio,
staged assets) pass through untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.datastructures import MutableHeaders

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

# M16. The CSP is deliberately the minimal, source-free set: the parent and
# workshop pages run inline scripts and load ClerkJS from the instance's
# Frontend API host, and the player fetches stories, audio and art from
# ASSET_BASE — a script-src/default-src allow-list would have to track all of
# that. These directives restrict nothing a page legitimately loads.
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'; base-uri 'self'; object-src 'none'",
}


class SecurityHeadersMiddleware:
    """Stamp SECURITY_HEADERS onto every HTTP response, errors included."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)
