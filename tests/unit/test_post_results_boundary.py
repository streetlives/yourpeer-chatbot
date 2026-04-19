"""Tests for ambiguous routing between post-results follow-ups and new service requests.

These test the boundary where a user has results displayed and sends a message
that could be either:
  (a) a question about the displayed results, or
  (b) a new service request that should start a fresh search

The post-results handler should NOT intercept new service requests.
The normal router should NOT intercept genuine post-results questions.

Run: pytest tests/test_post_results_boundary.py -v
"""

import pytest
import uuid
from conftest import send, send_multi, MOCK_QUERY_RESULTS


@pytest.fixture
def sid():
    return f"test-{uuid.uuid4().hex[:8]}"


def _get_results(sid):
    """Helper: complete a search so results are in session."""
    return send_multi(
        ["I need food in Brooklyn", "yes"],
        session_id=sid,
    )


# ---------------------------------------------------------------------------
# NEW SERVICE REQUESTS SHOULD ESCAPE POST-RESULTS
# These messages should NOT be intercepted by the post-results handler,
# even when _last_results is populated.
# ---------------------------------------------------------------------------


class TestNewRequestEscapesPostResults:
    """Messages that start a new service search should fall through to
    normal routing, not be answered from stored results."""

    def test_i_need_new_service(self, sid):
        """'I need X' is a new request, not a post-results question."""
        _get_results(sid)
        r = send("I need shelter in Manhattan", session_id=sid)
        # Should start a new search flow, not show old results
        assert r["slots"].get("service_type") == "shelter"

    def test_where_can_i_go_with_location(self, sid):
        """'Where can I go in [location]?' is a new request."""
        _get_results(sid)
        r = send(
            "I need to detox from Alcohol and Opiates. Where can I go in Manhattan?",
            session_id=sid,
        )
        # Should NOT show food results or "here's the address"
        assert "address info" not in r["response"].lower()
        assert "clothing" not in r["response"].lower()

    def test_where_can_i_find(self, sid):
        """'Where can I find X?' is a new request."""
        _get_results(sid)
        r = send("Where can I find clothing?", session_id=sid)
        assert "address info" not in r["response"].lower()

    def test_looking_for_new_service(self, sid):
        """'I'm looking for X' is a new request."""
        _get_results(sid)
        r = send("I'm looking for legal help in Queens", session_id=sid)
        assert r["slots"].get("service_type") == "legal"

    def test_can_i_get(self, sid):
        """'Can I get X?' is a new request."""
        _get_results(sid)
        r = send("Can I get shelter in the Bronx?", session_id=sid)
        assert r["slots"].get("service_type") == "shelter"

    def test_help_me_find(self, sid):
        """'Help me find X' is a new request."""
        _get_results(sid)
        r = send("Help me find mental health services", session_id=sid)
        assert r["slots"].get("service_type") == "mental_health"

    def test_search_for(self, sid):
        """'Search for X' is a new request."""
        _get_results(sid)
        r = send("Search for employment programs in Manhattan", session_id=sid)
        assert r["slots"].get("service_type") == "employment"

    def test_is_there(self, sid):
        """'Is there X nearby?' is a new request."""
        _get_results(sid)
        r = send("Is there a shelter near Harlem?", session_id=sid)
        assert r["slots"].get("service_type") == "shelter"

    def test_do_you_have(self, sid):
        """'Do you have X?' is a new request."""
        _get_results(sid)
        r = send("Do you have information about legal aid?", session_id=sid)
        assert r["slots"].get("service_type") == "legal"

    def test_new_location_clears_results(self, sid):
        """Providing a new location should clear _last_results."""
        _get_results(sid)
        _r = send("What about services in Queens?", session_id=sid)
        # The new location "Queens" should signal a new search context
        from app.services.session_store import get_session_slots
        slots = get_session_slots(sid)
        assert slots.get("_last_results") is None


