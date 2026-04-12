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

from fastapi import APIRouter, Depends, Query
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
_DEFAULT_TESTS_DIR = Path(__file__).resolve().parent.parent.parent / "tests"
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

@router.get("/api/stats")
def admin_stats():
    """Aggregate statistics for the dashboard overview."""
    return get_stats()


@router.get("/api/conversations")
def admin_conversations(limit: int = Query(50, ge=1, le=200)):
    """List recent conversations with summary info."""
    return get_conversations_summary(limit=limit)


@router.get("/api/conversations/{session_id}")
def admin_conversation_detail(session_id: str):
    """Get full transcript for a specific conversation."""
    events = get_conversation(session_id)
    if not events:
        return JSONResponse(
            status_code=404,
            content={"detail": f"No conversation found for session {session_id}"},
        )
    return events


@router.get("/api/events")
def admin_events(
    limit: int = Query(100, ge=1, le=500),
    event_type: str = Query(None, pattern="^(conversation_turn|query_execution|crisis_detected|session_reset|feedback|location_feedback)$"),
):
    """Get recent events, optionally filtered by type."""
    return get_recent_events(limit=limit, event_type=event_type)


@router.get("/api/queries")
def admin_queries(limit: int = Query(100, ge=1, le=500)):
    """Get recent query execution log."""
    return get_query_log(limit=limit)


@router.get("/api/eval")
def admin_eval():
    """Get LLM-as-judge evaluation results."""
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


@router.get("/api/eval/status")
def admin_eval_status():
    """Check whether an eval run is in progress."""
    with _eval_lock:
        return {"running": _eval_running, **_eval_status}


@router.post("/api/eval/run")
async def admin_eval_run(
    scenarios: int = Query(None, ge=1, le=30, description="Max scenarios to run (default: all)"),
    category: str = Query(None, description="Only run scenarios in this category"),
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


async def _run_eval_background(
    api_key: str,
    max_scenarios: Optional[int],
    category: Optional[str],
) -> None:
    """Run the eval script in a subprocess and store results when done."""
    global _eval_running, _eval_status

    cmd = [
        "python", "tests/eval_llm_judge.py",
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
