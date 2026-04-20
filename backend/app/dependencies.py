"""
FastAPI dependencies for request-level concerns (rate limiting, auth, etc.).
"""

import hmac
import json
import logging
import os
from urllib.parse import urlparse
import time as _time

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from app.services.rate_limiter import (
    check_rate_limit,
    CHAT_SESSION_LIMITS,
    CHAT_IP_LIMITS,
    FEEDBACK_SESSION_LIMITS,
    ADMIN_IP_LIMITS,
    ADMIN_EVAL_LIMITS,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Admin API key authentication
# ---------------------------------------------------------------------------

def require_admin_key(request: Request) -> None:
    """Dependency that enforces admin API key authentication.

    When ADMIN_API_KEY is set, requests must include a matching
    ``Authorization: Bearer <key>`` header.  When the env var is
    unset, all requests are allowed (local-dev convenience).
    """
    expected = os.environ.get("ADMIN_API_KEY")
    if not expected:
        return  # No key configured — open access (dev mode)
    auth = request.headers.get("authorization", "")
    if hmac.compare_digest(auth, f"Bearer {expected}"):
        return
    raise HTTPException(
        status_code=401,
        detail="Missing or invalid admin API key",
    )


# ---------------------------------------------------------------------------
# 429 response body — compassionate, with crisis resources
# ---------------------------------------------------------------------------

_RATE_LIMIT_RESPONSE = (
    "You\u2019re sending messages very quickly. "
    "Please wait a moment and try again."
)

_CRISIS_RESOURCES = (
    "If you need immediate help, call 311 for NYC services "
    "or 988 for the Suicide & Crisis Lifeline."
)


# ---------------------------------------------------------------------------
# Client IP extraction
# ---------------------------------------------------------------------------

def get_client_ip(request: Request) -> str:
    """Extract the real client IP, respecting proxy headers.

    Render (and most reverse proxies) set ``X-Forwarded-For``.
    We take the *last* entry — the one appended by the trusted proxy —
    and fall back to the direct connection address.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Render (and most single-layer reverse proxies) appends the true
        # client IP as the last entry.  Taking the first entry is spoofable —
        # an attacker can prepend a fake IP to evade rate limits and bans.
        return forwarded.split(",")[-1].strip()
    if request.client:
        return request.client.host
    return "unknown"


# ---------------------------------------------------------------------------
# Rate-limit middleware
# ---------------------------------------------------------------------------

class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply rate limits to chat endpoints as middleware.

    Using middleware instead of Depends() because the rate limiter needs the
    raw request body (for session_id) before the route handler parses it.
    FastAPI's body caching makes this safe — the route handler still receives
    the full body.

    Only applies to /chat/, /chat/feedback, and /admin/api/ endpoints.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint,
    ):
        path = request.url.path.rstrip("/")

        # --- Chat endpoints (POST only) ---
        if request.method == "POST":
            if path == "/chat":
                return await self._check_chat(request, call_next)
            elif path == "/chat/feedback":
                return await self._check_feedback(request, call_next)

        # --- Admin endpoints (all methods) ---
        if path.startswith("/admin/api"):
            return await self._check_admin(request, call_next)

        return await call_next(request)

    async def _check_chat(self, request: Request, call_next):
        client_ip = get_client_ip(request)
        session_id = await _extract_session_id(request)

        # Check per-session limits (if session_id is present)
        if session_id:
            result = check_rate_limit(f"session:{session_id}", CHAT_SESSION_LIMITS)
            if not result.allowed:
                logger.warning(
                    "Rate limited session=%s ip=%s (session limit: %d/%ds)",
                    session_id, client_ip, result.limit, result.window,
                )
                return _build_429(result.retry_after)

        # Check per-IP limits (always)
        result = check_rate_limit(f"ip:{client_ip}", CHAT_IP_LIMITS)
        if not result.allowed:
            logger.warning(
                "Rate limited ip=%s (IP limit: %d/%ds)",
                client_ip, result.limit, result.window,
            )
            return _build_429(result.retry_after)

        return await call_next(request)

    async def _check_feedback(self, request: Request, call_next):
        session_id = await _extract_session_id(request)

        if session_id:
            result = check_rate_limit(
                f"feedback:{session_id}", FEEDBACK_SESSION_LIMITS,
            )
            if not result.allowed:
                return _build_429(result.retry_after)

        return await call_next(request)

    async def _check_admin(self, request: Request, call_next):
        client_ip = get_client_ip(request)
        path = request.url.path.rstrip("/")

        # Stricter limit on eval runs — they consume LLM API credits
        if path == "/admin/api/eval/run" and request.method == "POST":
            result = check_rate_limit(f"admin-eval:{client_ip}", ADMIN_EVAL_LIMITS)
            if not result.allowed:
                logger.warning(
                    "Rate limited admin eval ip=%s (limit: %d/%ds)",
                    client_ip, result.limit, result.window,
                )
                return _build_429(result.retry_after)

        # General admin IP limits (all endpoints)
        result = check_rate_limit(f"admin:{client_ip}", ADMIN_IP_LIMITS)
        if not result.allowed:
            logger.warning(
                "Rate limited admin ip=%s (limit: %d/%ds)",
                client_ip, result.limit, result.window,
            )
            return _build_429(result.retry_after)

        return await call_next(request)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _extract_session_id(request: Request) -> str | None:
    """Read session_id from the JSON body without consuming it."""
    try:
        body = await request.body()
        if body:
            data = json.loads(body)
            return data.get("session_id")
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return None


def _build_429(retry_after: int) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "detail": _RATE_LIMIT_RESPONSE,
            "crisis_resources": _CRISIS_RESOURCES,
            "retry_after": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )


# ---------------------------------------------------------------------------
# Bot detection — block scanners, honeypot banning
# ---------------------------------------------------------------------------

# User-Agent substrings associated with vulnerability scanners and SEO bots.
# Not foolproof, but eliminates low-effort automated traffic.
_BLOCKED_USER_AGENTS = [
    "sqlmap", "nikto", "nmap", "masscan", "zgrab",
    "semrush", "ahrefs", "mj12bot", "dotbot", "petalbot",
    "censys", "shodan", "nuclei", "httpx", "gobuster",
    "dirbuster", "wfuzz", "ffuf",
]

# Paths that real users never hit but scanners always try.
# Any IP hitting these gets temporarily banned.
_HONEYPOT_PATHS = {
    "/wp-admin", "/wp-login.php", "/.env", "/phpmyadmin",
    "/.git/config", "/actuator", "/debug", "/console",
    "/admin.php", "/xmlrpc.php", "/wp-content",
}

# Temporarily banned IPs (IP → expiry timestamp)
_banned_ips: dict[str, float] = {}
_BAN_DURATION = 3600  # 1 hour


class BotDetectionMiddleware(BaseHTTPMiddleware):
    """Block known scanners and honeypot-triggered IPs.

    Three layers:
    1. Check if IP is temporarily banned (from honeypot hit)
    2. Block requests from known scanner User-Agents
    3. Ban IPs that probe honeypot paths (/wp-admin, /.env, etc.)
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        client_ip = get_client_ip(request)
        path = request.url.path.rstrip("/")
        now = _time.monotonic()

        # 1. Check if IP is banned
        ban_expiry = _banned_ips.get(client_ip)
        if ban_expiry and now < ban_expiry:
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})
        elif ban_expiry:
            _banned_ips.pop(client_ip, None)  # expired

        # 2. Block known scanner User-Agents
        ua = (request.headers.get("user-agent") or "").lower()
        if any(bot in ua for bot in _BLOCKED_USER_AGENTS):
            logger.info("Blocked scanner UA from %s: %s", client_ip, ua[:80])
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})

        # 3. Honeypot — ban IP on probe
        if path.lower() in _HONEYPOT_PATHS:
            logger.warning("Honeypot triggered by %s: %s", client_ip, path)
            _banned_ips[client_ip] = now + _BAN_DURATION
            # Evict expired bans to prevent memory growth
            if len(_banned_ips) > 1000:
                expired = [ip for ip, exp in _banned_ips.items() if now >= exp]
                for ip in expired:
                    del _banned_ips[ip]
            return JSONResponse(status_code=404, content={"detail": "Not found"})

        return await call_next(request)


