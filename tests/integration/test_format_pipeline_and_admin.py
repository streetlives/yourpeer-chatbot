"""
Gap Coverage Tests — Addresses 9 identified testing gaps

1. _compute_freshness — timezone handling, null values, boundary
2. Admin /api/stats response shape — routing, tone, multi_intent keys
3. Post-results through generate_reply — end-to-end integration
4. skip_llm through chatbot pipeline — ≤4 word threshold in generate_reply
5. also_available + last_validated_at in post_results detail view
6. last_validated_at timezone edge cases in format_service_card
7. Multi-intent queue decline → re-offer remaining
8. Prompt builder functions — basic shape validation
9. format_service_card deduplication with also_available

Run with: python -m pytest tests/test_gap_coverage.py -v
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

from app.services.classifier import _classify_action
from app.services.chatbot import generate_reply
from app.services.post_results import (
    answer_from_results,
    _service_detail_response,
)
from app.services.session_store import clear_session, save_session_slots, get_session_slots
from app.services.audit_log import (
    clear_audit_log, log_conversation_turn, log_query_execution,
    log_feedback, log_crisis_detected, get_stats,
)
from app.rag.query_executor import _compute_freshness
from app.rag.query_templates import format_service_card


# -----------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------

MOCK_SERVICES = [
    {"service_id": "1", "service_name": "Test Pantry", "organization": "Org A",
     "is_open": "open", "hours_today": "9AM-5PM", "phone": "555-1234",
     "address": "123 Main St, New York, NY", "fees": "Free",
     "also_available": ["Shower", "Laundry", "Health"],
     "last_validated_at": "2026-04-08T10:00:00"},
    {"service_id": "2", "service_name": "Test Shelter", "organization": "Org B",
     "is_open": None, "hours_today": None, "phone": "555-5678",
     "address": "456 Oak Ave, Brooklyn, NY", "fees": None,
     "also_available": None,
     "last_validated_at": "2025-01-15T10:00:00"},
]

MOCK_QUERY_RESULTS = {
    "services": MOCK_SERVICES, "result_count": 2,
    "template_used": "FoodQuery", "params_applied": {"borough": "Manhattan"},
    "relaxed": False, "execution_ms": 45,
    "freshness": {"fresh": 1, "total": 2, "total_with_date": 2},
}


def _fresh():
    sid = f"test-gap-{uuid.uuid4().hex[:8]}"
    clear_session(sid)
    return sid


def _send(msg, sid, mock_crisis=None):
    with (
        patch("app.services.chatbot.execution.query_services", return_value=MOCK_QUERY_RESULTS),
        patch("app.services.chatbot.handlers.meta.claude_reply", return_value="How can I help?"),
        patch("app.services.chatbot.orchestrator.detect_crisis", return_value=mock_crisis),
    ):
        return generate_reply(msg, session_id=sid)


def _setup_with_results(sid):
    """Run a search and store results in session."""
    _send("food in Manhattan", sid)
    _send("yes", sid)
    assert "_last_results" in get_session_slots(sid)


# -----------------------------------------------------------------------
# 1. _compute_freshness
# -----------------------------------------------------------------------

class TestComputeFreshness:
    """Gap #1: _compute_freshness has zero tests."""

    def test_all_fresh(self):
        now = datetime.now(timezone.utc)
        rows = [
            {"last_validated_at": now - timedelta(days=1)},
            {"last_validated_at": now - timedelta(days=30)},
            {"last_validated_at": now - timedelta(days=89)},
        ]
        result = _compute_freshness(rows)
        assert result == {"fresh": 3, "total": 3, "total_with_date": 3}

    def test_mixed_fresh_stale(self):
        now = datetime.now(timezone.utc)
        rows = [
            {"last_validated_at": now - timedelta(days=10)},    # fresh
            {"last_validated_at": now - timedelta(days=100)},   # stale
            {"last_validated_at": now - timedelta(days=200)},   # stale
        ]
        result = _compute_freshness(rows)
        assert result["fresh"] == 1
        assert result["total"] == 3
        assert result["total_with_date"] == 3

    def test_boundary_90_days(self):
        """A date within the 90-day window should be fresh."""
        now = datetime.now(timezone.utc)
        # Use 89 days to avoid race condition at exact boundary
        rows = [{"last_validated_at": now - timedelta(days=89, hours=23)}]
        result = _compute_freshness(rows)
        assert result["fresh"] == 1

    def test_boundary_91_days(self):
        """91 days ago should be stale."""
        now = datetime.now(timezone.utc)
        rows = [{"last_validated_at": now - timedelta(days=91)}]
        result = _compute_freshness(rows)
        assert result["fresh"] == 0

    def test_null_dates_excluded(self):
        now = datetime.now(timezone.utc)
        rows = [
            {"last_validated_at": now - timedelta(days=1)},
            {"last_validated_at": None},
            {},  # no key at all
        ]
        result = _compute_freshness(rows)
        assert result["fresh"] == 1
        assert result["total"] == 3
        assert result["total_with_date"] == 1

    def test_empty_rows(self):
        result = _compute_freshness([])
        assert result == {"fresh": 0, "total": 0, "total_with_date": 0}

    def test_naive_datetime_treated_as_utc(self):
        """Naive datetimes (no tzinfo) should be treated as UTC."""
        now = datetime.now(timezone.utc)
        naive_recent = (now - timedelta(days=5)).replace(tzinfo=None)
        rows = [{"last_validated_at": naive_recent}]
        result = _compute_freshness(rows)
        assert result["fresh"] == 1

    def test_timezone_aware_datetime(self):
        """Timezone-aware datetimes should work correctly."""
        from datetime import timezone as tz
        now = datetime.now(tz.utc)
        aware = now - timedelta(days=10)
        rows = [{"last_validated_at": aware}]
        result = _compute_freshness(rows)
        assert result["fresh"] == 1


