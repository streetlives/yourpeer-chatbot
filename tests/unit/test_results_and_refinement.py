"""
Tests for results delivery, pagination, and post-results refinement.

Covers:
  Results delivery:
    - Display pagination (_DISPLAY_PAGE_SIZE = 5, query wide / display narrow)
    - Crisis step-down display (loc_label must never render as "None")
    - Total-vs-displayed messaging ("I found 10 — here are the top 5")
    - service_taxonomies card field for client-side filtering

  Post-results refinement:
    - _REFINE_RE + _NEW_REQUEST_RE regex interaction
    - confirm_change_location uses extracted location inline
    - Frustration re-statement detection ("I already said Manhattan")
    - Frustration handler context recovery (offers to proceed when slots sufficient)
    - confirm_yes after recovery routes to search, not escalation

Run with: python -m pytest tests/unit/test_results_and_refinement.py -v
"""

import uuid
import pytest
from unittest.mock import patch

from app.services.post_results import classify_post_results_question
from app.services.classifier import _classify_tone, _classify_action
from app.services.chatbot import generate_reply, _DISPLAY_PAGE_SIZE
from app.services.session_store import (
    clear_session, get_session_slots, save_session_slots,
)
from conftest import send, send_multi, MOCK_QUERY_RESULTS, MOCK_SERVICE_CARD


# -----------------------------------------------------------------------
# TEST DATA
# -----------------------------------------------------------------------

def _fresh():
    sid = f"test-dvfix-{uuid.uuid4().hex[:8]}"
    clear_session(sid)
    return sid


def _send(msg, sid, mock_crisis=None, mock_query=None):
    with (
        patch("app.services.chatbot.query_services",
              return_value=mock_query or MOCK_QUERY_RESULTS),
        patch("app.services.chatbot.claude_reply",
              return_value="How can I help?"),
        patch("app.services.chatbot.detect_crisis",
              return_value=mock_crisis),
    ):
        return generate_reply(msg, session_id=sid)


def _build_shelter_results(n):
    """Build mock shelter results with taxonomy tags for filtering tests."""
    base = [
        {
            "service_id": "1", "service_name": "Adult Families Intake",
            "organization": "Department of Homeless Services (DHS)",
            "description": "Intake Center for Adult Families",
            "address": "400 E 30th St, New York, NY",
            "city": "Manhattan", "phone": "212-555-0001",
            "fees": "Free", "is_open": "open",
            "hours_today": "8:00 AM – 6:00 PM",
            "requires_membership": False, "yourpeer_url": None,
            "last_validated_at": "2026-04-01",
            "also_available": ["Case Management"],
            "eligibility_summary": "Families",
            "review_highlight": "Staff was helpful",
            "required_documents": None,
            "languages": ["English", "Spanish"],
            "service_taxonomies": ["Shelter", "Families", "Intake"],
        },
        {
            "service_id": "2", "service_name": "Family Intake",
            "organization": "Department of Homeless Services (DHS)",
            "description": "DHS shelter intake for Families with children",
            "address": "151 E 151st St, Bronx, NY",
            "city": "Bronx", "phone": "212-555-0002",
            "fees": "Free", "is_open": "closed",
            "hours_today": "9:00 AM – 5:00 PM",
            "requires_membership": False, "yourpeer_url": None,
            "last_validated_at": "2026-03-15",
            "also_available": ["Food"],
            "eligibility_summary": "Families",
            "review_highlight": None,
            "required_documents": ["State ID"],
            "languages": ["English"],
            "service_taxonomies": ["Shelter", "Families"],
        },
        {
            "service_id": "3", "service_name": "Single Adult Shelter",
            "organization": "CAMBA",
            "description": "Emergency shelter for single adults",
            "address": "1000 Blake Ave, Brooklyn, NY",
            "city": "Brooklyn", "phone": "212-555-0003",
            "fees": "Free", "is_open": "open",
            "hours_today": "24 Hours",
            "requires_membership": False, "yourpeer_url": None,
            "last_validated_at": "2026-04-10",
            "also_available": ["Shower", "Food"],
            "eligibility_summary": None,
            "review_highlight": None,
            "required_documents": None,
            "languages": ["English"],
            "service_taxonomies": ["Shelter", "Single Adult"],
        },
        {
            "service_id": "4", "service_name": "Veterans Shelter",
            "organization": "Veterans Affairs",
            "description": "Transitional housing for veterans",
            "address": "245 W Houston St, New York, NY",
            "city": "Manhattan", "phone": "212-555-0004",
            "fees": "Free", "is_open": None,
            "hours_today": None,
            "requires_membership": True, "yourpeer_url": None,
            "last_validated_at": "2026-02-20",
            "also_available": ["Health"],
            "eligibility_summary": None,
            "review_highlight": "Really supportive environment",
            "required_documents": ["DD-214"],
            "languages": ["English"],
            "service_taxonomies": ["Shelter", "Veterans", "Veterans Short-Term Housing"],
        },
        {
            "service_id": "5", "service_name": "Youth Safe Haven",
            "organization": "Covenant House",
            "description": "Emergency shelter for youth ages 16-24",
            "address": "460 W 41st St, New York, NY",
            "city": "Manhattan", "phone": "212-555-0005",
            "fees": "Free", "is_open": "open",
            "hours_today": "24 Hours",
            "requires_membership": False, "yourpeer_url": None,
            "last_validated_at": "2026-04-12",
            "also_available": ["Food", "Health", "Employment"],
            "eligibility_summary": "Ages 16–24",
            "review_highlight": None,
            "required_documents": None,
            "languages": ["English", "Spanish"],
            "service_taxonomies": ["Shelter", "Youth", "Safe Haven"],
        },
    ]
    # Pad to n with generic cards
    while len(base) < n:
        i = len(base)
        base.append({
            "service_id": str(i + 1),
            "service_name": f"Shelter Program {i + 1}",
            "organization": f"Org {i + 1}",
            "description": f"Emergency shelter services",
            "address": f"{i+1} Main St, Manhattan, NY",
            "city": "Manhattan", "phone": f"212-555-{i:04d}",
            "fees": "Free", "is_open": None,
            "hours_today": None,
            "requires_membership": False, "yourpeer_url": None,
            "last_validated_at": None,
            "also_available": None,
            "eligibility_summary": None,
            "review_highlight": None,
            "required_documents": None,
            "languages": None,
            "service_taxonomies": ["Shelter"],
        })
    return {
        "services": base[:n],
        "result_count": n,
        "template_used": "HousingEligibilityQuery",
        "params_applied": {},
        "relaxed": False,
        "execution_ms": 50,
        "freshness": {"fresh": n, "total": n, "total_with_date": n},
    }


