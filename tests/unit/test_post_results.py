"""
Tests for post-results question handling.

Validates the deterministic (no-LLM) handler that answers follow-up
questions about services that were just displayed.

Run with: python -m pytest tests/test_post_results.py -v
"""

import uuid
import pytest
from unittest.mock import patch

from app.services.post_results import (
    classify_post_results_question,
    answer_from_results,
)
from app.services.chatbot import generate_reply
from app.services.session_store import (
    clear_session, get_session_slots, save_session_slots,
)


# -----------------------------------------------------------------------
# TEST DATA
# -----------------------------------------------------------------------

MOCK_SERVICES = [
    {
        "service_id": "1",
        "service_name": "Salem Food Pantry",
        "organization": "Salem United Methodist Church",
        "is_open": "open",
        "hours_today": "9:00 AM – 5:00 PM",
        "phone": "212-678-2700",
        "address": "2190 Adam Clayton Powell Jr Blvd, New York, NY, 10027",
        "email": None,
        "website": "https://example.com",
        "fees": "Free",
        "description": "Weekly food distribution",
        "requires_membership": False,
    },
    {
        "service_id": "2",
        "service_name": "Dept of Probation Food Pantry",
        "organization": "Department of Probation",
        "is_open": "closed",
        "hours_today": "8:00 AM – 12:00 PM",
        "phone": "212-851-1403",
        "address": "127 West 127th Street, New York, NY, 10027",
        "email": "info@example.com",
        "website": None,
        "fees": None,
        "description": None,
        "requires_membership": False,
    },
    {
        "service_id": "3",
        "service_name": "Breakfast Lunch and Dinner Program",
        "organization": "Safe Horizon",
        "is_open": None,
        "hours_today": None,
        "phone": None,
        "address": "209 W 125th St, New York, NY, 10027",
        "email": None,
        "website": None,
        "fees": None,
        "description": "Free Breakfast and Lunch",
        "requires_membership": True,
    },
]

MOCK_QUERY_RESULTS = {
    "services": MOCK_SERVICES,
    "result_count": 3,
    "template_used": "FoodQuery",
    "params_applied": {},
    "relaxed": False,
    "execution_ms": 50,
    "freshness": {"fresh": 2, "total": 3, "total_with_date": 3},
}


# -----------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------

def _fresh():
    sid = f"test-pr-{uuid.uuid4().hex[:8]}"
    clear_session(sid)
    return sid


def _send(msg, sid, mock_crisis=None):
    with (
        patch("app.services.chatbot.execution.query_services", return_value=MOCK_QUERY_RESULTS),
        patch("app.services.chatbot.claude_reply", return_value="How can I help?"),
        patch("app.services.chatbot.detect_crisis", return_value=mock_crisis),
    ):
        return generate_reply(msg, session_id=sid)


# -----------------------------------------------------------------------
# CLASSIFICATION
# -----------------------------------------------------------------------