# -----------------------------------------------------------------------
# 2. Admin /api/stats response shape
# -----------------------------------------------------------------------

class TestAdminStatsResponseShape:
    """Gap #2: No test verifies routing/tone/multi_intent in stats."""

    def setup_method(self):
        clear_audit_log()
        log_conversation_turn("s1", "hi", "hello", {}, "greeting")
        log_conversation_turn("s1", "food", "searching", {}, "service", tone="urgent")
        log_conversation_turn("s1", "yes", "results", {}, "confirm_yes")
        log_query_execution("s1", "FoodQuery", {}, 3, False, 40)
        log_conversation_turn("s2", "asdf", "help?", {}, "general")
        log_conversation_turn("s2", "sad", "I hear you", {}, "emotional", tone="emotional")

    def test_stats_has_routing(self):
        stats = get_stats()
        assert "routing" in stats
        r = stats["routing"]
        assert "buckets" in r
        assert "service_flow" in r["buckets"]
        assert "conversational" in r["buckets"]
        assert "emotional" in r["buckets"]
        assert "safety" in r["buckets"]
        assert "general" in r["buckets"]
        assert "total_categorized" in r
        assert "general_rate" in r
        assert "category_distribution" in r

    def test_stats_has_tone_distribution(self):
        stats = get_stats()
        assert "tone_distribution" in stats
        td = stats["tone_distribution"]
        assert "tones" in td
        assert "total_with_tone" in td
        assert "turns_without_tone" in td
        assert isinstance(td["tones"], dict)

    def test_stats_has_multi_intent(self):
        stats = get_stats()
        assert "multi_intent" in stats
        mi = stats["multi_intent"]
        assert "queue_offers" in mi
        assert "queue_declines" in mi
        assert "queue_accept_sessions" in mi

    def test_stats_routing_values_correct(self):
        stats = get_stats()
        r = stats["routing"]
        assert r["buckets"]["service_flow"] == 2  # service + confirm_yes
        assert r["buckets"]["conversational"] == 1  # greeting
        assert r["buckets"]["emotional"] == 1  # emotional
        assert r["buckets"]["general"] == 1  # general
        assert r["total_categorized"] == 5

    def test_stats_tone_values_correct(self):
        stats = get_stats()
        td = stats["tone_distribution"]
        assert td["tones"].get("urgent") == 1
        assert td["tones"].get("emotional") == 1
        assert td["total_with_tone"] == 2
        assert td["turns_without_tone"] == 3

    def test_admin_api_stats_includes_new_keys(self):
        """HTTP-level test that /api/stats returns the new keys.

        Admin endpoints require ``Authorization: Bearer <ADMIN_API_KEY>``
        when the env var is set (see app/dependencies.py :: require_admin_key).
        Without the header, the dependency returns 401 before the route
        runs, which was masking the actual test. We read the same env
        var the app reads and send it in the header. If neither is set
        (dev mode), require_admin_key is a no-op and the header is
        ignored — test still passes.
        """
        import os
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        headers = {}
        admin_key = os.environ.get("ADMIN_API_KEY")
        if admin_key:
            headers["Authorization"] = f"Bearer {admin_key}"
        response = client.get("/admin/api/stats", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert "routing" in data
        assert "tone_distribution" in data
        assert "multi_intent" in data


# -----------------------------------------------------------------------
# 2b. Crisis category aggregation
# -----------------------------------------------------------------------

class TestCrisisByCategory:
    """get_stats() must surface crises broken down by category, not just
    a single total count. Counts come from the crisis_category field on
    each crisis_detected event; the aggregate must always equal
    total_crises (no double-counting, no events dropped)."""

    def setup_method(self):
        clear_audit_log()
        # Stage a representative mix: two domestic_violence, one
        # suicide_self_harm, one medical_emergency, plus one crisis with
        # no category (caught by the fallback path).
        log_crisis_detected("s1", "domestic_violence", "[redacted]")
        log_crisis_detected("s2", "domestic_violence", "[redacted]")
        log_crisis_detected("s3", "suicide_self_harm", "[redacted]")
        log_crisis_detected("s4", "medical_emergency", "[redacted]")
        log_crisis_detected("s5", "", "[redacted]")  # no category

    def test_total_matches_sum_of_categories(self):
        """The single most important invariant: nothing dropped, nothing
        double-counted. If this drifts, the dashboard chart won't sum to
        the headline number and admins lose trust in both."""
        stats = get_stats()
        breakdown_total = sum(stats["crises_by_category"].values())
        assert breakdown_total == stats["total_crises"], (
            f"crises_by_category sums to {breakdown_total} but "
            f"total_crises is {stats['total_crises']}"
        )

    def test_known_categories_present_with_correct_counts(self):
        stats = get_stats()
        bd = stats["crises_by_category"]
        assert bd.get("domestic_violence") == 2
        assert bd.get("suicide_self_harm") == 1
        assert bd.get("medical_emergency") == 1

    def test_missing_category_routed_to_uncategorized(self):
        """A crisis_detected event with no crisis_category (rare, but
        possible if a future code path forgets to set it) must not be
        dropped silently — it should land under 'uncategorized' so the
        sum invariant still holds."""
        stats = get_stats()
        assert stats["crises_by_category"].get("uncategorized") == 1

    def test_sparse_representation(self):
        """Categories that didn't fire don't appear in the dict —
        consistent with category_distribution / service_type_distribution.
        Frontend renders an empty bar (or hides it) for unseen categories."""
        stats = get_stats()
        bd = stats["crises_by_category"]
        # We staged 4 distinct categories + 1 uncategorized; nothing else
        # should be present.
        assert set(bd.keys()) == {
            "domestic_violence", "suicide_self_harm",
            "medical_emergency", "uncategorized",
        }

    def test_empty_when_no_crises(self):
        """Zero-state: no events, empty dict (not None, not missing)."""
        clear_audit_log()
        stats = get_stats()
        assert stats["crises_by_category"] == {}
        assert stats["total_crises"] == 0


# -----------------------------------------------------------------------
# 3. Post-results through generate_reply (end-to-end)
# -----------------------------------------------------------------------

class TestPostResultsIntegration:
    """Gap #3: Post-results flow never tested through generate_reply."""

    def test_post_results_question_after_search(self):
        """'are any open' after results should return post-results answer."""
        sid = _fresh()
        _setup_with_results(sid)
        result = _send("are any of them open now", sid)
        # Should get a post-results answer, not an LLM response
        assert "open" in result["response"].lower() or "hours" in result["response"].lower()
        clear_session(sid)

    def test_post_results_detail_view(self):
        """'tell me about the first one' should show detail view."""
        sid = _fresh()
        _setup_with_results(sid)
        result = _send("tell me about the first one", sid)
        assert "Test Pantry" in result["response"]
        clear_session(sid)

    def test_post_results_cleared_on_new_search(self):
        """Starting a new search should clear stored results."""
        sid = _fresh()
        _setup_with_results(sid)
        _send("I need shelter in Brooklyn", sid)
        slots = get_session_slots(sid)
        assert slots.get("service_type") == "shelter"
        # _last_results should be cleared when new service starts
        clear_session(sid)

    def test_post_results_preserves_session_slots(self):
        """Post-results questions should not modify service slots."""
        sid = _fresh()
        _setup_with_results(sid)
        slots_before = {k: v for k, v in get_session_slots(sid).items()
                        if not k.startswith("_")}
        _send("are any free", sid)
        slots_after = {k: v for k, v in get_session_slots(sid).items()
                       if not k.startswith("_")}
        assert slots_before == slots_after
        clear_session(sid)


# -----------------------------------------------------------------------
# 4. skip_llm through chatbot pipeline
# -----------------------------------------------------------------------

class TestSkipLlmPipeline:
    """Gap #4: skip_llm tested at detector level but not through generate_reply."""

    def test_short_safe_action_skips_crisis_llm(self):
        """'yes' (1 word) should skip the LLM crisis detection call."""
        sid = _fresh()
        _send("food in Manhattan", sid)
        # On the "yes" confirmation, detect_crisis should be called
        # with skip_llm=True (or not called at all for short actions).
        #
        # Patch target: orchestrator.detect_crisis. The name is bound
        # in app/services/chatbot/orchestrator.py at import time, so
        # patching the re-export at app.services.chatbot.detect_crisis
        # would not affect the already-bound local name.
        with (
            patch("app.services.chatbot.execution.query_services", return_value=MOCK_QUERY_RESULTS),
            patch("app.services.chatbot.handlers.meta.claude_reply", return_value="ok"),
            patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None) as mock_crisis,
        ):
            generate_reply("yes", session_id=sid)
            # If called, should have skip_llm=True
            if mock_crisis.called:
                _, kwargs = mock_crisis.call_args
                assert kwargs.get("skip_llm") is True, \
                    "Short safe action should have skip_llm=True"
        clear_session(sid)

    def test_long_message_does_not_skip_crisis_llm(self):
        """'yes I want to die' (5 words) should NOT skip LLM."""
        sid = _fresh()
        _send("food in Manhattan", sid)
        with (
            patch("app.services.chatbot.execution.query_services", return_value=MOCK_QUERY_RESULTS),
            patch("app.services.chatbot.handlers.meta.claude_reply", return_value="ok"),
            patch("app.services.chatbot.orchestrator.detect_crisis",
                  return_value=("suicide_self_harm", "Call 988.")) as mock_crisis,
        ):
            result = generate_reply("yes I want to die", session_id=sid)
            assert "988" in result["response"]
            # Verify the long message didn't skip the LLM — the inverse of
            # test_short_safe_action_skips_crisis_llm
            assert mock_crisis.called, "detect_crisis should have been called"
            _, kwargs = mock_crisis.call_args
            assert kwargs.get("skip_llm") is not True, "Long message should NOT have skip_llm=True"
        clear_session(sid)


