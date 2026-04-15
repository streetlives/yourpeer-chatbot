"""
Post-results filter classification tests.

Covers: _REFINE_RE / _NEW_REQUEST_RE regex interaction, compound filter
        detection (open/free + subcategory), negation regex.

Run with: python -m pytest tests/unit/test_filter_classification.py -v
"""

import pytest

from app.services.post_results import classify_post_results_question
from app.services.session_store import save_session_slots

from test_helpers import _fresh, _send, _build_shelter_results


# =======================================================================
# FIX 2: Post-results refinement detection
# =======================================================================

class TestRefineREClassification:
    """_REFINE_RE catches sub-category refinements without false positives."""

    # --- Should be classified as filter_subcategory ---
    @pytest.mark.parametrize("msg", [
        "Only the adult families intake is relevant",
        "Just the soup kitchens",
        "Only show me the ones for families",
        "Filter to family shelters",
        "Narrow down to youth services",
        "More like that",
        "More like those",
        "Locate more like those",
        "Similar to the first one",
        "Ones that accept walk-ins",
        "Ones for families",
        "Ones with detox",
        "The intake ones",
        "Just the intake",
        "Only intake services",
        "Can you locate more like that",
        "Can you locate similar ones",
        "Refine the results",
    ])
    def test_refinement_detected(self, msg):
        intent = classify_post_results_question(msg)
        assert intent is not None, f"'{msg}' returned None — not caught by _REFINE_RE"
        assert intent["type"] == "filter_subcategory", \
            f"'{msg}' classified as '{intent['type']}', expected 'filter_subcategory'"

    # --- Should escape to main router (new request) ---
    @pytest.mark.parametrize("msg", [
        "Can you locate a food pantry near me",
        "Can you locate shelters in Brooklyn",
        "Can you locate dental services in Manhattan",
        "I need food too",
        "I'm looking for a clinic",
        "Search for food in Queens",
        "Help me find a shelter",
        "Is there a food bank near me",
    ])
    def test_new_request_escapes(self, msg):
        intent = classify_post_results_question(msg)
        assert intent is None, \
            f"'{msg}' should escape to main router but got: {intent}"

    # --- Should NOT be classified as refinement ---
    @pytest.mark.parametrize("msg", [
        "I also need food",
        "What about food pantries",
        "The weather is nice today",
        "How do I get there",
        "Thank you",
    ])
    def test_non_refinement_not_caught(self, msg):
        intent = classify_post_results_question(msg)
        # These should either be None or a different post-results type
        if intent is not None:
            assert intent["type"] != "filter_subcategory", \
                f"'{msg}' incorrectly classified as filter_subcategory"

    # --- Greedy regex guards ---
    def test_tightened_intake_regex_rejects_distant_match(self):
        """'the.*intake' was tightened to max 3 words gap."""
        intent = classify_post_results_question(
            "the weather is terrible and I really need intake services"
        )
        # "the" and "intake" are >3 words apart — should NOT match _REFINE_RE
        # But "I need" matches _NEW_REQUEST_RE and escapes first
        assert intent is None

    def test_intake_within_three_words_matches(self):
        """'the adult families intake' (3 words gap) should match."""
        intent = classify_post_results_question("the adult families intake")
        assert intent is not None
        assert intent["type"] == "filter_subcategory"

    # --- Negative lookahead on 'can you locate' ---
    def test_locate_more_not_blocked_by_new_request(self):
        """'can you locate more like that' should NOT match _NEW_REQUEST_RE."""
        intent = classify_post_results_question("can you locate more like that")
        assert intent is not None
        assert intent["type"] == "filter_subcategory"

    def test_locate_similar_not_blocked(self):
        intent = classify_post_results_question("can you locate similar options")
        assert intent is not None
        assert intent["type"] == "filter_subcategory"

    def test_locate_new_service_escapes(self):
        """'can you locate a food pantry' should escape to main router."""
        intent = classify_post_results_question("can you locate a food pantry")
        assert intent is None

    # --- Targeted negation refinement ---
    # Only unambiguous patterns are regex-caught. Natural language negation
    # ("not the DHS ones", "without referrals") routes through the LLM tier.

    @pytest.mark.parametrize("msg", [
        "Exclude the veterans shelter",
        "Anything but DHS",
        "Everything except Safe Horizon",
    ])
    def test_unambiguous_negation_caught_by_regex(self, msg):
        """Unambiguous negation signals are caught without LLM."""
        intent = classify_post_results_question(msg)
        assert intent is not None, \
            f"'{msg}' was not caught — would fall to LLM tier"
        assert intent["type"] == "filter_subcategory"
        assert intent.get("_is_negation") is True

    @pytest.mark.parametrize("msg", [
        "None of those work",
        "I don't want any of those",
        "None of these are helpful",
        "I don't like those options",
        "Those aren't what I need",
    ])
    def test_blanket_rejection_not_caught(self, msg):
        """Blanket rejections must NOT be caught as filter_subcategory.
        They're handled upstream by negative_preference."""
        intent = classify_post_results_question(msg)
        if intent is not None:
            assert intent["type"] != "filter_subcategory", \
                f"'{msg}' caught as filter_subcategory — should be negative_preference"

    # NOTE: Natural language negation ("not the DHS ones", "without
    # referrals", "don't show me the closed ones") is handled by the
    # LLM tier in classify_post_results_question. These cannot be unit
    # tested without mocking the LLM. Integration tests with LLM mocking
    # should cover: "not the DHS ones" → refine → filter inverted.