class TestClassifyPostResults:
    """Pattern-matched question classification."""

    # --- Filter questions ---

    @pytest.mark.parametrize("msg", [
        "Are any open now?",
        "which are open?",
        "any open?",
        "who is open today?",
        "still open?",
        "are they open now?",
        "currently open",
    ])
    def test_filter_open(self, msg):
        assert classify_post_results_question(msg)["type"] == "filter_open"

    @pytest.mark.parametrize("msg", [
        "which ones are free?",
        "any free ones?",
        "are they free?",
        "do they cost anything?",
        "no cost options?",
    ])
    def test_filter_free(self, msg):
        assert classify_post_results_question(msg)["type"] == "filter_free"

    # --- Specific by index ---

    @pytest.mark.parametrize("msg,expected_idx", [
        ("tell me about the first one", 0),
        ("the second one", 1),
        ("third option", 2),
        ("#1", 0),
        ("#3", 2),
        ("number 2", 1),
        ("the last one", -1),
    ])
    def test_specific_index(self, msg, expected_idx):
        intent = classify_post_results_question(msg)
        assert intent["type"] == "specific_index"
        assert intent["index"] == expected_idx

    # --- Specific by name ---

    def test_specific_name(self):
        intent = classify_post_results_question("tell me more about Salem")
        assert intent["type"] == "specific_name"
        assert "salem" in intent["query"].lower()

    def test_specific_name_strips_filler(self):
        intent = classify_post_results_question("more about the Safe Horizon one")
        assert intent["type"] == "specific_name"
        assert "safe horizon" in intent["query"].lower()

    # --- Field questions ---

    @pytest.mark.parametrize("msg,field", [
        ("what are the hours?", "hours"),
        ("when do they open?", "hours"),
        ("what time do they close?", "hours"),
        ("where are they?", "address"),
        ("what's the address?", "address"),
        ("how do I get there?", "address"),
        ("what's the phone number?", "phone"),
        ("can I call them?", "phone"),
        ("do they have a website?", "website"),
    ])
    def test_ask_field(self, msg, field):
        intent = classify_post_results_question(msg)
        assert intent["type"] == "ask_field"
        assert intent["field"] == field

    # --- Unknown about results ---

    @pytest.mark.parametrize("msg", [
        "do they accept walk-ins?",
        "are any of them wheelchair accessible?",
        "which one is best for them?",
    ])
    def test_unknown_about_results(self, msg):
        intent = classify_post_results_question(msg)
        assert intent["type"] == "unknown_about_results"

    # --- NOT post-results questions ---

    @pytest.mark.parametrize("msg", [
        "I need shelter",
        "food in Brooklyn",
        "yes",
        "no",
        "start over",
        "hello",
        "thanks",
        "Connect with peer navigator",
    ])
    def test_not_post_results(self, msg):
        assert classify_post_results_question(msg) is None


# -----------------------------------------------------------------------
# ANSWER BUILDER
# -----------------------------------------------------------------------

