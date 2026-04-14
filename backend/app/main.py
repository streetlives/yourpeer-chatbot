from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.routes.chat import router as chat_router
from app.routes.admin import router as admin_router
from app.dependencies import (
    RateLimitMiddleware, CSRFMiddleware, BodySizeLimitMiddleware,
    BotDetectionMiddleware, get_allowed_origins,
)
import hmac
import logging
import os
import time

logger = logging.getLogger(__name__)

# Track process start time for uptime calculation.
_start_time = time.time()

# Render sets RENDER=true automatically. Use this to detect production
# and enforce security requirements that are optional in local dev.
_IS_PRODUCTION = bool(os.getenv("RENDER"))


def _validate_production_env():
    """Verify required secrets are set when running in production.

    In local dev (no RENDER env var), missing secrets trigger warnings.
    In production, missing secrets fail the startup — silent fallback to
    open access is too dangerous for an app serving vulnerable populations.
    """
    issues = []

    if not os.environ.get("SESSION_SECRET"):
        msg = "SESSION_SECRET is not set — session tokens will not be signed."
        if _IS_PRODUCTION:
            issues.append(msg)
        else:
            logger.warning(f"{msg} This is fine for local dev.")

    if not os.environ.get("ADMIN_API_KEY"):
        msg = "ADMIN_API_KEY is not set — admin panel is publicly accessible."
        if _IS_PRODUCTION:
            issues.append(msg)
        else:
            logger.warning(f"{msg} This is fine for local dev.")

    if not os.environ.get("DATABASE_URL"):
        msg = "DATABASE_URL is not set — no search results will be returned."
        if _IS_PRODUCTION:
            issues.append(msg)
        else:
            logger.warning(msg)

    if issues:
        for issue in issues:
            logger.critical(f"PRODUCTION SECURITY: {issue}")
        raise RuntimeError(
            f"Missing required environment variables in production: "
            f"{'; '.join(issues)}"
        )


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup/shutdown lifecycle. Hydrates persisted data on boot."""

    # Validate production environment before anything else
    _validate_production_env()

    from app.services import persistence
    if persistence.is_enabled():
        from app.services.audit_log import hydrate_from_db as hydrate_audit
        from app.services.session_store import hydrate_from_db as hydrate_sessions
        events = hydrate_audit()
        sessions = hydrate_sessions()
        logger.info(f"Startup hydration: {events} events, {sessions} sessions from SQLite")

    # Pre-warm the semantic router (Tier 2) so it's ready for the
    # first user query. Downloads the model (~80 MB) on first run.
    # Non-blocking: if sentence-transformers isn't installed or the
    # download fails, the router stays disabled and Tiers 1+3 handle
    # all routing.
    try:
        from app.services.semantic_router import initialize as sr_init
        ok = sr_init()
        if ok:
            logger.info("Semantic router (Tier 2): ready")
        else:
            logger.warning(
                "Semantic router (Tier 2): not available. "
                "Install with: pip install sentence-transformers"
            )
    except Exception as e:
        logger.warning(f"Semantic router init failed: {e}")

    yield
    # Shutdown: close DB connection pool and SQLite persistence
    try:
        from app.rag.query_executor import dispose_engine
        dispose_engine()
        logger.info("PostgreSQL connection pool disposed")
    except Exception as e:
        logger.warning(f"Engine disposal failed: {e}")
    from app.services import persistence as p
    p.close()


app = FastAPI(
    title="YourPeer Chatbot API",
    description="A chatbot for the YourPeer network by Streetlives.",
    lifespan=lifespan,
)

# --- CORS ---
# Allowed origins are read from the CORS_ALLOWED_ORIGINS env var
# (comma-separated). Defaults to localhost for local dev.
# In production, set to the actual frontend domain(s).
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# --- CSRF protection ---
# Validates Origin header on POST/PUT/DELETE to prevent cross-site forgery.
app.add_middleware(CSRFMiddleware)

# --- Rate limiting ---
# Protects /chat/ and /chat/feedback. Admin and health routes are exempt.
app.add_middleware(RateLimitMiddleware)

# --- Body size limit ---
# Rejects oversized request bodies before parsing (50KB cap).
app.add_middleware(BodySizeLimitMiddleware)

# --- Bot detection ---
# Blocks known scanner User-Agents and bans IPs that probe honeypot paths.
app.add_middleware(BotDetectionMiddleware)

# --- API routes ---
app.include_router(chat_router)
app.include_router(admin_router)


@app.get("/api/health")
def health(request: Request):
    """Readiness check — verifies each dependency independently.

    Returns 200 when the database is reachable (required for search results).
    Returns 503 when the database is unreachable.

    Detailed diagnostics (latency, model info, uptime) are only included
    when the admin API key is provided via Authorization header. Without
    auth, returns only the status — enough for Render's readiness probe
    without exposing internal architecture details.
    """
    from datetime import datetime, timezone

    # Check if admin auth is provided for detailed view
    _admin_key = os.environ.get("ADMIN_API_KEY")
    _auth = request.headers.get("authorization", "")
    _is_admin = (
        not _admin_key  # dev mode — show everything
        or hmac.compare_digest(_auth, f"Bearer {_admin_key}")
    )

    checks: dict = {}
    overall = "healthy"

    # --- Database (critical — without it, no search results) ---
    try:
        t0 = time.perf_counter()
        from app.rag.query_executor import test_connection
        db_ok = test_connection()
        db_ms = round((time.perf_counter() - t0) * 1000, 1)
        if db_ok:
            checks["database"] = {"status": "up"}
            if _is_admin:
                checks["database"]["latency_ms"] = db_ms
        else:
            checks["database"] = {"status": "down"}
            overall = "unhealthy"
    except Exception as e:
        checks["database"] = {"status": "down"}
        if _is_admin:
            checks["database"]["error"] = str(e)[:120]
        overall = "unhealthy"

    # --- LLM / Anthropic API (non-critical — regex-only fallback) ---
    _use_llm = bool(os.getenv("ANTHROPIC_API_KEY"))
    if _use_llm:
        checks["llm"] = {"status": "up"}
    else:
        checks["llm"] = {"status": "unavailable"}
        if overall == "healthy":
            overall = "degraded"

    # --- Semantic router (informational — optional Tier 2 enhancement) ---
    try:
        from app.services.semantic_router import get_status as _sr_status
        sr = _sr_status()
        if sr["available"]:
            checks["semantic_router"] = {"status": "up"}
            if _is_admin:
                checks["semantic_router"]["model"] = sr["model"]
                checks["semantic_router"]["route_count"] = sr["route_count"]
        else:
            checks["semantic_router"] = {"status": "not_loaded", "required": False}
    except Exception:
        checks["semantic_router"] = {"status": "not_loaded", "required": False}

    payload = {
        "status": overall,
        "uptime_seconds": round(time.time() - _start_time),
        "checks": checks,
    }
    if _is_admin:
        payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        payload["uptime_seconds"] = round(time.time() - _start_time)

    status_code = 503 if overall == "unhealthy" else 200
    return JSONResponse(content=payload, status_code=status_code)


@app.get("/")
def root():
    return {"message": "YourPeer chatbot API is running. Frontend served by Next.js."}
