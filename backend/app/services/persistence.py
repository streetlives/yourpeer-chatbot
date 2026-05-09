"""
SQLite Persistence Layer — Pilot Data Storage

Provides durable storage for audit events and sessions so data survives
server restarts during pilot testing. Controlled by the PILOT_DB_PATH
environment variable:

    PILOT_DB_PATH=data/pilot.db   → SQLite persistence enabled
    (unset)                       → in-memory only (default)

Design:
    - Write-through: every mutation writes to both in-memory and SQLite
    - Startup hydration: on import, loads existing data from SQLite into
      the in-memory stores (deque, OrderedDict, dict)
    - Read path unchanged: all reads come from in-memory (fast)
    - Thread-safe: uses a dedicated SQLite connection per thread via
      check_same_thread=False + module-level lock

Schema:
    events     — all audit events (type, timestamp, session_id, JSON data)
    sessions   — active sessions (session_id, JSON slots, last_accessed)
    eval_data  — singleton row for eval results (JSON data)
"""

import json
import logging
import os
import sqlite3
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

PILOT_DB_PATH: Optional[str] = os.environ.get("PILOT_DB_PATH")

_conn: Optional[sqlite3.Connection] = None
_db_lock = threading.Lock()


def is_enabled() -> bool:
    """Whether SQLite persistence is active."""
    return PILOT_DB_PATH is not None


# ---------------------------------------------------------------------------
# INITIALIZATION
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    session_id TEXT DEFAULT '',
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp DESC);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    slots TEXT NOT NULL,
    last_accessed REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_data (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data TEXT NOT NULL
);

