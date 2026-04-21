"""
Tests for results enhancements: sort options, day-specific hours,
and urgent auto-execute.

Covers:
  Gap 8:  Sort options (recently verified, most services)
  Gap 15: Hours for specific days ("are they open Saturday?")
  Gap 16: Auto-execute for high-urgency queries (skip confirmation)

Run with: python -m pytest tests/unit/test_results_enhancements.py -v
"""

import uuid
from datetime import time
from unittest.mock import patch

import pytest

from app.services.post_results import classify_post_results_question, _DAY_NAMES
from app.services.chatbot import generate_reply
from app.services.session_store import clear_session, save_session_slots
from conftest import send


# -----------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------

def _session_with_results(n=5, service_type="food", location="Brooklyn"):
    """Create a session with _last_results populated."""
    sid = f"test-{uuid.uuid4().hex[:8]}"
    clear_session(sid)
    results = []
    for i in range(n):
        results.append({
            "service_id": f"svc-{i}",
            "service_name": f"Service {i+1}",
            "organization": f"Org {i+1}",
            "address": f"{i+1} Main St, Brooklyn, NY 11201",
            "phone": f"212-555-{i:04d}",
            "fees": "Free",
            "hours_today": f"{9+i}:00 AM – 5:00 PM" if i < 3 else None,
            "is_open": "open" if i < 2 else "closed",
            "last_validated_at": f"2026-0{min(i+1,9)}-01T10:00:00" if i < 4 else None,
            "also_available": [f"Svc{j}" for j in range(i)],
            "requires_membership": False,
            "yourpeer_url": None,
        })
    save_session_slots(sid, {
        "service_type": service_type,
        "location": location,
        "_last_results": results,
        "_displayed_count": len(results),
    })
    return sid, results


# -----------------------------------------------------------------------
# GAP 16: AUTO-EXECUTE FOR URGENT QUERIES
# -----------------------------------------------------------------------

class TestAutoExecute:
    """Test that high-urgency queries skip confirmation."""

    @pytest.mark.xfail(reason="Auto-execute for urgent queries not yet implemented — chatbot always confirms")
    def test_high_urgency_skips_confirmation(self):
        """'I need a bed tonight in Brooklyn' should return results directly."""
        result = send("I need a bed tonight in Brooklyn")
        # High urgency + enough info → should auto-execute
        # Result should have services (from mocked query) or be a direct response
        # without follow_up_needed=True (confirmation would set that)
        assert result["follow_up_needed"] is False
        # Should have services from mock
        assert result["result_count"] >= 1

    @pytest.mark.xfail(reason="Auto-execute for urgent queries not yet implemented — chatbot always confirms")
    def test_high_urgency_with_location_executes(self):
        """Urgent + location should execute immediately."""
        result = send("emergency shelter in Harlem right now")
        assert result["follow_up_needed"] is False

    def test_medium_urgency_still_confirms(self):
        """Medium urgency should still go through confirmation."""
        result = send("I need food in Brooklyn this week")
        # "this week" is medium urgency — should ask for confirmation
        # (follow_up_needed=True or result_count=0)
        assert result["result_count"] == 0 or result["follow_up_needed"] is True

    def test_high_urgency_without_location_asks_location(self):
        """Urgent but no location → still need to ask."""
        result = send("I need a bed right now")
        # Has urgency but no location → should ask for location
        assert result["follow_up_needed"] is True
        assert result["result_count"] == 0


# -----------------------------------------------------------------------
# GAP 8: SORT OPTIONS
# -----------------------------------------------------------------------

