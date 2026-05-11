"""
Admin API Routes — Staff review console endpoints.

All endpoints are prefixed with /admin/api/.
The admin UI is served by Next.js at /admin/.
"""

import asyncio
import os
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.dependencies import require_admin_key
from app.services.audit_log import (
    get_recent_events,
    get_conversation,
    get_conversations_summary,
    get_query_log,
    get_stats,
    get_eval_results,
    load_eval_results_from_file,
    set_eval_results,
)

logger = logging.getLogger(__name__)

# Tracks whether an eval run is currently in progress
_eval_running = False
_eval_status: dict = {}
_eval_lock = threading.Lock()

# Strong references to background tasks — prevents the GC from collecting
# a running task before it completes.  See:
# https://docs.python.org/3/library/asyncio-task.html#creating-tasks
_background_tasks: set = set()

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_key)],  # S1: all admin routes require auth
)

# Path to the eval/test directory. Configurable via EVAL_DIR env var;
# falls back to inferring from the file's location in the repo tree.
# admin.py is at app/app/routes/admin.py → .parent×4 = repo root → tests/
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_TESTS_DIR = _REPO_ROOT / "tests"
# On Render, the backend runs from a subdirectory — EVAL_DIR lets you
# point to the correct absolute path (e.g. /opt/render/project/src/tests/tests)
TESTS_DIR = Path(os.getenv("EVAL_DIR", str(_DEFAULT_TESTS_DIR)))


# ---------------------------------------------------------------------------
# ADMIN UI REDIRECT
# ---------------------------------------------------------------------------

@router.get("/")
def admin_root():
    """The admin UI is served by Next.js. Point users there."""
    return JSONResponse(
        content={
            "detail": "Admin UI is served by Next.js at http://localhost:3000/admin",
        },
    )


# ---------------------------------------------------------------------------
# DATA API
# ---------------------------------------------------------------------------

def _admin_error(endpoint: str, e: Exception) -> JSONResponse:
    """Return a structured error response for admin API failures."""
    logger.exception(f"Admin API error in {endpoint}")
    return JSONResponse(
        status_code=500,
        content={
            "error": True,
            "endpoint": endpoint,
            "detail": f"{type(e).__name__}: {e}",
        },
    )


@router.get("/api/stats")
def admin_stats():
    """Aggregate statistics for the dashboard overview."""
    try:
        return get_stats()
    except Exception as e:
        return _admin_error("/api/stats", e)


@router.get("/api/conversations")
def admin_conversations(limit: int = Query(50, ge=1, le=200)):
    """List recent conversations with summary info."""
    try:
        return get_conversations_summary(limit=limit)
    except Exception as e:
        return _admin_error("/api/conversations", e)


@router.get("/api/conversations/{session_id}")
def admin_conversation_detail(session_id: str):
    """Get full transcript for a specific conversation."""
    try:
        events = get_conversation(session_id)
        if not events:
            return JSONResponse(
                status_code=404,
                content={"detail": f"No conversation found for session {session_id}"},
            )
        return events
    except Exception as e:
        return _admin_error(f"/api/conversations/{session_id}", e)


@router.get("/api/events")
def admin_events(
    limit: int = Query(100, ge=1, le=500),
    event_type: str = Query(None, pattern="^(conversation_turn|query_execution|crisis_detected|session_reset|feedback|location_feedback)$"),
):
    """Get recent events, optionally filtered by type."""
    try:
        return get_recent_events(limit=limit, event_type=event_type)
    except Exception as e:
        return _admin_error("/api/events", e)


@router.get("/api/queries")
def admin_queries(limit: int = Query(100, ge=1, le=500)):
    """Get recent query execution log."""
    try:
        return get_query_log(limit=limit)
    except Exception as e:
        return _admin_error("/api/queries", e)


@router.get("/api/eval")
def admin_eval():
    """Get LLM-as-judge evaluation results."""
    try:
        results = get_eval_results()
        if results is None:
            # Try loading from the default file location
            eval_path = TESTS_DIR / "eval_report.json"
            if eval_path.exists():
                load_eval_results_from_file(str(eval_path))
                results = get_eval_results()

        if results is None:
            return JSONResponse(
                status_code=200,
                content={"results": None, "detail": "No evaluation results yet. Use the Run Evals button to generate them."},
            )
        return results
    except Exception as e:
        return _admin_error("/api/eval", e)


@router.get("/api/eval/status")
def admin_eval_status():
    """Check whether an eval run is in progress."""
    try:
        with _eval_lock:
            return {"running": _eval_running, **_eval_status}
    except Exception as e:
        return _admin_error("/api/eval/status", e)


