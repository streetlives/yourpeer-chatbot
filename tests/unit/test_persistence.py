"""
Tests for SQLite pilot persistence layer.

Covers:
    - persistence.py: event CRUD, session CRUD, eval results, disabled mode
    - audit_log.py: hydration from SQLite, clear propagation
    - session_store.py: hydration, clear propagation, eviction propagation
    - Round-trip: write events → clear in-memory → hydrate → verify

Run with: python -m pytest tests/test_persistence.py -v
"""

import json
import os
import tempfile
import time

import pytest

from app.services import persistence
from app.services.audit_log import (
    clear_audit_log, log_conversation_turn, log_query_execution,
    log_feedback, get_stats, get_recent_events, hydrate_from_db as hydrate_audit,
)
from app.services.session_store import (
    clear_session, save_session_slots, get_session_slots,
    session_exists, hydrate_from_db as hydrate_sessions,
    _SESSION_STATE, _lock,
)


# ---------------------------------------------------------------------------
# FIXTURES
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def sqlite_db(tmp_path):
    """Create a temporary SQLite DB for each test."""
    db_path = str(tmp_path / "test_pilot.db")
    # Patch the module-level config
    original_path = persistence.PILOT_DB_PATH
    original_conn = persistence._conn

    persistence.PILOT_DB_PATH = db_path
    persistence._conn = None  # Force re-init

    yield db_path

    # Cleanup
    persistence.close()
    persistence.PILOT_DB_PATH = original_path
    persistence._conn = original_conn

    # Also clear in-memory stores
    clear_audit_log()
    with _lock:
        _SESSION_STATE.clear()


# ---------------------------------------------------------------------------
# PERSISTENCE MODULE — DIRECT TESTS
# ---------------------------------------------------------------------------

class TestPersistenceEnabled:
    def test_is_enabled(self):
        assert persistence.is_enabled()

    def test_persist_and_load_event(self):
        event = {"type": "conversation_turn", "timestamp": "2026-04-10T10:00:00",
                 "session_id": "s1", "user_message": "hi"}
        persistence.persist_event(event)
        events = persistence.load_all_events()
        assert len(events) == 1
        assert events[0]["session_id"] == "s1"

    def test_load_events_order(self):
        """Events should load in chronological order (oldest first)."""
        for i in range(5):
            persistence.persist_event({
                "type": "conversation_turn",
                "timestamp": f"2026-04-10T10:0{i}:00",
                "session_id": f"s{i}",
            })
        events = persistence.load_all_events()
        assert len(events) == 5
        assert events[0]["session_id"] == "s0"
        assert events[4]["session_id"] == "s4"

    def test_load_events_max_limit(self):
        for i in range(10):
            persistence.persist_event({
                "type": "conversation_turn", "timestamp": "t", "session_id": f"s{i}",
            })
        events = persistence.load_all_events(max_events=3)
        assert len(events) == 3
        # Should get the 3 most recent
        assert events[2]["session_id"] == "s9"

    def test_clear_events(self):
        persistence.persist_event({"type": "test", "timestamp": "t", "session_id": ""})
        persistence.clear_events()
        assert persistence.load_all_events() == []

    def test_persist_and_load_session(self):
        persistence.persist_session("sess1", {"food": "yes"}, 1000.0)
        sessions = persistence.load_all_sessions()
        assert "sess1" in sessions
        slots, _ = sessions["sess1"]
        assert slots["food"] == "yes"

    def test_delete_session(self):
        persistence.persist_session("sess1", {}, 1000.0)
        persistence.delete_session("sess1")
        assert "sess1" not in persistence.load_all_sessions()

    def test_clear_sessions(self):
        persistence.persist_session("s1", {}, 1000.0)
        persistence.persist_session("s2", {}, 1000.0)
        persistence.clear_sessions()
        assert persistence.load_all_sessions() == {}

    def test_session_upsert(self):
        """Saving the same session twice should update, not duplicate."""
        persistence.persist_session("sess1", {"v": 1}, 1000.0)
        persistence.persist_session("sess1", {"v": 2}, 2000.0)
        sessions = persistence.load_all_sessions()
        assert len(sessions) == 1
        assert sessions["sess1"][0]["v"] == 2

    def test_persist_and_load_eval_results(self):
        data = {"overall_average": 4.2, "dimensions": {}}
        persistence.persist_eval_results(data)
        loaded = persistence.load_eval_results()
        assert loaded["overall_average"] == 4.2

    def test_eval_results_none_when_empty(self):
        assert persistence.load_eval_results() is None

    def test_eval_results_upsert(self):
        persistence.persist_eval_results({"v": 1})
        persistence.persist_eval_results({"v": 2})
        assert persistence.load_eval_results()["v"] == 2


class TestPersistenceDisabled:
    """When PILOT_DB_PATH is unset, all operations are no-ops."""

    @pytest.fixture(autouse=True)
    def disable_persistence(self):
        original = persistence.PILOT_DB_PATH
        persistence.PILOT_DB_PATH = None
        persistence._conn = None
        yield
        persistence.PILOT_DB_PATH = original

    def test_is_not_enabled(self):
        assert not persistence.is_enabled()

    def test_persist_event_noop(self):
        """persist_event is a no-op when persistence is disabled.

        Contract: does not raise and does not alter load_all_events()
        output. Verifies both explicitly — a bare "should not raise"
        comment wouldn't survive a future refactor that accidentally
        makes this a real side effect.
        """
        before = persistence.load_all_events()
        persistence.persist_event({"type": "test"})
        after = persistence.load_all_events()
        assert after == before, "persist_event altered state in noop mode"

    def test_load_events_empty(self):
        assert persistence.load_all_events() == []

    def test_persist_session_noop(self):
        """persist_session is a no-op when persistence is disabled.

        Same contract as persist_event_noop: does not raise, does not
        alter load_all_sessions() output.
        """
        before = persistence.load_all_sessions()
        persistence.persist_session("s1", {}, 0)
        after = persistence.load_all_sessions()
        assert after == before, "persist_session altered state in noop mode"

    def test_load_sessions_empty(self):
        assert persistence.load_all_sessions() == {}

    def test_load_eval_none(self):
        assert persistence.load_eval_results() is None