# =======================================================================
# FIX 1: loc_label None display in crisis step-down
# =======================================================================

class TestLocLabelNoneDisplay:
    """Crisis step-down must show 'your area' not 'None' when no location."""

    def test_crisis_step_down_no_location_shows_your_area(self):
        """The original bug: 'shelter in None' displayed to a DV survivor."""
        sid = _fresh()
        result = _send(
            "I'm 19 with a toddler fleeing domestic violence "
            "need somewhere safe tonight",
            sid,
            mock_crisis=("domestic_violence",
                         "I'm sorry you're going through this. "
                         "National DV Hotline: 1-800-799-7233"),
        )
        assert "None" not in result["response"], \
            f"Response contains 'None': {result['response']}"
        # Should say "your area" since no location was provided
        if "help you find" in result["response"]:
            assert "your area" in result["response"]

    def test_crisis_step_down_with_location_shows_location(self):
        """When location IS provided, it should appear in the step-down."""
        sid = _fresh()
        result = _send(
            "Fleeing domestic violence need shelter in Manhattan",
            sid,
            mock_crisis=("domestic_violence",
                         "You deserve to be safe."),
        )
        assert "None" not in result["response"]
        if "help you find" in result["response"]:
            assert "manhattan" in result["response"].lower()


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

class TestConfirmChangeLocationUsesExtracted:
    """When the user says location + change signal, use it directly."""

    def test_change_to_brooklyn_sets_location_directly(self):
        """'Change to Brooklyn' should set Brooklyn, not wipe and re-ask."""
        sid = _fresh()
        # Build up session with pending confirmation
        save_session_slots(sid, {
            "service_type": "food",
            "location": "manhattan",
            "_pending_confirmation": True,
        })
        result = _send("change to Brooklyn", sid)
        slots = get_session_slots(sid)
        assert slots.get("location") == "brooklyn", \
            f"Location should be 'brooklyn', got: {slots.get('location')}"
        # Should NOT show borough picker
        qr_values = [qr["value"] for qr in result.get("quick_replies", [])]
        assert "Queens" not in qr_values, \
            "Should not show borough picker when location was provided"

    def test_change_location_without_value_shows_picker(self):
        """'Different location' with no location → show borough picker."""
        sid = _fresh()
        save_session_slots(sid, {
            "service_type": "food",
            "location": "manhattan",
            "_pending_confirmation": True,
        })
        result = _send("different location", sid)
        qr_values = [qr["value"] for qr in result.get("quick_replies", [])]
        assert "Manhattan" in qr_values, \
            "Should show borough picker when no location provided"

    def test_outside_pending_change_location_with_value(self):
        """confirm_change_location outside pending also uses extracted location."""
        sid = _fresh()
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
        })
        result = _send("change to Queens", sid)
        slots = get_session_slots(sid)
        assert slots.get("location") == "queens", \
            f"Location should be 'queens', got: {slots.get('location')}"