class TestUnrecognizedServiceEscapesPostResults:
    """When the user asks for something that isn't a known service category
    but IS clearly a new request, they should not be trapped in post-results."""

    def test_what_about_unrecognized_service(self, sid):
        """'What about financial services?' — unrecognized category, should
        NOT show 'I couldn't find a service matching' from post-results."""
        _get_results(sid)
        r = send("What about financial services?", session_id=sid)
        # Should NOT say "I couldn't find a service matching"
        assert "matching" not in r["response"].lower()
        # Should fall through to unrecognized service or general handler

    def test_what_about_detox(self, sid):
        """'What about detox programs?' — should not show old food results."""
        _get_results(sid)
        r = send("What about detox programs?", session_id=sid)
        assert "matching" not in r["response"].lower()

    def test_narrative_after_results(self, sid):
        """A long narrative describing a new need should not be intercepted."""
        _get_results(sid)
        r = send(
            "I need to find a shelter, I just lost my apartment and I "
            "don't have anywhere to go tonight",
            session_id=sid,
        )
        # Should start a new shelter search, not answer from food results
        assert r["slots"].get("service_type") == "shelter"


# ---------------------------------------------------------------------------
# GENUINE POST-RESULTS QUESTIONS SHOULD STILL WORK
# These messages should be intercepted by the post-results handler.
# ---------------------------------------------------------------------------


class TestGenuinePostResultsStillWork:
    """Legitimate follow-up questions about displayed results should continue
    to be handled by the post-results system."""

    def test_are_any_open(self, sid):
        """'Are any of them open now?' — genuine post-results filter."""
        _get_results(sid)
        r = send("Are any of them open now?", session_id=sid)
        # Should filter results, not start new search
        assert r["slots"].get("service_type") == "food"  # still food

    def test_first_one(self, sid):
        """'Tell me about the first one' — specific index."""
        _get_results(sid)
        r = send("Tell me about the first one", session_id=sid)
        assert r["slots"].get("service_type") == "food"

    def test_phone_number(self, sid):
        """'What's the phone number?' — field question."""
        _get_results(sid)
        r = send("What's the phone number?", session_id=sid)
        assert "phone" in r["response"].lower() or "call" in r["response"].lower()

    def test_where_are_they_located(self, sid):
        """'Where are they located?' — address question about results."""
        _get_results(sid)
        r = send("Where are they located?", session_id=sid)
        assert "address" in r["response"].lower() or "located" in r["response"].lower()

    def test_are_any_free(self, sid):
        """'Are any of them free?' — filter question."""
        _get_results(sid)
        r = send("Are any of them free?", session_id=sid)
        assert r["slots"].get("service_type") == "food"

    def test_tell_me_about_named_result(self, sid):
        """'Tell me about [exact result name]' — should match."""
        _get_results(sid)
        service_name = MOCK_QUERY_RESULTS["services"][0].get("service_name", "Test Service")
        r = send(f"Tell me about {service_name}", session_id=sid)
        # Should show details, not start new search
        assert r["slots"].get("service_type") == "food"

    def test_show_all_results(self, sid):
        """'Show all results' — re-display results."""
        _get_results(sid)
        r = send("Show all results", session_id=sid)
        assert len(r["services"]) > 0

    def test_what_about_named_result(self, sid):
        """'What about [exact result name]?' — should match the result."""
        _get_results(sid)
        service_name = MOCK_QUERY_RESULTS["services"][0].get("service_name", "Test Service")
        r = send(f"What about {service_name}?", session_id=sid)
        # Should show details of that result
        assert r["slots"].get("service_type") == "food"


# ---------------------------------------------------------------------------
# EDGE CASES — ambiguous messages at the boundary
# ---------------------------------------------------------------------------