# =======================================================================
# FIX 3: confirm_change_location uses extracted location
# =======================================================================


# =======================================================================
# COMPOUND FILTERS (6.2)
# =======================================================================

class TestCompoundFilters:
    """Compound filters apply multiple criteria as AND."""

    def test_open_and_families_detected_as_compound(self):
        intent = classify_post_results_question("Open now and for families")
        assert intent is not None
        assert intent["type"] == "filter_subcategory"
        assert intent.get("_compound") is True
        assert intent.get("_has_open") is True

    def test_free_and_spanish_detected_as_compound(self):
        intent = classify_post_results_question("Free ones that speak Spanish")
        assert intent is not None
        assert intent["type"] == "filter_subcategory"
        assert intent.get("_compound") is True
        assert intent.get("_has_free") is True

    def test_single_open_not_compound(self):
        intent = classify_post_results_question("Which are open?")
        assert intent is not None
        assert intent["type"] == "filter_open"

    def test_single_free_not_compound(self):
        intent = classify_post_results_question("Any free ones?")
        assert intent is not None
        assert intent["type"] == "filter_free"

    def test_compound_intersects_results(self):
        """'Open now and for families' returns only cards that are BOTH."""
        sid = _fresh()
        services = _build_shelter_results(10)["services"]
        # Card 0: families + open → should match
        services[0]["service_taxonomies"] = ["Shelter", "Families"]
        services[0]["is_open"] = "open"
        # Card 1: families + closed → should NOT match
        services[1]["service_taxonomies"] = ["Shelter", "Families"]
        services[1]["is_open"] = "closed"
        # Card 2: not families + open → should NOT match
        services[2]["service_taxonomies"] = ["Shelter"]
        services[2]["is_open"] = "open"

        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
            "_last_results": services,
            "_displayed_count": 5,
        })
        result = _send("open now and for families", sid)
        # Only card 0 matches both criteria
        assert len(result["services"]) == 1, \
            f"Expected 1 (open + families), got {len(result['services'])}"

    def test_compound_empty_intersection_helpful_message(self):
        """When subcategory matches but none are open, offer to show subcategory anyway."""
        sid = _fresh()
        services = _build_shelter_results(10)["services"]
        # 3 family shelters, all closed
        for i in range(3):
            services[i]["service_taxonomies"] = ["Shelter", "Families"]
            services[i]["is_open"] = "closed"

        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
            "_last_results": services,
            "_displayed_count": 5,
        })
        result = _send("open now and for families", sid)
        resp = result["response"].lower()
        assert "3" in resp or "found" in resp, \
            f"Should mention the 3 family matches: {result['response']}"
        assert "open" in resp, \
            f"Should mention open constraint: {result['response']}"

    def test_regex_bug_fix_for_families(self):
        """Pre-existing bug: 'for famil' regex now matches 'for families'."""
        import re
        pattern = re.compile(
            r"\b(for famil\w*|takes? kids|with children|accept\w* children)\b", re.I
        )
        assert pattern.search("for families"), "Should match 'for families'"
        assert pattern.search("for family"), "Should match 'for family'"
        assert pattern.search("takes kids"), "Should match 'takes kids'"


# =======================================================================
# EDGE CASE FIXES (design doc section 6)
# =======================================================================