# =======================================================================
# FIX 4: Frustration re-statement phrases
# =======================================================================

class TestFrustrationRestatementPhrases:
    """'I already said Manhattan' etc. should route to frustration, not
    confirm_change_location."""

    @pytest.mark.parametrize("msg", [
        "I already said Manhattan",
        "I already told you Brooklyn",
        "I just told you Queens",
        "I just said the Bronx",
        "Why are you asking me this",
        "Why are you asking again",
        "You already know where I am",
        "You're giving me the same options",
    ])
    def test_restatement_classified_as_frustrated(self, msg):
        tone = _classify_tone(msg, crisis_result=None)
        assert tone == "frustrated", \
            f"'{msg}' should be frustrated, got tone='{tone}'"

    def test_restatement_not_classified_as_change_location(self):
        """Critically: 'I already said Manhattan' must NOT be
        confirm_change_location — that would wipe the location."""
        action = _classify_action("I already said Manhattan")
        assert action != "confirm_change_location", \
            "'I already said Manhattan' was misclassified as confirm_change_location"

    def test_service_intent_overrides_frustration_tone(self):
        """'I already said I need food' has frustration tone but service
        intent should take routing priority."""
        from app.services.slot_extractor import extract_slots
        extracted = extract_slots("I already said I need food")
        tone = _classify_tone("I already said I need food", crisis_result=None)
        assert extracted.get("service_type") == "food"
        assert tone == "frustrated"
        # In routing, service intent (line 578) takes priority over
        # frustrated tone (line 589). We verify extraction is correct;
        # the routing priority is tested by integration tests.


# =======================================================================
# FIX 5: Frustration handler context recovery
# =======================================================================

class TestFrustrationContextRecovery:
    """When the user is frustrated but session has enough to search,
    offer to proceed."""

    def test_recovery_offers_to_proceed(self):
        """First frustration + enough slots → 'Sound good?' confirmation."""
        sid = _fresh()
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
        })
        result = _send("why are you asking me this", sid)
        resp = result["response"].lower()
        assert "apologize" in resp or "sorry" in resp or "right" in resp, \
            f"Should acknowledge frustration, got: {result['response']}"
        assert "manhattan" in resp, \
            "Should mention the existing location"
        assert "shelter" in resp, \
            "Should mention the existing service type"

    def test_recovery_sets_pending_confirmation(self):
        """Recovery path should set _pending_confirmation so 'Yes' works."""
        sid = _fresh()
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
        })
        _send("why are you asking me this", sid)
        slots = get_session_slots(sid)
        assert slots.get("_pending_confirmation") is True, \
            "_pending_confirmation should be set after recovery"

    def test_recovery_clears_last_action(self):
        """Recovery path must clear _last_action so confirm_yes reaches
        _handle_pending_confirmation, not _handle_context_aware_confirm."""
        sid = _fresh()
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
        })
        _send("why are you asking me this", sid)
        slots = get_session_slots(sid)
        assert slots.get("_last_action") is None, \
            "_last_action should be cleared — otherwise 'Yes' routes to escalation"

    def test_yes_after_recovery_executes_search(self):
        """The critical regression: 'Yes' after recovery must execute search,
        NOT show escalation response."""
        sid = _fresh()
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
        })
        _send("why are you asking me this", sid)
        result = _send("Yes, search", sid)
        # Should execute a search and return service cards
        assert result.get("result_count", 0) >= 1 or \
            "option" in result["response"].lower() or \
            "found" in result["response"].lower(), \
            f"'Yes' should execute search, got: {result['response']}"
        # Should NOT show escalation / peer navigator response
        assert "peer navigator" not in result["response"].lower() or \
            result.get("result_count", 0) >= 1, \
            f"Should not escalate — should search. Got: {result['response']}"

    def test_no_recovery_without_enough_slots(self):
        """Without service_type + location, frustration uses default response."""
        sid = _fresh()
        save_session_slots(sid, {
            "service_type": "shelter",
            # No location — not enough to search
        })
        result = _send("why are you asking me this", sid)
        slots = get_session_slots(sid)
        assert slots.get("_pending_confirmation") is not True, \
            "Should NOT offer confirmation without location"

    def test_escalation_on_repeated_frustration(self):
        """Second frustration still escalates even with enough slots."""
        sid = _fresh()
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
        })
        _send("this is useless", sid)  # first frustration — offers recovery
        result = _send("still not helpful", sid)  # second frustration
        resp = result["response"].lower()
        assert "peer navigator" in resp or "311" in resp, \
            "Second frustration should escalate to navigator"