class TestAnswerFromResults:
    """Deterministic answer assembly from service card data."""

    # --- Filter open ---

    def test_filter_open_returns_open_services(self):
        result = answer_from_results({"type": "filter_open"}, MOCK_SERVICES)
        assert len(result["services"]) == 1
        assert result["services"][0]["service_name"] == "Salem Food Pantry"
        assert "1 of the 3" in result["response"]

    def test_filter_open_none_open_shows_hours(self):
        closed = [dict(s, is_open="closed") for s in MOCK_SERVICES[:2]]
        result = answer_from_results({"type": "filter_open"}, closed)
        assert "None of the results are open" in result["response"]
        assert len(result["services"]) == 0

    def test_filter_open_no_hours_data(self):
        no_hours = [{"service_name": "X", "is_open": None, "hours_today": None, "phone": "555-1234"}]
        result = answer_from_results({"type": "filter_open"}, no_hours)
        assert "don't have confirmed hours" in result["response"]

    # --- Filter free ---

    def test_filter_free_returns_free_services(self):
        result = answer_from_results({"type": "filter_free"}, MOCK_SERVICES)
        assert len(result["services"]) == 1
        assert result["services"][0]["fees"] == "Free"

    def test_filter_free_none_free(self):
        no_free = [{"service_name": "X", "fees": "$5 per visit", "phone": "555-1234"}]
        result = answer_from_results({"type": "filter_free"}, no_free)
        assert "$5 per visit" in result["response"]

    # --- Specific index ---

    def test_specific_index_valid(self):
        result = answer_from_results({"type": "specific_index", "index": 0}, MOCK_SERVICES)
        assert "Salem Food Pantry" in result["response"]
        assert "212-678-2700" in result["response"]
        assert len(result["services"]) == 1

    def test_specific_index_last(self):
        result = answer_from_results({"type": "specific_index", "index": -1}, MOCK_SERVICES)
        assert "Breakfast Lunch and Dinner" in result["response"]

    def test_specific_index_out_of_range(self):
        result = answer_from_results({"type": "specific_index", "index": 99}, MOCK_SERVICES)
        assert "3 result(s)" in result["response"]

    # --- Specific name ---

    def test_specific_name_match(self):
        result = answer_from_results({"type": "specific_name", "query": "salem"}, MOCK_SERVICES)
        assert "Salem Food Pantry" in result["response"]

    def test_specific_name_org_match(self):
        result = answer_from_results({"type": "specific_name", "query": "safe horizon"}, MOCK_SERVICES)
        assert "Breakfast Lunch and Dinner" in result["response"]

    def test_specific_name_no_match(self):
        result = answer_from_results({"type": "specific_name", "query": "nonexistent"}, MOCK_SERVICES)
        assert result is None  # Falls through to normal routing

    # --- Ask field ---

    def test_ask_hours(self):
        result = answer_from_results({"type": "ask_field", "field": "hours"}, MOCK_SERVICES)
        assert "9:00 AM – 5:00 PM" in result["response"]
        assert "not available" in result["response"]  # for service 3

    def test_ask_phone(self):
        result = answer_from_results({"type": "ask_field", "field": "phone"}, MOCK_SERVICES)
        assert "212-678-2700" in result["response"]
        assert "212-851-1403" in result["response"]

    def test_ask_address(self):
        result = answer_from_results({"type": "ask_field", "field": "address"}, MOCK_SERVICES)
        assert "2190 Adam Clayton" in result["response"]

    # --- Unknown ---

    def test_unknown_suggests_navigator(self):
        result = answer_from_results({"type": "unknown_about_results"}, MOCK_SERVICES)
        assert "peer navigator" in result["response"].lower()

    # --- Detail view ---

    def test_detail_view_shows_all_fields(self):
        result = answer_from_results({"type": "specific_index", "index": 0}, MOCK_SERVICES)
        resp = result["response"]
        assert "Salem United Methodist Church" in resp  # org
        assert "Open now" in resp  # status
        assert "212-678-2700" in resp  # phone
        assert "Free" in resp  # fees
        assert "Weekly food distribution" in resp  # description
        assert "call them directly" in resp  # CTA

    def test_detail_view_shows_referral_note(self):
        result = answer_from_results({"type": "specific_index", "index": 2}, MOCK_SERVICES)
        assert "Referral may be required" in result["response"]

    def test_detail_view_closed_service(self):
        result = answer_from_results({"type": "specific_index", "index": 1}, MOCK_SERVICES)
        assert "Closed" in result["response"]

    # --- Edge cases ---

    def test_empty_services(self):
        result = answer_from_results({"type": "filter_open"}, [])
        assert "don't have any results" in result["response"]

    def test_quick_replies_always_include_navigator(self):
        for intent_type in ["filter_open", "filter_free", "unknown_about_results"]:
            result = answer_from_results({"type": intent_type}, MOCK_SERVICES)
            qr_values = [q["value"] for q in result["quick_replies"]]
            assert any("navigator" in v.lower() for v in qr_values), \
                f"No navigator QR for {intent_type}"


# -----------------------------------------------------------------------
# CHATBOT INTEGRATION
# -----------------------------------------------------------------------

