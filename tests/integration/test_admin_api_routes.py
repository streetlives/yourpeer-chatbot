"""
Tests for the admin API routes — HTTP-level endpoint tests using
FastAPI TestClient.

Covers all admin API endpoints:
    GET /admin/api/stats  — aggregate statistics
    GET /admin/api/conversations       — conversation summaries
    GET /admin/api/conversations/{id}  — single conversation detail
    GET /admin/api/events              — recent events (filterable)
    GET /admin/api/queries             — query execution log
    GET /admin/api/eval                — LLM evaluation results

Run with: python -m pytest tests/integration/test_admin_api_routes.py -v
Or just:  python tests/integration/test_admin_api_routes.py
"""

import os
import json
import tempfile
from unittest.mock import patch


from fastapi.testclient import TestClient
from app.main import app
from app.services.audit_log import (
    log_conversation_turn,
    log_query_execution,
    log_crisis_detected,
    log_session_reset,
    set_eval_results,
    clear_audit_log,
)

client = TestClient(app)


# -----------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------

def _seed_data():
    """Populate the audit log with realistic test data."""
    clear_audit_log()

    # Session 1: full service flow
    log_conversation_turn(
        session_id="sess-abc",
        user_message_redacted="I need food in Brooklyn",
        bot_response="I'll search for food in Brooklyn.",
        slots={"service_type": "food", "location": "Brooklyn"},
        category="service",
        services_count=0,
        follow_up_needed=True,
    )
    log_conversation_turn(
        session_id="sess-abc",
        user_message_redacted="Yes, search",
        bot_response="I found 3 options.",
        slots={"service_type": "food", "location": "Brooklyn"},
        category="confirm_yes",
        services_count=3,
    )
    log_query_execution(
        session_id="sess-abc",
        template_name="FoodQuery",
        params={"taxonomy_name": "Food", "city": "Brooklyn", "max_results": 10},
        result_count=3,
        relaxed=False,
        execution_ms=42,
    )

    # Session 2: crisis
    log_conversation_turn(
        session_id="sess-xyz",
        user_message_redacted="I want to [REDACTED]",
        bot_response="I hear you...",
        slots={},
        category="crisis",
    )
    log_crisis_detected(
        session_id="sess-xyz",
        crisis_category="suicide_self_harm",
        user_message_redacted="I want to [REDACTED]",
    )

    # Session 3: reset
    log_session_reset("sess-reset")


# -----------------------------------------------------------------------
# ADMIN API KEY AUTHENTICATION
# -----------------------------------------------------------------------