# =======================================================================
# PAGINATION: _DISPLAY_PAGE_SIZE = 5
# =======================================================================

class TestDisplayPagination:
    """Query wide (25), store all, display narrow (5) per page."""

    def test_display_page_size_is_five(self):
        assert _DISPLAY_PAGE_SIZE == 5

    def test_initial_display_capped_at_page_size(self):
        results = send_multi(
            ["I need shelter", "Manhattan", "Yes, search"],
            mock_query_return=_build_shelter_results(15),
        )
        final = results[-1]
        assert len(final["services"]) == 5, \
            f"Should display 5, got {len(final['services'])}"

    def test_all_results_stored_in_session(self):
        sid = _fresh()
        results = []
        with patch("app.services.chatbot.query_services",
                   return_value=_build_shelter_results(15)), \
             patch("app.services.chatbot.claude_reply", return_value=""), \
             patch("app.services.chatbot.detect_crisis", return_value=None):
            for msg in ["I need shelter", "Manhattan", "Yes, search"]:
                results.append(generate_reply(msg, session_id=sid))

        slots = get_session_slots(sid)
        assert len(slots.get("_last_results", [])) == 15, \
            "All 15 results should be stored for client-side filtering"
        assert slots.get("_displayed_count") == 5

    def test_show_more_button_capped_at_page_size(self):
        results = send_multi(
            ["I need shelter", "Manhattan", "Yes, search"],
            mock_query_return=_build_shelter_results(20),
        )
        final = results[-1]
        qr_labels = [qr["label"] for qr in final.get("quick_replies", [])]
        more_labels = [l for l in qr_labels if "more result" in l.lower()]
        assert len(more_labels) == 1, f"Should have one 'show more' button, got: {qr_labels}"
        # Should say "Show 5 more" not "Show 15 more"
        assert "5 more" in more_labels[0], \
            f"Button should say '5 more', got: {more_labels[0]}"

    def test_show_more_returns_next_page(self):
        results = send_multi(
            ["I need shelter", "Manhattan", "Yes, search", "Show more results"],
            mock_query_return=_build_shelter_results(12),
        )
        first_page = results[2]
        second_page = results[3]
        assert len(first_page["services"]) == 5
        assert len(second_page["services"]) == 5
        assert "more" in second_page["response"].lower()

    def test_show_more_third_page_gets_remainder(self):
        sid = _fresh()
        with patch("app.services.chatbot.query_services",
                   return_value=_build_shelter_results(12)), \
             patch("app.services.chatbot.claude_reply", return_value=""), \
             patch("app.services.chatbot.detect_crisis", return_value=None):
            generate_reply("I need shelter", session_id=sid)
            generate_reply("Manhattan", session_id=sid)
            p1 = generate_reply("Yes, search", session_id=sid)
            p2 = generate_reply("Show more results", session_id=sid)
            p3 = generate_reply("Show more results", session_id=sid)

        assert len(p1["services"]) == 5   # page 1: 5
        assert len(p2["services"]) == 5   # page 2: 5
        assert len(p3["services"]) == 2   # page 3: remainder

    def test_no_show_more_when_all_fit(self):
        results = send_multi(
            ["I need shelter", "Manhattan", "Yes, search"],
            mock_query_return=_build_shelter_results(3),
        )
        final = results[-1]
        qr_labels = [qr["label"] for qr in final.get("quick_replies", [])]
        assert not any("more result" in l.lower() for l in qr_labels), \
            "No 'show more' when all results fit on one page"

    def test_response_shows_total_vs_displayed(self):
        results = send_multi(
            ["I need shelter", "Manhattan", "Yes, search"],
            mock_query_return=_build_shelter_results(12),
        )
        resp = results[-1]["response"]
        assert "12" in resp, f"Response should mention total (12), got: {resp}"
        assert "5" in resp, f"Response should mention displayed (5), got: {resp}"

    def test_response_no_pagination_language_when_all_fit(self):
        results = send_multi(
            ["I need shelter", "Manhattan", "Yes, search"],
            mock_query_return=_build_shelter_results(4),
        )
        resp = results[-1]["response"]
        assert "top" not in resp.lower(), \
            f"Should not say 'top N' when all fit: {resp}"

    def test_sort_respects_page_size(self):
        """Sort handler should also page by _DISPLAY_PAGE_SIZE."""
        sid = _fresh()
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
            "_last_results": _build_shelter_results(12)["services"],
            "_displayed_count": 5,
        })
        result = _send("sort by recently verified", sid)
        assert len(result["services"]) == 5, \
            f"Sort should display 5, got {len(result['services'])}"