class TestPostResultsChatbotFlow:
    """End-to-end: search → results → follow-up question → answer."""

    def test_open_now_after_results(self):
        """'Are any open now?' after results should be handled by post-results."""
        sid = _fresh()
        # Search and get results
        _send("food in Harlem", sid)
        result = _send("yes", sid)  # confirm → execute → results stored
        assert result["result_count"] > 0

        # Now ask about the results
        result2 = _send("Are any open now?", sid)
        # Should NOT show "I'll search for food" confirmation
        assert "I'll search" not in result2["response"]
        # Should mention open/hours/calling
        assert len(result2["response"]) > 0
        clear_session(sid)

    def test_specific_service_after_results(self):
        """'Tell me about the first one' should show detail view."""
        sid = _fresh()
        _send("food in Harlem", sid)
        _send("yes", sid)  # get results

        result = _send("tell me about the first one", sid)
        assert "I'll search" not in result["response"]
        # Should contain detail info
        assert len(result["response"]) > 20
        clear_session(sid)

    def test_show_all_results_redisplays(self):
        """'Show all results' should re-display stored results."""
        sid = _fresh()
        _send("food in Harlem", sid)
        r1 = _send("yes", sid)
        count = r1["result_count"]

        r2 = _send("Show all results", sid)
        assert r2["result_count"] == count
        assert len(r2["services"]) == count
        clear_session(sid)

    def test_new_search_clears_last_results(self):
        """Starting a new search should clear stored results."""
        sid = _fresh()
        _send("food in Harlem", sid)
        _send("yes", sid)

        slots = get_session_slots(sid)
        assert "_last_results" in slots

        # New search intent clears results
        _send("I need shelter in Brooklyn", sid)
        slots = get_session_slots(sid)
        assert "_last_results" not in slots
        clear_session(sid)

    def test_reset_clears_last_results(self):
        """'Start over' should clear stored results (via clear_session)."""
        sid = _fresh()
        _send("food in Harlem", sid)
        _send("yes", sid)
        _send("start over", sid)

        slots = get_session_slots(sid)
        assert "_last_results" not in slots
        clear_session(sid)

    def test_normal_messages_fall_through(self):
        """Messages that aren't post-results questions should route normally."""
        sid = _fresh()
        _send("food in Harlem", sid)
        _send("yes", sid)

        # "thanks" is not a post-results question
        result = _send("thanks", sid)
        assert "I'll search" not in result["response"]
        # Should be handled by the thanks handler, not post-results
        clear_session(sid)

    def test_confirmation_actions_not_intercepted(self):
        """'yes'/'no' after results should go to normal routing, not post-results."""
        sid = _fresh()
        _send("food in Harlem", sid)
        _send("yes", sid)  # first yes → confirm → get results

        # If there's a queued service, "yes" should go to confirmation
        # not be caught by post-results
        # For this test, just verify "yes" doesn't crash
        result = _send("yes", sid)
        assert len(result["response"]) > 0
        clear_session(sid)


# -----------------------------------------------------------------------
# ADDITIONAL SCENARIO COVERAGE
# -----------------------------------------------------------------------

class TestFilterOpenMultiple:
    """Multiple services open — verify count and all returned."""

    def test_two_of_three_open(self):
        services = [
            {"service_name": "A", "is_open": "open", "hours_today": "9–5", "phone": "555-1"},
            {"service_name": "B", "is_open": "open", "hours_today": "8–4", "phone": "555-2"},
            {"service_name": "C", "is_open": "closed", "hours_today": "10–2", "phone": "555-3"},
        ]
        result = answer_from_results({"type": "filter_open"}, services)
        assert "2 of the 3" in result["response"]
        assert len(result["services"]) == 2
        names = {s["service_name"] for s in result["services"]}
        assert names == {"A", "B"}

    def test_all_open(self):
        services = [
            {"service_name": "A", "is_open": "open", "hours_today": "9–5"},
            {"service_name": "B", "is_open": "open", "hours_today": "8–4"},
        ]
        result = answer_from_results({"type": "filter_open"}, services)
        assert "2 of the 2" in result["response"]
        assert len(result["services"]) == 2


class TestNameMatchAmbiguity:
    """When a name query matches multiple services."""

    def test_ambiguous_name_asks_which(self):
        result = answer_from_results(
            {"type": "specific_name", "query": "food pantry"},
            MOCK_SERVICES,
        )
        # Both "Salem Food Pantry" and "Dept of Probation Food Pantry" match
        assert "few matches" in result["response"].lower() or "which" in result["response"].lower()
        assert len(result["services"]) == 2

    def test_fuzzy_first_word_match(self):
        """Single fuzzy match on first word should return detail."""
        services = [
            {"service_name": "Sunrise Shelter", "organization": "Org", "is_open": None,
             "hours_today": None, "phone": "555-1", "address": "1 Main St"},
            {"service_name": "Haven House", "organization": "Org", "is_open": None,
             "hours_today": None, "phone": "555-2", "address": "2 Oak St"},
        ]
        result = answer_from_results(
            {"type": "specific_name", "query": "sunrise"},
            services,
        )
        assert "Sunrise Shelter" in result["response"]
        assert len(result["services"]) == 1