@router.post("/api/eval/run")
async def admin_eval_run(
    scenarios: int = Query(None, ge=1, le=200, description="Max scenarios to run (default: all)"),
    category: str = Query(None, pattern="^[a-z_]+$", description="Only run scenarios in this category"),
):
    """Trigger an LLM-as-judge eval run in the background."""
    global _eval_running, _eval_status

    with _eval_lock:
        if _eval_running:
            return JSONResponse(
                status_code=409,
                content={"detail": "An eval run is already in progress."},
            )

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return JSONResponse(
                status_code=500,
                content={"detail": "ANTHROPIC_API_KEY is not set on the server."},
            )

        _eval_running = True
        _eval_status = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "message": "Starting…",
        }

    # S3: run the eval script in a subprocess, not importlib in-process.
    # This isolates the eval from the web server — a compromised eval file
    # cannot affect server memory or state.
    task = asyncio.create_task(
        _run_eval_background(api_key, scenarios, category)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"detail": "Eval started. Poll /admin/api/eval/status for progress."}


@router.post("/api/eval/upload")
async def admin_eval_upload(request: Request):
    """Upload a locally-run eval_report.json and store it for display.

    Accepts the JSON file as the raw request body (Content-Type: application/json).
    Validates that the report has the expected structure before storing.
    """
    try:
        with _eval_lock:
            if _eval_running:
                return JSONResponse(
                    status_code=409,
                    content={"detail": "An eval run is in progress. Wait for it to finish before uploading."},
                )

        body = await request.body()
        if len(body) > 10 * 1024 * 1024:  # 10 MB limit
            return JSONResponse(
                status_code=413,
                content={"detail": "File too large. Maximum size is 10 MB."},
            )

        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            return JSONResponse(
                status_code=400,
                content={"detail": f"Invalid JSON: {str(e)[:100]}"},
            )

        # Validate expected structure
        if not isinstance(data, dict):
            return JSONResponse(
                status_code=400,
                content={"detail": "Expected a JSON object at the top level."},
            )

        summary = data.get("summary")
        if not summary or not isinstance(summary, dict):
            return JSONResponse(
                status_code=400,
                content={"detail": "Missing or invalid 'summary' field. Is this an eval_report.json?"},
            )

        required_summary_fields = ["overall_average", "scenarios_evaluated"]
        missing = [f for f in required_summary_fields if f not in summary]
        if missing:
            return JSONResponse(
                status_code=400,
                content={"detail": f"Summary is missing required fields: {', '.join(missing)}"},
            )

        set_eval_results(data)

        # Also save to disk so it survives restarts
        try:
            eval_path = TESTS_DIR / "eval_report.json"
            eval_path.parent.mkdir(parents=True, exist_ok=True)
            with open(eval_path, "w") as f:
                json.dump(data, f, indent=2)
            logger.info(f"Eval report uploaded and saved to {eval_path}")
        except Exception as e:
            logger.warning(f"Eval report stored in memory but failed to save to disk: {e}")

        scenario_count = summary.get("scenarios_evaluated", "?")
        avg = summary.get("overall_average", "?")
        return {
            "detail": f"Eval report uploaded successfully. {scenario_count} scenarios, {avg} average.",
        }

    except Exception as e:
        return _admin_error("/api/eval/upload", e)


async def _run_eval_background(
    api_key: str,
    max_scenarios: Optional[int],
    category: Optional[str],
) -> None:
    """Run the eval script in a subprocess and store results when done."""
    global _eval_running, _eval_status

    cmd = [
        "python", str(TESTS_DIR / "eval" / "eval_llm_judge.py"),
        "--output", str(TESTS_DIR / "eval_report.json"),
    ]
    if max_scenarios:
        cmd += ["--scenarios", str(max_scenarios)]
    if category:
        cmd += ["--category", category]

    env = {**os.environ, "ANTHROPIC_API_KEY": api_key}

    try:
        with _eval_lock:
            _eval_status["message"] = "Subprocess started…"

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            # Load the report the script wrote to disk
            out_path = TESTS_DIR / "eval_report.json"
            if out_path.exists():
                with open(out_path) as f:
                    report = json.load(f)
                set_eval_results(report)
                total = report.get("summary", {}).get("scenarios_evaluated", "?")
                message = f"Done — {total} scenario(s) evaluated."
            else:
                message = "Subprocess exited cleanly but no report file was written."

            logger.info("Eval completed.\n%s", stdout.decode())

            with _eval_lock:
                _eval_status = {
                    "message": message,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
        else:
            err = stderr.decode()
            logger.error("Eval subprocess failed (exit %s):\n%s", proc.returncode, err)
            with _eval_lock:
                _eval_status = {
                    "message": f"Eval failed (exit {proc.returncode}). Check server logs.",
                }

    except Exception as e:
        logger.exception("Eval subprocess could not be started")
        with _eval_lock:
            _eval_status = {"message": f"Error: {e}"}
    finally:
        with _eval_lock:
            _eval_running = False


# ---------------------------------------------------------------------------
# SUB-ROUTERS
# ---------------------------------------------------------------------------

# Locations admin endpoints — registered under /admin/api/locations/*.
# Imported and included here (rather than in main.py) so the auth
# dependency on the parent /admin router applies transparently.
# Adding a new sub-router for a future admin section follows the
# same pattern: create routes/admin_<thing>.py with its own APIRouter,
# import + include here.
from app.routes.admin_locations import router as locations_router  # noqa: E402
router.include_router(locations_router)