# =======================================================================
# FILTERED RESULTS PAGINATION
# =======================================================================

class TestFilteredResultsPagination:
    """Filtered results also paginate by _DISPLAY_PAGE_SIZE."""

    def test_filter_displays_first_page_only(self):
        """8 filtered matches should display only first 5."""
        sid = _fresh()
        # Build 15 results where 8 have "Families" taxonomy
        services = _build_shelter_results(15)["services"]
        for i in range(8):
            services[i]["service_taxonomies"] = ["Shelter", "Families"]
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
            "_last_results": services,
            "_displayed_count": 5,
        })
        result = _send("ones for families", sid)
        assert len(result["services"]) == 5, \
            f"Should display 5 of 8 filtered, got {len(result['services'])}"

    def test_filter_stores_full_set(self):
        """All filtered matches stored in _filtered_results for 'which is open?'."""
        sid = _fresh()
        services = _build_shelter_results(15)["services"]
        for i in range(8):
            services[i]["service_taxonomies"] = ["Shelter", "Families"]
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
            "_last_results": services,
            "_displayed_count": 5,
        })
        _send("ones for families", sid)
        slots = get_session_slots(sid)
        assert len(slots.get("_filtered_results", [])) == 8, \
            "Full filtered set should be stored"
        assert slots.get("_displayed_count") == 5

    def test_filter_show_more_button(self):
        """'Show N more' button appears when filtered results exceed page size."""
        sid = _fresh()
        services = _build_shelter_results(15)["services"]
        for i in range(8):
            services[i]["service_taxonomies"] = ["Shelter", "Families"]
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
            "_last_results": services,
            "_displayed_count": 5,
        })
        result = _send("ones for families", sid)
        qr_labels = [qr["label"] for qr in result.get("quick_replies", [])]
        more_labels = [l for l in qr_labels if "more result" in l.lower()]
        assert len(more_labels) == 1, f"Expected show-more button, got: {qr_labels}"
        assert "3 more" in more_labels[0], \
            f"Should say '3 more' (8-5=3), got: {more_labels[0]}"

    def test_filter_no_show_more_when_all_fit(self):
        """3 filtered matches all fit — no show-more button."""
        sid = _fresh()
        services = _build_shelter_results(15)["services"]
        for i in range(3):
            services[i]["service_taxonomies"] = ["Shelter", "Veterans"]
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
            "_last_results": services,
            "_displayed_count": 5,
        })
        result = _send("ones for veterans", sid)
        qr_labels = [qr["label"] for qr in result.get("quick_replies", [])]
        assert not any("more result" in l.lower() for l in qr_labels), \
            f"No show-more when all fit, got: {qr_labels}"

    def test_show_more_pages_through_filtered_set(self):
        """'Show more' after filter pages through _filtered_results, not _last_results."""
        sid = _fresh()
        services = _build_shelter_results(15)["services"]
        for i in range(8):
            services[i]["service_taxonomies"] = ["Shelter", "Families"]
            services[i]["service_name"] = f"Family Shelter {i+1}"
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
            "_last_results": services,
            "_displayed_count": 5,
        })
        _send("ones for families", sid)  # shows first 5 of 8 filtered
        result = _send("show more", sid)  # should show remaining 3
        assert len(result["services"]) == 3, \
            f"Should show 3 remaining filtered results, got {len(result['services'])}"
        # All shown services should be from the filtered set (families)
        for svc in result["services"]:
            assert "Family" in svc["service_name"], \
                f"Service '{svc['service_name']}' is not from filtered set"

    def test_show_all_clears_filter_after_pagination(self):
        """'Show all' after filtered pagination resets to full results."""
        sid = _fresh()
        services = _build_shelter_results(15)["services"]
        for i in range(8):
            services[i]["service_taxonomies"] = ["Shelter", "Families"]
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
            "_last_results": services,
            "_displayed_count": 5,
        })
        _send("ones for families", sid)
        result = _send("show all results", sid)
        slots = get_session_slots(sid)
        assert slots.get("_filtered_results") is None, \
            "Filter should be cleared"
        assert len(result["services"]) == 5, \
            "Should show first page of full results"

    def test_subsequent_question_uses_full_filtered_set(self):
        """'Which is open?' after filter uses ALL filtered matches, not just displayed page."""
        sid = _fresh()
        services = _build_shelter_results(15)["services"]
        # 8 family shelters, 3 of which are open (including one past page boundary)
        for i in range(8):
            services[i]["service_taxonomies"] = ["Shelter", "Families"]
            services[i]["is_open"] = "open" if i in (0, 2, 6) else "closed"
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
            "_last_results": services,
            "_displayed_count": 5,
        })
        _send("ones for families", sid)  # displays 5 of 8, but stores all 8
        result = _send("which are open", sid)
        # Should find 3 open across ALL 8 filtered, not just the displayed 5
        assert len(result["services"]) == 3, \
            f"Should find 3 open in full filtered set, got {len(result['services'])}"