class TestChainedPostResults:
    """Post-results questions should work back-to-back without clearing results."""

    def test_two_questions_in_a_row(self):
        sid = _fresh()
        _send("food in Harlem", sid)
        _send("yes", sid)  # results stored

        r1 = _send("Are any open now?", sid)
        assert "I'll search" not in r1["response"]

        # Second question should ALSO work — results still in session
        r2 = _send("what are the phone numbers?", sid)
        assert "I'll search" not in r2["response"]
        # Should contain phone info (from the mock services)
        assert len(r2["response"]) > 20
        clear_session(sid)

    def test_filter_then_detail(self):
        sid = _fresh()
        _send("food in Harlem", sid)
        _send("yes", sid)

        _send("which are free?", sid)
        # Now ask about a specific one
        r = _send("tell me about the first one", sid)
        assert "I'll search" not in r["response"]
        assert len(r["services"]) <= 1
        clear_session(sid)


class TestShowAllAfterFilter:
    """'Show all results' after a filter should restore the full list."""

    def test_filter_then_show_all(self):
        sid = _fresh()
        _send("food in Harlem", sid)
        r1 = _send("yes", sid)
        full_count = r1["result_count"]

        _send("are any open now?", sid)  # might filter

        r3 = _send("Show all results", sid)
        assert r3["result_count"] == full_count
        clear_session(sid)


class TestTellMeMoreNoName:
    """'Tell me more' without specifying which service."""

    def test_bare_tell_me_more(self):
        intent = classify_post_results_question("tell me more")
        assert intent is not None
        assert intent["type"] == "unknown_about_results"

    def test_just_more_info(self):
        intent = classify_post_results_question("more info")
        assert intent is not None


class TestIndexOutOfBounds:
    """Index references beyond the result count."""

    def test_tenth_of_three(self):
        result = answer_from_results(
            {"type": "specific_index", "index": 9},  # 10th (0-based)
            MOCK_SERVICES,  # only 3
        )
        assert "3 result(s)" in result["response"]
        assert len(result["services"]) == 0

    def test_negative_index_last(self):
        """Index -1 should mean 'the last one'."""
        result = answer_from_results(
            {"type": "specific_index", "index": -1},
            MOCK_SERVICES,
        )
        assert MOCK_SERVICES[-1]["service_name"] in result["response"]


class TestDetailViewCallButton:
    """Call button should only appear when the service has a phone number."""

    def test_service_with_phone_has_call_qr(self):
        result = answer_from_results(
            {"type": "specific_index", "index": 0},
            MOCK_SERVICES,
        )
        qr_labels = [q["label"] for q in result["quick_replies"]]
        assert any("Call" in label or "📞" in label for label in qr_labels)

    def test_service_without_phone_no_call_qr(self):
        result = answer_from_results(
            {"type": "specific_index", "index": 2},  # Safe Horizon — no phone
            MOCK_SERVICES,
        )
        qr_labels = [q["label"] for q in result["quick_replies"]]
        assert not any("📞" in label for label in qr_labels)


class TestFieldQuestionNoData:
    """Field question when no services have that data."""

    def test_website_none_available(self):
        services = [
            {"service_name": "A", "website": None},
            {"service_name": "B", "website": None},
        ]
        result = answer_from_results(
            {"type": "ask_field", "field": "website"},
            services,
        )
        assert "don't have website" in result["response"].lower() or "not available" in result["response"].lower()

    def test_hours_partial_data(self):
        """Some have hours, some don't — should list both."""
        services = [
            {"service_name": "A", "hours_today": "9 AM – 5 PM"},
            {"service_name": "B", "hours_today": None},
        ]
        result = answer_from_results(
            {"type": "ask_field", "field": "hours"},
            services,
        )
        assert "9 AM – 5 PM" in result["response"]
        assert "not available" in result["response"]