def test_admin_auth_open_when_no_key_configured():
    """When ADMIN_API_KEY is not set, admin endpoints are open."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ADMIN_API_KEY", None)
        clear_audit_log()
        response = client.get("/admin/api/stats")
        assert response.status_code == 200


def test_admin_auth_rejects_missing_header():
    """When ADMIN_API_KEY is set, requests without auth header get 401."""
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-secret-key"}):
        response = client.get("/admin/api/stats")
        assert response.status_code == 401
        assert "Missing or invalid" in response.json()["detail"]


def test_admin_auth_rejects_wrong_key():
    """When ADMIN_API_KEY is set, requests with wrong key get 401."""
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-secret-key"}):
        response = client.get(
            "/admin/api/stats",
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert response.status_code == 401


def test_admin_auth_accepts_correct_key():
    """When ADMIN_API_KEY is set, requests with correct key succeed."""
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-secret-key"}):
        clear_audit_log()
        response = client.get(
            "/admin/api/stats",
            headers={"Authorization": "Bearer test-secret-key"},
        )
        assert response.status_code == 200


def test_admin_auth_protects_eval_run():
    """POST /admin/api/eval/run should also be protected by the API key."""
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-secret-key"}):
        response = client.post("/admin/api/eval/run")
        assert response.status_code == 401


# -----------------------------------------------------------------------
# GET /admin/api/stats
# -----------------------------------------------------------------------

def test_stats_empty():
    """Stats on empty log should return zeros."""
    clear_audit_log()
    response = client.get("/admin/api/stats")
    assert response.status_code == 200

    data = response.json()
    assert data["total_events"] == 0
    assert data["total_turns"] == 0
    assert data["total_queries"] == 0
    assert data["total_crises"] == 0
    assert data["total_resets"] == 0
    assert data["unique_sessions"] == 0
    assert data["relaxed_query_rate"] == 0


def test_stats_with_data():
    """Stats should reflect seeded data correctly."""
    _seed_data()
    response = client.get("/admin/api/stats")
    assert response.status_code == 200

    data = response.json()
    assert data["total_turns"] == 3
    assert data["total_queries"] == 1
    assert data["total_crises"] == 1
    assert data["total_resets"] == 1
    assert data["unique_sessions"] == 3
    assert "service" in data["category_distribution"]
    assert "food" in data["service_type_distribution"]


# -----------------------------------------------------------------------
# GET /admin/api/conversations
# -----------------------------------------------------------------------

def test_conversations_list():
    """Should return conversation summaries."""
    _seed_data()
    response = client.get("/admin/api/conversations")
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 3  # sess-abc, sess-xyz, sess-reset

    # Find sess-abc
    abc = next((c for c in data if c["session_id"] == "sess-abc"), None)
    assert abc is not None
    assert abc["turn_count"] == 2
    assert abc["services_delivered"] >= 3


def test_conversations_limit():
    """Limit parameter should cap results."""
    _seed_data()
    response = client.get("/admin/api/conversations?limit=1")
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_conversations_limit_validation():
    """Limit below 1 or above 200 should be rejected."""
    response = client.get("/admin/api/conversations?limit=0")
    assert response.status_code == 422  # validation error

    response = client.get("/admin/api/conversations?limit=999")
    assert response.status_code == 422


# -----------------------------------------------------------------------
# GET /admin/api/conversations/{session_id}
# -----------------------------------------------------------------------

def test_conversation_detail():
    """Should return all events for a specific session."""
    _seed_data()
    response = client.get("/admin/api/conversations/sess-abc")
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 3  # 2 turns + 1 query
    assert all(e["session_id"] == "sess-abc" for e in data)


def test_conversation_detail_not_found():
    """Should return 404 for unknown session ID."""
    _seed_data()
    response = client.get("/admin/api/conversations/nonexistent-session")
    assert response.status_code == 404
    assert "No conversation found" in response.json()["detail"]


def test_conversation_detail_crisis_session():
    """Crisis session should include both turn and crisis events."""
    _seed_data()
    response = client.get("/admin/api/conversations/sess-xyz")
    assert response.status_code == 200

    data = response.json()
    types = [e["type"] for e in data]
    assert "conversation_turn" in types
    assert "crisis_detected" in types


# -----------------------------------------------------------------------
# GET /admin/api/events
# -----------------------------------------------------------------------

def test_events_all():
    """Should return all events with default parameters."""
    _seed_data()
    response = client.get("/admin/api/events")
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 6  # 3 turns + 1 query + 1 crisis + 1 reset


def test_events_filter_by_type():
    """Should filter events by type."""
    _seed_data()

    response = client.get("/admin/api/events?event_type=crisis_detected")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["type"] == "crisis_detected"

    response = client.get("/admin/api/events?event_type=session_reset")
    data = response.json()
    assert len(data) == 1
    assert data[0]["type"] == "session_reset"


def test_events_invalid_type_rejected():
    """Invalid event_type should be rejected by the regex validator."""
    response = client.get("/admin/api/events?event_type=invalid_type")
    assert response.status_code == 422


def test_events_limit():
    """Limit parameter should cap results."""
    _seed_data()
    response = client.get("/admin/api/events?limit=2")
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_events_limit_validation():
    """Limit below 1 or above 500 should be rejected."""
    response = client.get("/admin/api/events?limit=0")
    assert response.status_code == 422

    response = client.get("/admin/api/events?limit=999")
    assert response.status_code == 422


# -----------------------------------------------------------------------
# GET /admin/api/queries
# -----------------------------------------------------------------------

def test_queries_list():
    """Should return query execution log."""
    _seed_data()
    response = client.get("/admin/api/queries")
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["template_name"] == "FoodQuery"
    assert data[0]["result_count"] == 3
    assert "max_results" not in data[0]["params"]


def test_queries_limit():
    """Limit parameter should cap results."""
    clear_audit_log()
    for i in range(10):
        log_query_execution("s1", f"Q{i}", {}, i, False, 10)

    response = client.get("/admin/api/queries?limit=3")
    assert response.status_code == 200
    assert len(response.json()) == 3


# -----------------------------------------------------------------------
# GET /admin/api/eval
# -----------------------------------------------------------------------

def test_eval_no_results():
    """Should return 200 with null results when no eval results exist."""
    from unittest.mock import patch
    from pathlib import Path
    clear_audit_log()
    # Patch TESTS_DIR to a non-existent path so eval_report.json isn't loaded
    with patch("app.routes.admin.TESTS_DIR", Path("/nonexistent")):
        response = client.get("/admin/api/eval")
    assert response.status_code == 200
    data = response.json()
    assert data["results"] is None
    assert "No evaluation results" in data["detail"]


def test_eval_with_results():
    """Should return eval results when set."""
    clear_audit_log()
    eval_data = {
        "timestamp": "2025-01-01T00:00:00Z",
        "scenarios_run": 10,
        "average_score": 4.2,
        "results": [{"scenario": "basic_food", "score": 5}],
    }
    set_eval_results(eval_data)

    response = client.get("/admin/api/eval")
    assert response.status_code == 200

    data = response.json()
    assert data["scenarios_run"] == 10
    assert data["average_score"] == 4.2


# -----------------------------------------------------------------------
# POST /admin/api/eval/run — guard clauses
# -----------------------------------------------------------------------

def test_eval_run_rejects_when_no_api_key():
    """Eval run should return 500 when ANTHROPIC_API_KEY is not set."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        response = client.post("/admin/api/eval/run")
        assert response.status_code == 500
        assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_eval_run_rejects_when_already_running():
    """Eval run should return 409 when an eval is already in progress."""
    import app.routes.admin as admin_mod
    with admin_mod._eval_lock:
        admin_mod._eval_running = True
    try:
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            response = client.post("/admin/api/eval/run")
            assert response.status_code == 409
            assert "already in progress" in response.json()["detail"]
    finally:
        with admin_mod._eval_lock:
            admin_mod._eval_running = False