# =======================================================================
# SERVICE_TAXONOMIES: card field present
# =======================================================================

class TestServiceTaxonomiesField:
    """Verify format_service_card includes service_taxonomies."""

    def test_format_includes_taxonomies(self):
        from app.rag.query_templates import format_service_card
        row = {
            "service_id": "1",
            "service_name": "Test",
            "service_description": "Test service",
            "organization_name": "Test Org",
            "address": "123 Main St",
            "city": "Brooklyn",
            "state": "NY",
            "zip_code": "11201",
            "number": None,
            "extension": None,
            "today_opens": None,
            "today_closes": None,
            "requires_membership": False,
            "last_validated_at": None,
            "also_available": None,
            "accessibility_info": None,
            "eligibility_rules": None,
            "review_highlight": None,
            "required_documents": None,
            "languages_spoken": None,
            "location_slug": "test",
            "location_id": "loc-1",
            "location_name": "Test Location",
            "organization_url": None,
            "service_url": None,
            "service_email": None,
            "fees": None,
            "phone": None,
            "phone_extension": None,
            # The new field:
            "service_taxonomies": ["Shelter", "Families", "Intake"],
        }
        card = format_service_card(row)
        assert "service_taxonomies" in card
        assert card["service_taxonomies"] == ["Shelter", "Families", "Intake"]

    def test_format_null_taxonomies(self):
        from app.rag.query_templates import format_service_card
        row = {
            "service_id": "1", "service_name": "Test",
            "service_description": None, "organization_name": None,
            "address": None, "city": None, "state": None, "zip_code": None,
            "number": None, "extension": None,
            "today_opens": None, "today_closes": None,
            "requires_membership": False, "last_validated_at": None,
            "also_available": None, "accessibility_info": None,
            "eligibility_rules": None, "review_highlight": None,
            "required_documents": None, "languages_spoken": None,
            "location_slug": None, "location_id": None,
            "location_name": None, "organization_url": None,
            "service_url": None, "service_email": None,
            "fees": None, "phone": None, "phone_extension": None,
            "service_taxonomies": None,
        }
        card = format_service_card(row)
        assert card["service_taxonomies"] is None


# =======================================================================
# EXISTING TEST UPDATES NEEDED
# =======================================================================
# NOTE: test_service_card_display.py has tests that assert <= 10 and == 10
# for display limits. These must be updated to 5 alongside the code change:
#
#   test_initial_results_capped_at_display_limit: assert <= 10 → <= 5
#   test_show_more_returns_remaining: assert == 10 → == 5
#   test_show_more_patterns_recognized: _displayed_count: 10 → 5
#   test_show_all_returns_everything: _displayed_count: 15 → unchanged
#     (show all still returns all)
#
# Also: conftest.py MOCK_SERVICE_CARD should gain service_taxonomies field.