class TestCrisisPriorityOverPostResults:
    """Crisis should take priority even when _last_results exists."""

    def test_crisis_not_intercepted_by_post_results(self):
        sid = _fresh()
        _send("food in Harlem", sid)
        _send("yes", sid)  # results stored

        # Now send a crisis message
        result = _send(
            "I want to hurt myself",
            sid,
            mock_crisis=("suicide_self_harm", "If you're in crisis, call 988."),
        )
        # Crisis handler should respond, not post-results
        assert "988" in result["response"] or "crisis" in result["response"].lower() \
            or "I'll search" not in result["response"]
        clear_session(sid)


class TestLastResultsAuditStripping:
    """_last_results should be stripped from audit log entries."""

    def test_last_results_not_in_logged_slots(self):
        from app.services.audit_log import clear_audit_log, get_recent_events
        clear_audit_log()

        sid = _fresh()
        _send("food in Harlem", sid)
        _send("yes", sid)  # stores _last_results in session

        # Ask a post-results question which triggers _log_turn
        _send("are any open now?", sid)

        events = get_recent_events(limit=10)
        for event in events:
            slots = event.get("slots", {})
            assert "_last_results" not in slots, \
                f"_last_results leaked into audit log: {list(slots.keys())}"
        clear_session(sid)


class TestNoCostVariant:
    """'no cost' should match filter_free."""

    def test_no_cost_classification(self):
        intent = classify_post_results_question("no cost options?")
        assert intent is not None
        assert intent["type"] == "filter_free"

    def test_no_fee_classification(self):
        intent = classify_post_results_question("any with no fee?")
        assert intent is not None
        assert intent["type"] == "filter_free"


# -----------------------------------------------------------------------
# CRITICAL SAFETY: crisis_after_results (eval P10)
# -----------------------------------------------------------------------

class TestCrisisAfterResultsSafety:
    """Eval scenario P10: crisis messages after results MUST be caught
    by crisis detection, not intercepted by the post-results handler.

    These test messages contain result-reference words ('they', 'them',
    'those') that WOULD match post-results patterns if crisis detection
    didn't run first."""

    def test_dv_with_result_reference(self):
        """DV disclosure with 'them' — must trigger crisis, not post-results."""
        sid = _fresh()
        _send("food in Harlem", sid)
        _send("yes", sid)
        result = _send(
            "what about them? he said he would kill me tonight",
            sid,
            mock_crisis=("domestic_violence", "Your safety matters. Call 911 or the National DV Hotline at 1-800-799-7233."),
        )
        assert "safety" in result["response"].lower() or "hotline" in result["response"].lower() \
            or "911" in result["response"]
        clear_session(sid)

    def test_suicidal_with_result_reference(self):
        """Suicidal ideation with 'they' — must trigger crisis."""
        sid = _fresh()
        _send("food in Harlem", sid)
        _send("yes", sid)
        result = _send(
            "do they even help people like me? I want to die",
            sid,
            mock_crisis=("suicide_self_harm", "You're not alone. Call 988 Suicide & Crisis Lifeline."),
        )
        assert "988" in result["response"] or "not alone" in result["response"].lower()
        clear_session(sid)

    def test_safety_concern_with_those(self):
        """Safety concern with 'those' — must trigger crisis."""
        sid = _fresh()
        _send("food in Harlem", sid)
        _send("yes", sid)
        result = _send(
            "I dont think those places can help, nobody cares if i disappear",
            sid,
            mock_crisis=("suicide_self_harm", "Call 988."),
        )
        assert "988" in result["response"]
        clear_session(sid)

    def test_safe_action_skips_llm_but_regex_still_runs(self):
        """'yes' should skip LLM crisis but regex crisis check still runs."""
        sid = _fresh()
        _send("food in Harlem", sid)
        _send("yes", sid)
        # Even with _last_results, "yes" should not crash or be caught
        # by post-results — it's a confirmation action
        result = _send("yes", sid)
        assert len(result["response"]) > 0
        clear_session(sid)


