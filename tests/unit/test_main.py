"""
Tests for main.py — FastAPI app configuration (headless API mode).

The backend is a headless API. All HTML is served by Next.js.

Covers:
    health     — GET /api/health
    root       — GET / → JSON message
    CORS       — headers present on responses + preflight
    CSRF       — Origin validation on state-changing requests

Run with: python -m pytest tests/test_main.py -v
Or just:  python tests/test_main.py
"""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

# Default allowed origin for CORS (matches _DEFAULT_ORIGINS in dependencies.py)
_ALLOWED_ORIGIN = "http://localhost:3000"


# -----------------------------------------------------------------------
# HEALTH
# -----------------------------------------------------------------------

def test_health():
    """GET /api/health should return structured health check response."""
    from unittest.mock import patch
    with patch("app.rag.query_executor.test_connection", return_value=True):
        r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] in ("healthy", "degraded")
    assert "checks" in data
    assert data["checks"]["database"]["status"] == "up"
    assert "uptime_seconds" in data


# -----------------------------------------------------------------------
# ROOT — JSON message (no static file serving)
# -----------------------------------------------------------------------

def test_root_returns_json():
    """GET / should return a JSON message indicating the API is running."""
    r = client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert "message" in data
    assert "running" in data["message"].lower()


# -----------------------------------------------------------------------
# API routes are reachable
# -----------------------------------------------------------------------

def test_api_health_routed():
    """/api/health should be handled by the API router."""
    from unittest.mock import patch
    with patch("app.rag.query_executor.test_connection", return_value=True):
        r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] in ("healthy", "degraded")
    assert "checks" in data


def test_chat_route_exists():
    """POST /chat/ should be handled by the chat router (not 404)."""
    # Will fail validation without a body, but should not be a 404
    r = client.post("/chat/", json={})
    assert r.status_code != 404


def test_admin_api_stats_routed():
    """GET /admin/api/stats should be handled by the admin router.

    When ADMIN_API_KEY is set in the env, `require_admin_key`
    rejects unauthenticated requests with 401 before the route
    runs. We include the Bearer header when the var is set so this
    test exercises routing, not auth. When the var is unset (local
    dev) the header is omitted and behavior is unchanged.
    """
    import os
    key = os.environ.get("ADMIN_API_KEY")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    r = client.get("/admin/api/stats", headers=headers)
    assert r.status_code == 200


# -----------------------------------------------------------------------
# CSRF PROTECTION
# -----------------------------------------------------------------------

def test_csrf_allows_post_with_valid_origin():
    """POST with a valid Origin header should be allowed through CSRF check."""
    r = client.post(
        "/chat/", json={"message": "hi"},
        headers={"Origin": _ALLOWED_ORIGIN},
    )
    # Should not be 403 (CSRF). May be 422 or 200 depending on validation.
    assert r.status_code != 403


def test_csrf_rejects_post_with_evil_origin():
    """POST with a disallowed Origin header should be rejected."""
    r = client.post(
        "/chat/", json={"message": "hi"},
        headers={"Origin": "https://evil.com"},
    )
    assert r.status_code == 403
    assert "Cross-origin" in r.json()["detail"]


def test_csrf_allows_post_without_browser_headers():
    """POST without Origin/Referer/Sec-Fetch-Site (non-browser) should be allowed."""
    r = client.post("/chat/", json={"message": "hi"})
    assert r.status_code != 403


def test_csrf_rejects_post_with_sec_fetch_but_no_origin():
    """POST with Sec-Fetch-Site but no Origin should be rejected (browser without origin)."""
    r = client.post(
        "/chat/", json={"message": "hi"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert r.status_code == 403


def test_csrf_allows_post_with_valid_referer():
    """POST with a valid Referer (no Origin) should be allowed via fallback."""
    r = client.post(
        "/chat/", json={"message": "hi"},
        headers={"Referer": f"{_ALLOWED_ORIGIN}/chat"},
    )
    assert r.status_code != 403


def test_csrf_rejects_post_with_evil_referer():
    """POST with a disallowed Referer (no Origin) should be rejected."""
    r = client.post(
        "/chat/", json={"message": "hi"},
        headers={"Referer": "https://evil.com/steal-data"},
    )
    assert r.status_code == 403
    assert "Cross-origin" in r.json()["detail"]


# -----------------------------------------------------------------------
# CORS HEADERS
# -----------------------------------------------------------------------

def test_cors_headers_present():
    """Responses should include CORS allow-origin header for allowed origins."""
    r = client.get("/", headers={"Origin": _ALLOWED_ORIGIN})
    assert r.status_code == 200
    origin = r.headers.get("access-control-allow-origin")
    assert origin == _ALLOWED_ORIGIN, f"Expected {_ALLOWED_ORIGIN}, got: {origin}"


def test_cors_rejects_unknown_origin():
    """Responses should NOT include CORS allow-origin for disallowed origins."""
    r = client.get("/", headers={"Origin": "https://evil.com"})
    assert r.status_code == 200
    origin = r.headers.get("access-control-allow-origin")
    assert origin is None, f"Expected no CORS header for evil origin, got: {origin}"


def test_cors_preflight():
    """OPTIONS preflight should return CORS headers for allowed origins."""
    r = client.options(
        "/chat/",
        headers={
            "Origin": _ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert r.status_code == 200
    origin = r.headers.get("access-control-allow-origin")
    assert origin == _ALLOWED_ORIGIN, f"Expected {_ALLOWED_ORIGIN}, got: {origin}"
    assert "POST" in r.headers.get("access-control-allow-methods", "")