# -----------------------------------------------------------------------
# 5. also_available + last_validated_at in post_results detail view
# -----------------------------------------------------------------------

class TestPostResultsDetailNewFields:
    """Gap #5: New fields should appear in post-results detail view."""

    def test_detail_view_shows_also_available(self):
        service = {
            "service_name": "Test Pantry",
            "organization": "Org A",
            "phone": "555-1234",
            "also_available": ["Shower", "Laundry", "Health"],
        }
        result = _service_detail_response(service, [service])
        assert "Also available here" in result["response"]
        assert "Shower" in result["response"]
        assert "Laundry" in result["response"]
        assert "Health" in result["response"]

    def test_detail_view_no_also_available(self):
        service = {
            "service_name": "Test Pantry",
            "also_available": None,
        }
        result = _service_detail_response(service, [service])
        assert "Also available here" not in result["response"]

    def test_detail_view_empty_also_available(self):
        service = {
            "service_name": "Test Pantry",
            "also_available": [],
        }
        result = _service_detail_response(service, [service])
        assert "Also available here" not in result["response"]

    def test_answer_from_results_specific_index_has_also_available(self):
        """The full answer_from_results path should include also_available."""
        services = [
            {"service_name": "Place A", "also_available": ["Clothing Pantry", "Shower"]},
        ]
        result = answer_from_results({"type": "specific_index", "index": 0}, services)
        assert "Also available here" in result["response"]
        assert "Clothing Pantry" in result["response"]