# ---------------------------------------------------------------------------
# Body size limit — reject oversized requests before parsing
# ---------------------------------------------------------------------------

class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests with Content-Length exceeding MAX_BODY_BYTES.

    FastAPI reads and parses the full request body before Pydantic
    validation. Without this, an attacker could send a 100MB JSON body
    that gets loaded into memory before the 10,000-char message limit
    rejects it. This middleware short-circuits before any parsing.

    Exemptions: endpoints that legitimately accept larger payloads
    (e.g., admin eval report uploads) are listed in _EXEMPT_PATHS and
    enforce their own size caps locally. Those endpoints also require
    admin auth via require_admin_key, so they're not open to abuse.
    """

    MAX_BODY_BYTES = 50_000  # 50KB — generous for a chat message

    # Paths whose handlers enforce their own size limits. The middleware
    # skips the 50KB cap for these but does NOT bypass other checks.
    _EXEMPT_PATHS = frozenset({
        "/admin/api/eval/upload",  # enforces 10MB locally; admin-authed
    })

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        if request.url.path.rstrip("/") in self._EXEMPT_PATHS:
            return await call_next(request)

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.MAX_BODY_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "Request body too large"},
                    )
            except ValueError:
                pass  # malformed header — let downstream handle it
        return await call_next(request)


# ---------------------------------------------------------------------------
# CORS / CSRF — shared origin list
# ---------------------------------------------------------------------------

_DEFAULT_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"


def get_allowed_origins() -> list[str]:
    """Return the list of allowed CORS origins.

    Used by both CORSMiddleware (in main.py) and CSRFMiddleware (below).
    Single source of truth — avoids the two lists drifting apart.
    """
    return [
        o.strip()
        for o in os.getenv("CORS_ALLOWED_ORIGINS", _DEFAULT_ORIGINS).split(",")
        if o.strip()
    ]


class CSRFMiddleware(BaseHTTPMiddleware):
    """Reject cross-origin state-changing requests from browsers.

    Checks the ``Origin`` header (or ``Referer``) on POST/PUT/DELETE
    requests against the allowed CORS origins.  Non-browser clients
    (curl, Postman) that send no ``Origin``/``Referer``/``Sec-Fetch-Site``
    headers are allowed through.
    """

    _SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        if request.method in self._SAFE_METHODS:
            return await call_next(request)

        origin = request.headers.get("origin")
        referer = request.headers.get("referer")
        sec_fetch = request.headers.get("sec-fetch-site")

        # Non-browser clients (no Origin, no Referer, no Sec-Fetch-Site) → allow
        if not origin and not referer and not sec_fetch:
            return await call_next(request)

        allowed = get_allowed_origins()

        # Check Origin header
        if origin:
            if origin in allowed:
                return await call_next(request)
            return JSONResponse(
                status_code=403,
                content={"detail": "Cross-origin request rejected"},
            )

        # Fallback: check Referer (extract origin portion)
        if referer:
            parsed = urlparse(referer)
            ref_origin = f"{parsed.scheme}://{parsed.netloc}"
            if ref_origin in allowed:
                return await call_next(request)
            return JSONResponse(
                status_code=403,
                content={"detail": "Cross-origin request rejected"},
            )

        # Browser sent Sec-Fetch-Site but no Origin/Referer — reject
        return JSONResponse(
            status_code=403,
            content={"detail": "Cross-origin request rejected"},
        )