class TestSortPatterns:
    """Test sort pattern recognition in the chatbot handler."""

    def test_sort_by_recently_verified(self):
        sid, _all = _session_with_results(5)
        with patch("app.services.chatbot.handlers.meta.claude_reply", return_value=""), \
             patch("app.services.chatbot.execution.query_services"), \
             patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None):
            result = generate_reply("Sort by recently verified", session_id=sid)
        assert "recently verified" in result["response"].lower()
        assert len(result["services"]) > 0
        # First result should have the most recent last_validated_at
        if result["services"][0].get("last_validated_at"):
            last_val = result["services"][-1].get("last_validated_at") or ""
            assert result["services"][0]["last_validated_at"] >= last_val
        clear_session(sid)

    def test_sort_by_most_services(self):
        sid, _all = _session_with_results(5)
        with patch("app.services.chatbot.handlers.meta.claude_reply", return_value=""), \
             patch("app.services.chatbot.execution.query_services"), \
             patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None):
            result = generate_reply("Sort by most services", session_id=sid)
        assert "most services" in result["response"].lower()
        assert len(result["services"]) > 0
        # First result should have the most also_available items
        first_count = len(result["services"][0].get("also_available") or [])
        last_count = len(result["services"][-1].get("also_available") or [])
        assert first_count >= last_count
        clear_session(sid)

    def test_sort_updates_last_results(self):
        """After sorting, _last_results should be in the new order."""
        sid, _all = _session_with_results(5)
        with patch("app.services.chatbot.handlers.meta.claude_reply", return_value=""), \
             patch("app.services.chatbot.execution.query_services"), \
             patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None):
            generate_reply("Sort by recently verified", session_id=sid)
        from app.services.session_store import get_session_slots
        slots = get_session_slots(sid)
        stored = slots.get("_last_results", [])
        # Verify stored order matches sort
        dates = [s.get("last_validated_at") or "" for s in stored]
        assert dates == sorted(dates, reverse=True)
        clear_session(sid)

    def test_unrecognized_sort_falls_through(self):
        """An unrecognized sort phrase should not match."""
        sid, _all = _session_with_results(3)
        with patch("app.services.chatbot.handlers.meta.claude_reply", return_value=""), \
             patch("app.services.chatbot.execution.query_services"), \
             patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None):
            result = generate_reply("Sort by cheapest", session_id=sid)
        # Should NOT match the sort handler
        assert "sorted" not in result["response"].lower() or "recently verified" not in result["response"].lower()
        clear_session(sid)


# -----------------------------------------------------------------------
# GAP 15: HOURS FOR SPECIFIC DAYS
# -----------------------------------------------------------------------

class TestDayDetection:
    """Test classify_post_results_question detects day-of-week questions."""

    def test_saturday_detection(self):
        intent = classify_post_results_question("are they open on Saturday?")
        assert intent is not None
        assert intent["type"] == "ask_hours_day"
        assert intent["weekday"] == 6  # ISODOW: Saturday=6

    def test_sunday_detection(self):
        intent = classify_post_results_question("hours on Sunday?")
        assert intent is not None
        assert intent["type"] == "ask_hours_day"
        assert intent["weekday"] == 7  # ISODOW: Sunday=7

    def test_monday_detection(self):
        intent = classify_post_results_question("open Monday?")
        assert intent is not None
        assert intent["type"] == "ask_hours_day"
        assert intent["weekday"] == 1

    def test_abbreviated_day(self):
        intent = classify_post_results_question("what time do they close on Fri?")
        assert intent is not None
        assert intent["type"] == "ask_hours_day"
        assert intent["weekday"] == 5

    def test_weekend_detection(self):
        intent = classify_post_results_question("what are the weekend hours?")
        assert intent is not None
        assert intent["type"] == "ask_hours_day"
        assert intent.get("weekend") is True
        assert intent["weekday"] == 6  # Saturday

    def test_day_without_hours_context_no_match(self):
        """Mentioning a day without an hours question should not match."""
        intent = classify_post_results_question("I went there on Saturday")
        # No hours-related keyword → should not match ask_hours_day
        assert intent is None or intent.get("type") != "ask_hours_day"

    def test_generic_hours_still_works(self):
        """'what are the hours?' without a day should still match generic hours."""
        intent = classify_post_results_question("what are the hours?")
        assert intent is not None
        assert intent["type"] == "ask_field"
        assert intent["field"] == "hours"


