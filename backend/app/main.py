from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.routes.chat import router as chat_router
from app.routes.admin import router as admin_router
from app.dependencies import (
    RateLimitMiddleware, CSRFMiddleware, BodySizeLimitMiddleware,
    BotDetectionMiddleware, get_allowed_origins,
)
import logging
import os
import time

logger = logging.getLogger(__name__)

# Track process start time for uptime calculation.
_start_time = time.time()


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup/shutdown lifecycle. Hydrates persisted data on boot."""
    from app.services import persistence
    if persistence.is_enabled():
        from app.services.audit_log import hydrate_from_db as hydrate_audit
        from app.services.session_store import hydrate_from_db as hydrate_sessions
        events = hydrate_audit()
        sessions = hydrate_sessions()
        logger.info(f"Startup hydration: {events} events, {sessions} sessions from SQLite")
    yield
    # Shutdown: close SQLite connection
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
    allow_methods=["*"],
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
def health():
    """Readiness check — verifies each dependency independently.

    Returns 200 when the database is reachable (required for search results).
    Returns 503 when the database is unreachable.
    LLM and semantic router being unavailable is "degraded" (200) because
    the service still works in regex-only mode.
    """
    from datetime import datetime, timezone

    checks: dict = {}
    overall = "healthy"

    # --- Database (critical — without it, no search results) ---
    try:
        t0 = time.perf_counter()
        from app.rag.query_executor import test_connection
        db_ok = test_connection()
        db_ms = round((time.perf_counter() - t0) * 1000, 1)
        if db_ok:
            checks["database"] = {"status": "up", "latency_ms": db_ms}
        else:
            checks["database"] = {"status": "down", "error": "SELECT 1 failed"}
            overall = "unhealthy"
    except Exception as e:
        checks["database"] = {"status": "down", "error": str(e)[:120]}
        overall = "unhealthy"

    # --- LLM / Anthropic API (non-critical — regex-only fallback) ---
    _use_llm = bool(os.getenv("ANTHROPIC_API_KEY"))
    if _use_llm:
        checks["llm"] = {"status": "up", "mode": "llm"}
    else:
        checks["llm"] = {"status": "unavailable", "mode": "regex_only"}
        if overall == "healthy":
            overall = "degraded"

    # --- Semantic router (non-critical — falls through to LLM or regex) ---
    try:
        from app.services.semantic_router import get_status as _sr_status
        sr = _sr_status()
        if sr["available"]:
            checks["semantic_router"] = {
                "status": "up",
                "model": sr["model"],
                "route_count": sr["route_count"],
            }
        else:
            checks["semantic_router"] = {"status": "not_loaded"}
            if overall == "healthy":
                overall = "degraded"
    except Exception:
        checks["semantic_router"] = {"status": "not_loaded"}
        if overall == "healthy":
            overall = "degraded"

    payload = {
        "status": overall,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": round(time.time() - _start_time),
        "checks": checks,
    }

    status_code = 503 if overall == "unhealthy" else 200
    return JSONResponse(content=payload, status_code=status_code)


@app.get("/")
def root():
    return {"message": "YourPeer chatbot API is running. Frontend served by Next.js."}