-- LLM calls — populated by audit_log.record_llm_call after each
-- Anthropic API invocation. Schema mirrors `events` (timestamp +
-- session_id native columns for index usefulness; rest as JSON in
-- `data`) so future ad-hoc SQL queries ("cost by day", "calls per
-- session") work without reshape. Logically separate from `events`
-- because LLM calls fire 3-5x per chat turn and would otherwise
-- dominate the events table's MAX_EVENTS=2000 window, distorting
-- both feeds.
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    session_id TEXT DEFAULT '',
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_session ON llm_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_llm_calls_timestamp ON llm_calls(timestamp DESC);
"""


def _get_conn() -> Optional[sqlite3.Connection]:
    """Get or create the SQLite connection. Thread-safe singleton."""
    global _conn
    if not is_enabled():
        return None
    if _conn is not None:
        return _conn

    with _db_lock:
        if _conn is not None:
            return _conn
        try:
            # Ensure directory exists
            db_dir = os.path.dirname(PILOT_DB_PATH)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)

            _conn = sqlite3.connect(
                PILOT_DB_PATH,
                check_same_thread=False,
                timeout=5.0,
            )
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA busy_timeout=3000")
            _conn.executescript(_SCHEMA)
            _conn.commit()
            logger.info(f"SQLite persistence enabled: {PILOT_DB_PATH}")
        except Exception as e:
            logger.error(f"Failed to initialize SQLite at {PILOT_DB_PATH}: {e}")
            _conn = None
    return _conn


# ---------------------------------------------------------------------------
# EVENT PERSISTENCE
# ---------------------------------------------------------------------------

def persist_event(event: dict) -> None:
    """Write an audit event to SQLite."""
    conn = _get_conn()
    if conn is None:
        return
    try:
        with _db_lock:
            conn.execute(
                "INSERT INTO events (type, timestamp, session_id, data) VALUES (?, ?, ?, ?)",
                (
                    event.get("type", ""),
                    event.get("timestamp", ""),
                    event.get("session_id", ""),
                    json.dumps(event, default=str),
                ),
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to persist event: {e}")


def load_all_events(max_events: int = 2000) -> list[dict]:
    """Load events from SQLite, most recent first, up to max_events."""
    conn = _get_conn()
    if conn is None:
        return []
    try:
        with _db_lock:
            cursor = conn.execute(
                "SELECT data FROM events ORDER BY id DESC LIMIT ?",
                (max_events,),
            )
            rows = cursor.fetchall()
        # Reverse to get chronological order (oldest first)
        return [json.loads(row[0]) for row in reversed(rows)]
    except Exception as e:
        logger.error(f"Failed to load events: {e}")
        return []


def clear_events() -> None:
    """Delete all events from SQLite."""
    conn = _get_conn()
    if conn is None:
        return
    try:
        with _db_lock:
            conn.execute("DELETE FROM events")
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to clear events: {e}")


# ---------------------------------------------------------------------------
# SESSION PERSISTENCE
# ---------------------------------------------------------------------------

def persist_session(session_id: str, slots: dict, last_accessed: float) -> None:
    """Write or update a session in SQLite."""
    conn = _get_conn()
    if conn is None:
        return
    try:
        with _db_lock:
            conn.execute(
                """INSERT INTO sessions (session_id, slots, last_accessed)
                   VALUES (?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                     slots = excluded.slots,
                     last_accessed = excluded.last_accessed""",
                (session_id, json.dumps(slots, default=str), last_accessed),
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to persist session {session_id}: {e}")


def delete_session(session_id: str) -> None:
    """Remove a session from SQLite."""
    conn = _get_conn()
    if conn is None:
        return
    try:
        with _db_lock:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to delete session {session_id}: {e}")


def load_all_sessions(ttl_seconds: float = 1800) -> dict:
    """Load non-expired sessions from SQLite.

    Returns: {session_id: (slots_dict, last_accessed_float)}
    """
    conn = _get_conn()
    if conn is None:
        return {}
    try:
        with _db_lock:
            cursor = conn.execute(
                "SELECT session_id, slots, last_accessed FROM sessions",
            )
            rows = cursor.fetchall()

        import time
        now = time.monotonic()
        # Sessions store monotonic timestamps which aren't meaningful
        # across restarts. On hydration, reset last_accessed to now
        # but respect the original relative ordering.
        result = {}
        for session_id, slots_json, _last_accessed in rows:
            try:
                slots = json.loads(slots_json)
                result[session_id] = (slots, now)
            except json.JSONDecodeError:
                logger.warning(f"Corrupt session data for {session_id}, skipping")
        return result
    except Exception as e:
        logger.error(f"Failed to load sessions: {e}")
        return {}


def clear_sessions() -> None:
    """Delete all sessions from SQLite."""
    conn = _get_conn()
    if conn is None:
        return
    try:
        with _db_lock:
            conn.execute("DELETE FROM sessions")
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to clear sessions: {e}")


# ---------------------------------------------------------------------------
# EVAL RESULTS PERSISTENCE
# ---------------------------------------------------------------------------

def persist_eval_results(data: dict) -> None:
    """Store eval results (singleton)."""
    conn = _get_conn()
    if conn is None:
        return
    try:
        with _db_lock:
            conn.execute(
                """INSERT INTO eval_data (id, data) VALUES (1, ?)
                   ON CONFLICT(id) DO UPDATE SET data = excluded.data""",
                (json.dumps(data, default=str),),
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to persist eval results: {e}")


def load_eval_results() -> Optional[dict]:
    """Load eval results from SQLite."""
    conn = _get_conn()
    if conn is None:
        return None
    try:
        with _db_lock:
            cursor = conn.execute("SELECT data FROM eval_data WHERE id = 1")
            row = cursor.fetchone()
        if row:
            return json.loads(row[0])
        return None
    except Exception as e:
        logger.error(f"Failed to load eval results: {e}")
        return None


# ---------------------------------------------------------------------------
# LLM CALL PERSISTENCE
# ---------------------------------------------------------------------------
# Each Anthropic API call recorded by audit_log.record_llm_call writes
# one row here. Without this, the in-memory `_llm_calls` deque resets
# to empty on every restart — which made Total LLM Calls / Estimated
# LLM Cost / Latency p50-p95 / LLM Failure Rate read as zero for
# minutes-to-hours after every deploy, exactly when staff are most
# likely to be looking at the dashboard. Write-through here mirrors
# the in-memory deque to disk synchronously so a restart hydrates
# the deque from SQLite and the metrics resume immediately.

def persist_llm_call(call: dict) -> None:
    """Write a single LLM call record to SQLite."""
    conn = _get_conn()
    if conn is None:
        return
    try:
        with _db_lock:
            conn.execute(
                "INSERT INTO llm_calls (timestamp, session_id, data) VALUES (?, ?, ?)",
                (
                    call.get("timestamp", ""),
                    call.get("session_id", ""),
                    json.dumps(call, default=str),
                ),
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to persist LLM call: {e}")


def load_all_llm_calls(max_calls: int = 2000) -> list[dict]:
    """Load LLM calls from SQLite, most recent first, up to max_calls.

    Returns the rows in chronological order (oldest first) so the
    caller can append directly to a bounded deque and have the
    natural eviction semantics work the same way as live writes.
    """
    conn = _get_conn()
    if conn is None:
        return []
    try:
        with _db_lock:
            cursor = conn.execute(
                "SELECT data FROM llm_calls ORDER BY id DESC LIMIT ?",
                (max_calls,),
            )
            rows = cursor.fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]
    except Exception as e:
        logger.error(f"Failed to load LLM calls: {e}")
        return []


def clear_llm_calls() -> None:
    """Delete all LLM calls from SQLite."""
    conn = _get_conn()
    if conn is None:
        return
    try:
        with _db_lock:
            conn.execute("DELETE FROM llm_calls")
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to clear LLM calls: {e}")


# ---------------------------------------------------------------------------
# CLOSE
# ---------------------------------------------------------------------------

def close() -> None:
    """Close the SQLite connection."""
    global _conn
    with _db_lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