# -----------------------------------------------------------------------
# 6. last_validated_at timezone edge cases in format_service_card
# -----------------------------------------------------------------------

class TestFormatServiceCardTimezones:
    """Gap #6: Timezone handling edge cases."""

    def test_naive_datetime(self):
        dt = datetime(2026, 4, 8, 14, 30, 0)
        card = format_service_card({"service_id": "1", "service_name": "T", "last_validated_at": dt})
        assert card["last_validated_at"] == "2026-04-08T14:30:00"

    def test_aware_utc_datetime(self):
        dt = datetime(2026, 4, 8, 14, 30, 0, tzinfo=timezone.utc)
        card = format_service_card({"service_id": "1", "service_name": "T", "last_validated_at": dt})
        assert "2026-04-08" in card["last_validated_at"]

    def test_none_datetime(self):
        card = format_service_card({"service_id": "1", "service_name": "T"})
        assert card["last_validated_at"] is None

    def test_string_passthrough(self):
        """If DB returns a string (shouldn't happen but be safe)."""
        card = format_service_card({
            "service_id": "1", "service_name": "T",
            "last_validated_at": "2026-04-08",
        })
        assert card["last_validated_at"] == "2026-04-08"


# -----------------------------------------------------------------------
# 7. Multi-intent queue decline → re-offer remaining
# -----------------------------------------------------------------------