# -----------------------------------------------------------------------
# ADMIN RATE LIMITING (D5)
# -----------------------------------------------------------------------

def test_admin_rate_limit_blocks_after_threshold():
    """Admin endpoints should return 429 after exceeding the IP rate limit."""
    clear_audit_log()
    # Patch to a low limit so the test doesn't need 120+ requests
    with patch("app.dependencies.ADMIN_IP_LIMITS", [(60, 5), (3600, 50)]):
        for i in range(5):
            r = client.get("/admin/api/stats")
            assert r.status_code == 200, f"Request {i+1} should succeed"

        r = client.get("/admin/api/stats")
        assert r.status_code == 429


def test_admin_eval_run_has_stricter_limit():
    """POST /admin/api/eval/run has a tighter rate limit (5/hour)."""
    # Need ANTHROPIC_API_KEY set so we don't get 500 before hitting rate limit,
    # and _eval_running must be True so we get 409 (not actually starting evals).
    import app.routes.admin as admin_mod
    with admin_mod._eval_lock:
        admin_mod._eval_running = True
    try:
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            for i in range(5):
                r = client.post("/admin/api/eval/run")
                assert r.status_code == 409, f"Request {i+1} should get 409 (already running)"

            # 6th request should hit the eval rate limit
            r = client.post("/admin/api/eval/run")
            assert r.status_code == 429
    finally:
        with admin_mod._eval_lock:
            admin_mod._eval_running = False


# -----------------------------------------------------------------------
# HEALTH CHECK (sanity — not admin but confirms app is wired)
# -----------------------------------------------------------------------

def test_health_endpoint():
    """GET /api/health/live should return status=alive.

    Uses the liveness probe rather than the deep readiness check so
    the sanity test doesn't require a live DATABASE_URL. The deep
    /api/health endpoint is for dashboards and intentionally returns
    503 when the DB is unreachable — not the signal we want here.
    """
    response = client.get("/api/health/live")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "alive"
    assert "uptime_seconds" in body


# -----------------------------------------------------------------------


# -----------------------------------------------------------------------
# B4: Feedback event filter
# -----------------------------------------------------------------------

def test_events_filter_by_feedback():
    """Should be able to filter events by type=feedback."""
    clear_audit_log()
    log_conversation_turn("s1", "hi", "hello", {}, "greeting")
    from app.services.audit_log import log_feedback
    log_feedback(session_id="s1", rating="up", comment="great")

    response = client.get("/admin/api/events?event_type=feedback")
    assert response.status_code == 200
    events = response.json()
    assert len(events) == 1
    assert events[0]["type"] == "feedback"
    assert events[0]["rating"] == "up"