# ---------------------------------------------------------------------------
# AUDIT LOG — ROUND-TRIP HYDRATION
# ---------------------------------------------------------------------------

class TestAuditLogHydration:
    """Write events → clear in-memory → hydrate → verify."""

    def test_hydrate_restores_events(self):
        clear_audit_log()
        log_conversation_turn("s1", "hi", "hello", {}, "greeting")
        log_query_execution("s1", "FoodQuery", {}, 3, False, 40)
        log_feedback(session_id="s1", rating="up")

        # Clear in-memory only (not SQLite)
        from app.services.audit_log import _events, _conversations, _query_log, _lock as al_lock
        with al_lock:
            _events.clear()
            _conversations.clear()
            _query_log.clear()

        # Hydrate from SQLite
        count = hydrate_audit()
        assert count == 3

        # Verify stats work
        stats = get_stats()
        assert stats["total_turns"] == 1
        assert stats["total_queries"] == 1
        assert stats["feedback_up"] == 1

    def test_hydrate_restores_query_log(self):
        clear_audit_log()
        log_query_execution("s1", "FoodQuery", {"city": "Brooklyn"}, 5, False, 30)

        from app.services.audit_log import _events, _query_log, _conversations, _lock as al_lock
        with al_lock:
            _events.clear()
            _query_log.clear()
            _conversations.clear()

        hydrate_audit()
        events = get_recent_events(limit=10)
        query_events = [e for e in events if e["type"] == "query_execution"]
        assert len(query_events) == 1
        assert query_events[0]["template_name"] == "FoodQuery"

    def test_clear_audit_log_clears_sqlite(self):
        log_conversation_turn("s1", "hi", "hello", {}, "greeting")
        clear_audit_log()
        assert persistence.load_all_events() == []

    def test_hydrate_when_disabled(self):
        original = persistence.PILOT_DB_PATH
        persistence.PILOT_DB_PATH = None
        persistence._conn = None
        count = hydrate_audit()
        assert count == 0
        persistence.PILOT_DB_PATH = original


# ---------------------------------------------------------------------------
# SESSION STORE — ROUND-TRIP HYDRATION
# ---------------------------------------------------------------------------

class TestSessionStoreHydration:
    """Write sessions → clear in-memory → hydrate → verify."""

    def test_hydrate_restores_sessions(self):
        save_session_slots("sess-a", {"service_type": "food", "location": "Brooklyn"})
        save_session_slots("sess-b", {"service_type": "shelter"})

        # Clear in-memory only
        with _lock:
            _SESSION_STATE.clear()

        count = hydrate_sessions()
        assert count == 2
        assert get_session_slots("sess-a")["service_type"] == "food"
        assert get_session_slots("sess-b")["service_type"] == "shelter"

    def test_clear_session_removes_from_sqlite(self):
        save_session_slots("sess-x", {"test": True})
        clear_session("sess-x")
        assert "sess-x" not in persistence.load_all_sessions()

    def test_save_updates_sqlite(self):
        save_session_slots("sess-u", {"v": 1})
        save_session_slots("sess-u", {"v": 2})
        sessions = persistence.load_all_sessions()
        assert sessions["sess-u"][0]["v"] == 2

    def test_hydrate_when_disabled(self):
        original = persistence.PILOT_DB_PATH
        persistence.PILOT_DB_PATH = None
        persistence._conn = None
        count = hydrate_sessions()
        assert count == 0
        persistence.PILOT_DB_PATH = original


# ---------------------------------------------------------------------------
# INTEGRATION — FULL CYCLE
# ---------------------------------------------------------------------------

class TestFullCycle:
    """Simulate a server restart: write data, destroy in-memory, reload."""

    def test_full_restart_simulation(self):
        # Phase 1: User interaction
        clear_audit_log()
        with _lock:
            _SESSION_STATE.clear()

        save_session_slots("user-1", {"service_type": "food", "location": "Manhattan"})
        log_conversation_turn("user-1", "food in manhattan", "searching", {"service_type": "food"}, "service")
        log_conversation_turn("user-1", "yes", "results", {}, "confirm_yes")
        log_query_execution("user-1", "FoodQuery", {"city": "Manhattan"}, 5, False, 42)
        log_feedback(session_id="user-1", rating="up", comment="helpful!")

        # Phase 2: "Server restart" — clear in-memory
        from app.services.audit_log import _events, _conversations, _query_log, _lock as al_lock
        with al_lock:
            _events.clear()
            _conversations.clear()
            _query_log.clear()
        with _lock:
            _SESSION_STATE.clear()

        # Verify in-memory is empty
        assert get_stats()["total_events"] == 0
        assert not session_exists("user-1")

        # Phase 3: Hydrate
        hydrate_audit()
        hydrate_sessions()

        # Phase 4: Verify everything is back
        stats = get_stats()
        assert stats["total_turns"] == 2
        assert stats["total_queries"] == 1
        assert stats["feedback_up"] == 1
        assert stats["unique_sessions"] >= 1

        slots = get_session_slots("user-1")
        assert slots["service_type"] == "food"
        assert slots["location"] == "Manhattan"