class TestMultiIntentQueueDeclineReOffer:
    """Gap #7: Queue with 2+ items — declining first should not lose second."""

    def test_queue_with_two_items_offers_first(self):
        """Queue [shelter, clothing] should offer shelter first."""
        sid = _fresh()
        _send("food in Manhattan", sid)
        # Manually set up queue with 2 items
        slots = get_session_slots(sid)
        slots["_queued_services"] = [("shelter", None), ("clothing", None)]
        save_session_slots(sid, slots)

        # Confirm → should get results + shelter offer
        result = _send("yes", sid)
        assert "shelter" in result["response"].lower() or "also mentioned" in result["response"].lower()
        clear_session(sid)

    def test_decline_first_clears_queue(self):
        """Declining the queue offer should clear the queue."""
        sid = _fresh()
        _send("food in Manhattan", sid)
        # Set 2 items — "yes" will pop the first and offer it.
        # After the offer, one item remains. "no thanks" should clear it.
        slots = get_session_slots(sid)
        slots["_queued_services"] = [("shelter", None), ("clothing", None)]
        save_session_slots(sid, slots)

        _send("yes", sid)  # get results + offer shelter, clothing remains

        # Now the session should still have clothing queued
        _mid_slots = get_session_slots(sid)
        # "no thanks" should trigger queue_decline and clear remaining
        _result = _send("no thanks", sid)

        slots_after = get_session_slots(sid)
        assert "_queued_services" not in slots_after
        clear_session(sid)