class TestAmbiguousEdgeCases:
    """Messages that are genuinely ambiguous — could go either way.
    These test the priority decisions we've made."""

    def test_where_alone_is_post_results(self, sid):
        """Bare 'where?' with results should ask about displayed results."""
        _get_results(sid)
        r = send("Where?", session_id=sid)
        # Short messages without new-request signals stay in post-results
        assert r["slots"].get("service_type") == "food"

    def test_crisis_still_wins_over_post_results(self, sid):
        """Crisis should always trump post-results."""
        _get_results(sid)
        r = send("I want to hurt myself", session_id=sid,
                 mock_crisis_return=("suicide_self_harm", "Call 988."))
        assert "988" in r["response"]

    def test_reset_clears_results(self, sid):
        """'Start over' should clear results and reset."""
        _get_results(sid)
        _r = send("Start over", session_id=sid)
        from app.services.session_store import get_session_slots
        slots = get_session_slots(sid)
        assert slots.get("_last_results") is None

    def test_emotional_after_results(self, sid):
        """Emotional message after results should not show addresses."""
        _get_results(sid)
        r = send("I'm feeling really scared", session_id=sid)
        assert "address" not in r["response"].lower()

    def test_what_about_with_service_keyword(self, sid):
        """'What about shelter?' — has a service keyword, should start
        a new search, not try to find 'shelter' in food results."""
        _get_results(sid)
        r = send("What about shelter?", session_id=sid)
        assert r["slots"].get("service_type") == "shelter"

    def test_multiple_new_requests_after_results(self, sid):
        """Multiple new requests should each clear and restart."""
        _get_results(sid)
        r1 = send("I need shelter in Manhattan", session_id=sid)
        assert r1["slots"].get("service_type") == "shelter"
        _r2 = send("yes", session_id=sid)
        # Now we have shelter results
        r3 = send("I also need clothing", session_id=sid)
        assert r3["slots"].get("service_type") in ("clothing", "shelter")


# ---------------------------------------------------------------------------
# UNIT TESTS for classify_post_results_question escape hatch
# ---------------------------------------------------------------------------


class TestClassifierEscapeHatch:
    """Unit tests for the _NEW_REQUEST_RE escape hatch in
    classify_post_results_question."""

    @pytest.mark.parametrize("msg", [
        "I need food",
        "I need to detox from alcohol",
        "I'm looking for shelter",
        "im looking for help",
        "looking for legal aid",
        "can I get clothing?",
        "find me a shelter",
        "help me find mental health services",
        "search for employment in Queens",
        "can you find dental care?",
        "can you search for food pantries?",
        "where can I go for help?",
        "where can I find a shelter?",
        "where can I get food?",
        "I want to find a clinic",
        "do you have information about shelters?",
        "is there a food pantry near me?",
    ])
    def test_new_request_phrases_return_none(self, msg):
        """New-request phrases should make the classifier return None."""
        from app.services.post_results import classify_post_results_question
        result = classify_post_results_question(msg)
        assert result is None, f"Expected None for new request '{msg}', got {result}"

    @pytest.mark.parametrize("msg,expected_type", [
        ("are any of them open now?", "filter_open"),
        ("are any free?", "filter_free"),
        ("tell me about the first one", "specific_index"),
        ("what's the phone number?", "ask_field"),
        ("where are they located?", "ask_field"),
        ("what are the hours?", "ask_field"),
    ])
    def test_genuine_post_results_still_classified(self, msg, expected_type):
        """Genuine post-results questions should still be classified."""
        from app.services.post_results import classify_post_results_question
        result = classify_post_results_question(msg)
        assert result is not None, f"Expected classification for '{msg}', got None"
        assert result["type"] == expected_type


class TestNameMatchFallthrough:
    """When classify returns specific_name but the name doesn't match
    any result, answer_from_results should return None so chatbot.py
    falls through to normal routing."""

    def test_unmatched_name_returns_none(self):
        """'financial services' won't match any result name."""
        from app.services.post_results import answer_from_results
        mock_results = [
            {"service_name": "Clothing Closet", "organization": "Org A"},
            {"service_name": "Food Pantry", "organization": "Org B"},
        ]
        intent = {"type": "specific_name", "query": "financial services"}
        result = answer_from_results(intent, mock_results)
        assert result is None

    def test_matched_name_returns_response(self):
        """'Clothing Closet' should match and return a response."""
        from app.services.post_results import answer_from_results
        mock_results = [
            {"service_name": "Clothing Closet", "organization": "Org A",
             "address": "123 Main St"},
            {"service_name": "Food Pantry", "organization": "Org B"},
        ]
        intent = {"type": "specific_name", "query": "clothing closet"}
        result = answer_from_results(intent, mock_results)
        assert result is not None
        assert "Clothing Closet" in result["response"]