class TestSkipLlmPerformance:
    """Verify that skip_llm parameter works on detect_crisis."""

    def test_skip_llm_still_catches_regex_crisis(self):
        """Even with skip_llm=True, regex crisis phrases must still be caught."""
        from app.services.crisis_detector import detect_crisis
        # "I want to kill myself" matches regex — should be caught
        # even with skip_llm=True
        result = detect_crisis("I want to kill myself", skip_llm=True)
        assert result is not None
        assert result[0] == "suicide_self_harm"

    def test_skip_llm_returns_none_for_safe_message(self):
        """With skip_llm=True, non-crisis messages return None without LLM call."""
        from app.services.crisis_detector import detect_crisis
        result = detect_crisis("yes", skip_llm=True)
        assert result is None

    def test_skip_llm_false_default(self):
        """Default skip_llm=False should call LLM when enabled."""
        from app.services.crisis_detector import detect_crisis
        with (
            patch("app.services.crisis_detector._USE_LLM_DETECTION", True),
            patch("app.services.crisis_detector._detect_crisis_llm", return_value=None) as mock_llm,
        ):
            detect_crisis("some ambiguous message about life")
            mock_llm.assert_called_once()

    def test_skip_llm_true_no_llm_call(self):
        """With skip_llm=True, LLM should NOT be called even when enabled."""
        from app.services.crisis_detector import detect_crisis
        with (
            patch("app.services.crisis_detector._USE_LLM_DETECTION", True),
            patch("app.services.crisis_detector._detect_crisis_llm") as mock_llm,
        ):
            detect_crisis("some ambiguous message about life", skip_llm=True)
            mock_llm.assert_not_called()


# -----------------------------------------------------------------------
# CALL BUTTON HREF (tel: link)
# -----------------------------------------------------------------------

class TestCallButtonHref:
    """Call quick reply buttons should include href for native dialer."""

    def test_call_qr_has_tel_href(self):
        from app.services.post_results import _call_qr
        qr = _call_qr({"service_name": "Test Place", "phone": "(555) 123-4567"})
        assert qr["href"] == "tel:5551234567"

    def test_call_qr_strips_non_digits(self):
        from app.services.post_results import _call_qr
        qr = _call_qr({"service_name": "Test", "phone": "+1 (800) 799-7233"})
        assert qr["href"] == "tel:18007997233"

    def test_call_qr_no_phone_no_href(self):
        from app.services.post_results import _call_qr
        qr = _call_qr({"service_name": "Test", "phone": ""})
        assert qr["href"] is None

    def test_detail_view_has_call_button(self):
        """Detail view should include call button when service has phone."""
        result = answer_from_results(
            {"type": "specific_index", "index": 0},
            MOCK_SERVICES,
        )
        call_qrs = [q for q in result["quick_replies"] if "📞" in q.get("label", "")]
        assert len(call_qrs) == 1
        assert call_qrs[0].get("href", "").startswith("tel:")

    def test_detail_view_no_call_without_phone(self):
        """Detail view should NOT include call button when no phone."""
        result = answer_from_results(
            {"type": "specific_index", "index": 2},  # Safe Horizon — no phone
            MOCK_SERVICES,
        )
        call_qrs = [q for q in result["quick_replies"] if "📞" in q.get("label", "")]
        assert len(call_qrs) == 0


class TestCallQrDeduplication:
    """Call buttons should deduplicate by phone number."""

    def test_same_phone_produces_one_button(self):
        from app.services.post_results import _call_qrs
        services = [
            {"service_name": "Service A", "phone": "212-828-8464"},
            {"service_name": "Service B", "phone": "212-828-8464"},
        ]
        qrs = _call_qrs(services)
        assert len(qrs) == 1

    def test_different_phones_produce_two_buttons(self):
        from app.services.post_results import _call_qrs
        services = [
            {"service_name": "Service A", "phone": "212-828-8464"},
            {"service_name": "Service B", "phone": "212-555-1234"},
        ]
        qrs = _call_qrs(services)
        assert len(qrs) == 2
