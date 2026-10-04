"""Request- and response-hardening middleware for the whole app (AI-496).

Pure ASGI rather than BaseHTTPMiddleware, so streamed responses (story audio,
staged assets) pass through untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse

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


# M4. The /workshop and /parent write routes are authenticated by Clerk's
# session cookie alone; SameSite=Lax keeps it off most cross-site requests,
# but this makes the refusal explicit and independent of cookie attributes.
GUARDED_PREFIXES = ("/workshop", "/parent")
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DEFAULT_PORTS = {"http": "80", "https": "443"}


def _is_guarded(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in GUARDED_PREFIXES)


def _own_origins(scope: Scope, headers: Headers) -> set[str]:
    """Every spelling of this request's own origin.

    Behind Render's TLS-terminating proxy the app sees plain http while the
    browser's Origin says https, so the scheme the proxy forwarded counts too.
    A cross-site page cannot set X-Forwarded-Proto on a browser request
    without a CORS preflight, which this app never grants.
    """
    host = headers.get("host", "")
    schemes = {str(scope.get("scheme", "http"))}
    forwarded = headers.get("x-forwarded-proto", "")
    if forwarded:
        schemes.add(forwarded.split(",", 1)[0].strip().lower())
    origins = set()
    for scheme in schemes:
        origins.add(f"{scheme}://{host}")
        default_port = DEFAULT_PORTS.get(scheme)
        if default_port and host.endswith(f":{default_port}"):
            origins.add(f"{scheme}://{host.removesuffix(f':{default_port}')}")
    return origins


def is_cross_origin_write(scope: Scope) -> bool:
    """True when a browser sent a state-changing /workshop or /parent request
    from another origin. Requests with neither Origin nor Sec-Fetch-Site
    (curl, the test suite) are not browser-forged and pass."""
    if scope["method"] not in UNSAFE_METHODS or not _is_guarded(scope["path"]):
        return False
    headers = Headers(scope=scope)
    origin = headers.get("origin")
    if origin is not None:
        return origin.lower() not in {o.lower() for o in _own_origins(scope, headers)}
    return headers.get("sec-fetch-site") == "cross-site"


class CrossOriginWriteGuard:
    """Answer 403 to a cross-origin state-changing request before routing."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and is_cross_origin_write(scope):
            response = JSONResponse({"detail": "Cross-origin request refused"}, status_code=403)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