class TestDayNameMapping:
    """Verify _DAY_NAMES uses ISO DOW convention."""

    def test_monday_is_1(self):
        assert _DAY_NAMES["monday"] == 1

    def test_sunday_is_7(self):
        assert _DAY_NAMES["sunday"] == 7

    def test_saturday_is_6(self):
        assert _DAY_NAMES["saturday"] == 6

    def test_abbreviations_match_full_names(self):
        assert _DAY_NAMES["mon"] == _DAY_NAMES["monday"]
        assert _DAY_NAMES["tue"] == _DAY_NAMES["tuesday"]
        assert _DAY_NAMES["wed"] == _DAY_NAMES["wednesday"]
        assert _DAY_NAMES["thu"] == _DAY_NAMES["thursday"]
        assert _DAY_NAMES["fri"] == _DAY_NAMES["friday"]
        assert _DAY_NAMES["sat"] == _DAY_NAMES["saturday"]
        assert _DAY_NAMES["sun"] == _DAY_NAMES["sunday"]


class TestHandleHoursForDay:
    """Test _handle_hours_for_day with mocked DB."""

    def test_returns_hours_for_each_service(self):
        sid, _all = _session_with_results(3)
        mock_schedule = {
            "svc-0": [{"opens_at": time(9, 0), "closes_at": time(17, 0)}],
            "svc-1": [{"opens_at": time(10, 0), "closes_at": time(14, 0)}],
        }
        with patch("app.services.chatbot.handlers.meta.claude_reply", return_value=""), \
             patch("app.services.chatbot.execution.query_services"), \
             patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None), \
             patch("app.services.chatbot.handlers.post_results.fetch_schedule_for_day", return_value=mock_schedule):
            result = generate_reply("are they open on Saturday?", session_id=sid)
        assert "Saturday" in result["response"]
        assert "Service 1" in result["response"]
        assert "Service 2" in result["response"]
        assert "call" in result["response"].lower()  # "call ahead to confirm"
        clear_session(sid)

    def test_no_schedule_data_message(self):
        sid, _all = _session_with_results(2)
        with patch("app.services.chatbot.handlers.meta.claude_reply", return_value=""), \
             patch("app.services.chatbot.execution.query_services"), \
             patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None), \
             patch("app.services.chatbot.handlers.post_results.fetch_schedule_for_day", return_value={}):
            result = generate_reply("hours on Sunday?", session_id=sid)
        assert "Sunday" in result["response"]
        assert "No schedule data" in result["response"]
        clear_session(sid)

    def test_weekend_fetches_both_days(self):
        """Weekend query should call fetch_schedule_for_day for both Sat and Sun."""
        sid, _all = _session_with_results(2)
        call_args = []
        def mock_fetch(service_ids, weekday):
            call_args.append(weekday)
            return {}

        with patch("app.services.chatbot.handlers.meta.claude_reply", return_value=""), \
             patch("app.services.chatbot.execution.query_services"), \
             patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None), \
             patch("app.services.chatbot.handlers.post_results.fetch_schedule_for_day", side_effect=mock_fetch):
            generate_reply("what are the weekend hours?", session_id=sid)
        assert 6 in call_args  # Saturday
        assert 7 in call_args  # Sunday
        clear_session(sid)


# -----------------------------------------------------------------------
# SCHEDULE DB FUNCTION
# -----------------------------------------------------------------------

class TestFetchScheduleForDay:
    """Test fetch_schedule_for_day function structure (no live DB)."""

    def test_empty_service_ids_returns_empty(self):
        from app.rag.query_executor import fetch_schedule_for_day
        assert fetch_schedule_for_day([], 1) == {}

    def test_sql_uses_isodow_param(self):
        """Verify the SQL query uses the weekday parameter correctly."""
        from app.rag.query_executor import _SCHEDULE_FOR_DAY_SQL
        assert ":weekday" in _SCHEDULE_FOR_DAY_SQL
        assert ":service_ids" in _SCHEDULE_FOR_DAY_SQL
        assert "holiday_schedules" in _SCHEDULE_FOR_DAY_SQL