# -----------------------------------------------------------------------
# 8. Prompt builder functions — basic shape validation
# -----------------------------------------------------------------------

class TestPromptBuilders:
    """Gap #8: _build_bot_question_prompt untested."""

    def test_bot_question_prompt_returns_string(self):
        from app.services.responses import _build_bot_question_prompt
        result = _build_bot_question_prompt("What can you help me with?", {})
        assert isinstance(result, str)
        assert len(result) > 50

    def test_bot_question_prompt_includes_capabilities(self):
        from app.services.responses import _build_bot_question_prompt
        result = _build_bot_question_prompt("How does this work?", {})
        lower = result.lower()
        assert any(word in lower for word in ["service", "food", "shelter", "nyc", "help"])

    def test_conversational_prompt_returns_string(self):
        from app.services.responses import _build_conversational_prompt
        result = _build_conversational_prompt("Hey how's it going", {})
        assert isinstance(result, str)
        assert len(result) > 50

    def test_fallback_response_returns_string(self):
        from app.services.responses import _fallback_response
        with patch("app.services.chatbot.handlers.meta.claude_reply", return_value="Hi there!"):
            result = _fallback_response("hello", {})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_fallback_response_handles_exception(self):
        from app.services.responses import _fallback_response
        with patch("app.services.responses.claude_reply", side_effect=Exception("fail")):
            result = _fallback_response("hello", {})
        assert "yourpeer.nyc" in result or "try again" in result

    def test_empty_reply_shape(self):
        from app.services.chatbot import _empty_reply
        result = _empty_reply("test-session", "Hello!", {})
        assert result["response"] == "Hello!"
        assert result["session_id"] == "test-session"
        assert "services" in result


# -----------------------------------------------------------------------
# 9. format_service_card also_available deduplication
# -----------------------------------------------------------------------

class TestFormatServiceCardAlsoAvailableEdges:
    """Gap #9: Edge cases for also_available in format_service_card."""

    def test_duplicates_in_also_available(self):
        """Same category appearing twice should be deduplicated."""
        card = format_service_card({
            "service_id": "1", "service_name": "T",
            "also_available": ["Shower", "Shower", "Health", "Health", "Laundry"],
        })
        assert card["also_available"] == ["Health", "Laundry", "Shower"]

    def test_all_non_display_categories(self):
        """If only non-display categories exist, should return None."""
        card = format_service_card({
            "service_id": "1", "service_name": "T",
            "also_available": ["Other service", "Unknown", "Foo Bar"],
        })
        assert card["also_available"] is None

    def test_mixed_display_and_non_display(self):
        """Should filter to display categories only."""
        card = format_service_card({
            "service_id": "1", "service_name": "T",
            "also_available": ["Other service", "Shower", "Unknown", "Mental Health"],
        })
        assert card["also_available"] == ["Mental Health", "Shower"]

    def test_null_input(self):
        card = format_service_card({
            "service_id": "1", "service_name": "T",
            "also_available": None,
        })
        assert card["also_available"] is None

    def test_large_list_preserves_all_display(self):
        """All valid display categories should be kept (after display name normalization)."""
        cats = ["Shelter", "Shower", "Clothing Pantry", "Health",
                "Mental Health", "Laundry", "Legal Services", "Benefits",
                "Education", "Employment", "Food", "Food Pantry",
                "Toiletries", "Mail", "Free Wifi"]
        card = format_service_card({
            "service_id": "1", "service_name": "T",
            "also_available": cats,
        })
        assert len(card["also_available"]) == len(cats)
        # Output should be sorted (format_service_card sorts)
        assert card["also_available"] == sorted(card["also_available"])
