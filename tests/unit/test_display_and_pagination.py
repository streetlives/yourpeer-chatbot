"""
Display and pagination tests.

Covers: loc_label None display, confirm_change_location, service_taxonomies,
        open-right-now regex, display pagination, filtered results pagination.

Run with: python -m pytest tests/unit/test_display_and_pagination.py -v
"""

from app.services.post_results import classify_post_results_question
from app.services.chatbot import _DISPLAY_PAGE_SIZE
from app.services.session_store import get_session_slots, save_session_slots

from test_helpers import _fresh, _send, _build_shelter_results


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
# FILTER MONITORING
# =======================================================================


# =======================================================================
# GAP-FILLING: Regex bug fix guards, response wording, helpers, dispatch
# =======================================================================

class TestOpenRightNowFix:
    """Bug fix: 'open right now' was missed by _FILTER_OPEN_RE."""

    def test_open_right_now_detected(self):
        intent = classify_post_results_question("Are any open right now?")
        assert intent is not None
        assert intent["type"] == "filter_open"

    def test_open_now_still_works(self):
        intent = classify_post_results_question("Which are open now?")
        assert intent is not None
        assert intent["type"] == "filter_open"

    def test_currently_open_still_works(self):
        intent = classify_post_results_question("Any currently open?")
        assert intent is not None
        assert intent["type"] == "filter_open"


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
# COMPOUND FILTERS (6.2)
# =======================================================================
