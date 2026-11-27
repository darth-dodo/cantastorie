"""Request- and response-hardening middleware for the whole app (AI-496).

Pure ASGI rather than BaseHTTPMiddleware, so streamed responses (story audio,
staged assets) pass through untouched.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse

from src.api.routes._nav import fapi_host

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

    from src.config import Settings

# M16. Every response carries these; the Content-Security-Policy is added per
# path by SecurityPolicy.apply (AI-505).
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
}
CSP_HEADER = "Content-Security-Policy"
CSP_REPORT_ONLY_HEADER = "Content-Security-Policy-Report-Only"

# Clerk's documented sign-in needs: bot protection (Cloudflare Turnstile),
# fraud protection, avatars, and blob: workers. Admin paths only (ADR-003).
CLERK_BOT_PROTECTION = "https://challenges.cloudflare.com"
CLERK_FRAUD_PROTECTION = "https://*.protect.clerk.com"
CLERK_IMAGES = "https://img.clerk.com"


def _origin(value: str) -> str | None:
    """The scheme, host and port of an absolute http(s) URL, never its userinfo."""
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    port = f":{parts.port}" if parts.port is not None else ""
    return f"{parts.scheme}://{host}{port}"


def _is_same_origin_path(value: str) -> bool:
    parts = urlsplit(value)
    return not parts.scheme and not parts.netloc


def _asset_origins(settings: Settings) -> list[str]:
    """The origins of ASSET_BASE and R2_PUBLIC_BASE that are absolute URLs.

    A same-origin path adds nothing. Any other value the policy cannot use
    (protocol-relative, a non-http scheme) is left out with a warning, since
    the browser would then block the assets it names.
    """
    origins: list[str] = []
    for name, value in (
        ("ASSET_BASE", settings.asset_base),
        ("R2_PUBLIC_BASE", settings.r2_public_base),
    ):
        if not value or _is_same_origin_path(value):
            continue
        origin = _origin(value)
        if origin is None:
            logger.warning(
                "csp_asset_origin_ignored",
                extra={"event": "csp_asset_origin_ignored", "setting": name},
            )
            continue
        if origin not in origins:
            origins.append(origin)
    return origins


def _serialize(directives: dict[str, list[str]]) -> str:
    return "; ".join(f"{name} {' '.join(sources)}" for name, sources in directives.items())


def _public_directives(settings: Settings) -> dict[str, list[str]]:
    assets = ["'self'", *_asset_origins(settings)]
    return {
        "default-src": ["'self'"],
        "script-src": ["'self'"],
        "style-src": ["'self'"],
        "font-src": ["'self'"],
        "img-src": list(assets),
        "media-src": list(assets),
        "connect-src": list(assets),
        "base-uri": ["'self'"],
        "object-src": ["'none'"],
        "frame-ancestors": ["'none'"],
        "form-action": ["'self'"],
    }


def build_policies(settings: Settings) -> tuple[str, str]:
    """The (public, admin) Content-Security-Policy values for these settings.

    Public covers the child player, the landing page and every non-admin
    response: scripts, styles and fonts from this origin only, story assets
    from the published asset origin too. Admin adds what Clerk's sign-in needs
    on /parent and /workshop; with Clerk unset those paths are 404s, so the
    admin policy is the public one.
    """
    public = _public_directives(settings)
    if not settings.clerk_jwks_url:
        policy = _serialize(public)
        return policy, policy
    fapi = f"https://{fapi_host(settings)}"
    admin = _public_directives(settings)
    admin["script-src"] += [fapi, CLERK_BOT_PROTECTION, CLERK_FRAUD_PROTECTION]
    admin["style-src"].append("'unsafe-inline'")
    admin["img-src"] += [CLERK_IMAGES, "data:"]
    admin["connect-src"] += [fapi, f"{CLERK_FRAUD_PROTECTION}:*"]
    admin["frame-src"] = [CLERK_BOT_PROTECTION, CLERK_FRAUD_PROTECTION]
    admin["worker-src"] = ["'self'", "blob:"]
    return _serialize(public), _serialize(admin)


class SecurityPolicy:
    """The headers for one process: built once from settings at app start."""

    def __init__(self, settings: Settings) -> None:
        self.public, self.admin = build_policies(settings)
        self.header = CSP_REPORT_ONLY_HEADER if settings.csp_report_only else CSP_HEADER

    def apply(self, headers: MutableHeaders, path: str) -> None:
        for name, value in SECURITY_HEADERS.items():
            headers.setdefault(name, value)
        headers.setdefault(self.header, self.admin if _is_guarded(path) else self.public)


class SecurityHeadersMiddleware:
    """Stamp the hardening headers and the path's policy onto every HTTP
    response that passes through it (500s are stamped by the app's
    Exception handler, which runs outside every user middleware)."""

    def __init__(self, app: ASGIApp, policy: SecurityPolicy) -> None:
        self.app = app
        self.policy = policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                self.policy.apply(MutableHeaders(scope=message), scope["path"])
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
