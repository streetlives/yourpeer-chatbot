"""
Filter pipeline, helpers, state management, and monitoring tests.

Covers: text search, taxonomy matching, co-located filtering, keyword
        extraction, response templates, handler dispatch, state management
        (never-mind, frustration, new-search, reset), monitoring.

Run with: python -m pytest tests/unit/test_filter_pipeline.py -v
"""

from app.services.post_results import (
    classify_post_results_question,
    answer_from_results,
    record_filter_event,
    get_filter_stats,
    _filter_events,
    _extract_raw_phrase,
    _extract_keywords,
    _filter_by_taxonomy,
    _filter_by_colocated,
    _text_search_cards,
    _handle_filter_subcategory,
    _has_taxonomy,
    _elig_contains,
    _lang_contains,
    _desc_contains,
)
from app.services.session_store import get_session_slots, save_session_slots
from conftest import MOCK_SERVICE_CARD

from test_helpers import _fresh, _send, _build_shelter_results


# =======================================================================
# CORE FILTERING FUNCTION UNIT TESTS
# =======================================================================

class TestExtractRawPhrase:
    """Unit tests for _extract_raw_phrase."""

    def test_strips_positive_intent(self):
        from app.services.post_results import _extract_raw_phrase
        assert _extract_raw_phrase("Only the adult families intake") == "adult families intake"

    def test_strips_negation_intent(self):
        from app.services.post_results import _extract_raw_phrase
        assert _extract_raw_phrase("Exclude the veterans shelter") == "veterans"

    def test_strips_trailing_filler(self):
        from app.services.post_results import _extract_raw_phrase
        result = _extract_raw_phrase("Only the intake is relevant")
        assert "relevant" not in result.lower()
        assert "intake" in result.lower()

    def test_strips_trailing_result_words(self):
        from app.services.post_results import _extract_raw_phrase
        assert _extract_raw_phrase("Not the DHS ones") == "DHS"

    def test_returns_original_if_nothing_left(self):
        from app.services.post_results import _extract_raw_phrase
        result = _extract_raw_phrase("only the")
        assert len(result) > 0  # should not be empty


class TestExtractKeywords:
    """Unit tests for _extract_keywords."""

    def test_removes_stop_words(self):
        from app.services.post_results import _extract_keywords
        kw = _extract_keywords("the adult families intake")
        assert "the" not in kw
        assert "adult" in kw
        assert "families" in kw
        assert "intake" in kw

    def test_removes_short_words(self):
        from app.services.post_results import _extract_keywords
        kw = _extract_keywords("no id needed")
        # "id" has length 2, "no" is a stop word
        assert "id" not in kw
        assert "needed" in kw

    def test_empty_input(self):
        from app.services.post_results import _extract_keywords
        assert _extract_keywords("") == []

    def test_all_stop_words(self):
        from app.services.post_results import _extract_keywords
        assert _extract_keywords("the ones for me") == []


class TestFilterByTaxonomy:
    """Unit tests for _filter_by_taxonomy."""

    def test_single_word_alias(self):
        from app.services.post_results import _filter_by_taxonomy
        cards = [
            {"service_taxonomies": ["Shelter", "Families"]},
            {"service_taxonomies": ["Shelter", "Single Adult"]},
        ]
        matched, taxes = _filter_by_taxonomy(cards, "families")
        assert len(matched) == 1
        assert "Families" in taxes

    def test_bigram_alias(self):
        from app.services.post_results import _filter_by_taxonomy
        cards = [
            {"service_taxonomies": ["Shelter", "Single Adult"]},
            {"service_taxonomies": ["Shelter", "Families"]},
        ]
        matched, taxes = _filter_by_taxonomy(cards, "single adult shelters")
        assert len(matched) == 1
        assert "Single Adult" in taxes

    def test_multiple_taxonomies_matched(self):
        from app.services.post_results import _filter_by_taxonomy
        cards = [
            {"service_taxonomies": ["Shelter", "Families", "Intake"]},
            {"service_taxonomies": ["Shelter", "Intake"]},
            {"service_taxonomies": ["Shelter"]},
        ]
        matched, taxes = _filter_by_taxonomy(cards, "families intake")
        assert len(matched) == 2  # first two cards match
        assert "Families" in taxes
        assert "Intake" in taxes

    def test_no_match(self):
        from app.services.post_results import _filter_by_taxonomy
        cards = [{"service_taxonomies": ["Shelter"]}]
        matched, taxes = _filter_by_taxonomy(cards, "dental care")
        assert len(matched) == 0
        assert len(taxes) == 0

    def test_null_taxonomies_skipped(self):
        from app.services.post_results import _filter_by_taxonomy
        cards = [
            {"service_taxonomies": None},
            {"service_taxonomies": ["Shelter", "Families"]},
        ]
        matched, _ = _filter_by_taxonomy(cards, "families")
        assert len(matched) == 1


class TestFilterByColocated:
    """Unit tests for _filter_by_colocated."""

    def test_also_has_food(self):
        from app.services.post_results import _filter_by_colocated
        cards = [
            {"also_available": ["Food", "Health"]},
            {"also_available": ["Shower"]},
            {"also_available": None},
        ]
        matched, desc = _filter_by_colocated(cards, "also has food")
        assert len(matched) == 1
        assert "food" in desc.lower()

    def test_with_showers_plural(self):
        from app.services.post_results import _filter_by_colocated
        cards = [
            {"also_available": ["Shower", "Laundry"]},
            {"also_available": ["Food"]},
        ]
        matched, _ = _filter_by_colocated(cards, "with showers")
        assert len(matched) == 1

    def test_no_match(self):
        from app.services.post_results import _filter_by_colocated
        cards = [{"also_available": ["Food"]}]
        matched, _ = _filter_by_colocated(cards, "something else entirely")
        assert len(matched) == 0


# =======================================================================
# EDGE CASE FIXES (design doc section 6)
# =======================================================================

class TestAddressCityTextSearch:
    """6.3/6.5: Text search includes address and city fields."""

    def test_text_search_finds_by_city(self):
        from app.services.post_results import _text_search_cards
        cards = [
            {"service_name": "Shelter A", "description": "", "organization": "",
             "address": "400 E 30th St", "city": "Manhattan"},
            {"service_name": "Shelter B", "description": "", "organization": "",
             "address": "151 E 151st St", "city": "Bronx"},
        ]
        matched = _text_search_cards(cards, ["bronx"])
        assert len(matched) == 1
        assert matched[0]["service_name"] == "Shelter B"

    def test_text_search_finds_by_address(self):
        from app.services.post_results import _text_search_cards
        cards = [
            {"service_name": "Shelter A", "description": "", "organization": "",
             "address": "1000 Blake Ave, Brooklyn", "city": "Brooklyn"},
            {"service_name": "Shelter B", "description": "", "organization": "",
             "address": "400 E 30th St", "city": "Manhattan"},
        ]
        matched = _text_search_cards(cards, ["blake"])
        assert len(matched) == 1
        assert matched[0]["service_name"] == "Shelter A"

    def test_negation_excludes_by_city(self):
        """'Anywhere but the Bronx' → text search 'bronx', then invert."""
        from app.services.post_results import _text_search_cards
        cards = [
            {"service_id": "1", "service_name": "A", "description": "",
             "organization": "", "address": "", "city": "Manhattan"},
            {"service_id": "2", "service_name": "B", "description": "",
             "organization": "", "address": "", "city": "Bronx"},
            {"service_id": "3", "service_name": "C", "description": "",
             "organization": "", "address": "", "city": "Brooklyn"},
        ]
        matched = _text_search_cards(cards, ["bronx"])
        matched_ids = {c["service_id"] for c in matched}
        inverted = [c for c in cards if c["service_id"] not in matched_ids]
        assert len(inverted) == 2
        assert all(c["city"] != "Bronx" for c in inverted)


class TestResponseTemplates:
    """Verify response wording for each filter outcome."""

    def test_match_banner_wording(self):
        from app.services.post_results import _handle_filter_subcategory
        cards = _build_shelter_results(10)["services"]
        for i in range(3):
            cards[i]["service_taxonomies"] = ["Shelter", "Families"]
        result = _handle_filter_subcategory(
            {"raw_phrase": "families", "_original_message": "ones for families"},
            cards,
        )
        assert "3" in result["response"]
        assert "10" in result["response"]
        assert "matching" in result["response"].lower()

    def test_single_match_wording(self):
        from app.services.post_results import _handle_filter_subcategory
        cards = _build_shelter_results(10)["services"]
        cards[0]["service_taxonomies"] = ["Shelter", "Veterans"]
        result = _handle_filter_subcategory(
            {"raw_phrase": "veterans", "_original_message": "ones for veterans"},
            cards,
        )
        assert "one of the" in result["response"].lower()

    def test_no_match_wording(self):
        from app.services.post_results import _handle_filter_subcategory
        cards = _build_shelter_results(5)["services"]
        result = _handle_filter_subcategory(
            {"raw_phrase": "dental care", "_original_message": "dental care"},
            cards,
        )
        assert "none of the" in result["response"].lower()
        assert result["_filter_matched"] is False

    def test_sparse_guard(self):
        from app.services.post_results import _handle_filter_subcategory
        cards = _build_shelter_results(2)["services"]
        result = _handle_filter_subcategory(
            {"raw_phrase": "anything", "_original_message": "anything"},
            cards,
        )
        assert "not much to filter" in result["response"].lower()


class TestNegationResponseWording:
    """Negation filter response must contain 'excluding'."""

    def test_negation_response_says_excluding(self):
        from app.services.post_results import _handle_filter_subcategory
        cards = _build_shelter_results(10)["services"]
        # Give card 0 a distinctive org name
        cards[0]["organization"] = "DHS"
        cards[0]["service_name"] = "DHS Shelter"
        result = _handle_filter_subcategory(
            {"raw_phrase": "DHS", "_original_message": "exclude DHS",
             "_is_negation": True},
            cards,
        )
        assert "excluding" in result["response"].lower(), \
            f"Negation response should say 'excluding', got: {result['response']}"
        # Should return cards WITHOUT DHS
        for svc in result.get("services", []):
            assert "DHS" not in svc.get("organization", ""), \
                f"DHS card should be excluded: {svc['service_name']}"


class TestHelperFunctions:
    """Direct tests for card-field helper functions."""

    def test_has_taxonomy(self):
        from app.services.post_results import _has_taxonomy
        card = {"service_taxonomies": ["Shelter", "Families", "Intake"]}
        assert _has_taxonomy(card, "Families") is True
        assert _has_taxonomy(card, "Veterans") is False
        assert _has_taxonomy({"service_taxonomies": None}, "Families") is False
        assert _has_taxonomy({}, "Families") is False

    def test_elig_contains(self):
        from app.services.post_results import _elig_contains
        card = {"eligibility_summary": "Ages 18-24, Women only"}
        assert _elig_contains(card, "women") is True
        assert _elig_contains(card, "men") is True  # substring of "women"
        assert _elig_contains(card, "veterans") is False
        assert _elig_contains({"eligibility_summary": None}, "women") is False

    def test_lang_contains(self):
        from app.services.post_results import _lang_contains
        card = {"languages": ["English", "Spanish"]}
        assert _lang_contains(card, "Spanish") is True
        assert _lang_contains(card, "French") is False
        assert _lang_contains({"languages": None}, "Spanish") is False

    def test_desc_contains(self):
        from app.services.post_results import _desc_contains
        card = {"description": "Free meals and showers available daily"}
        assert _desc_contains(card, "showers") is True
        assert _desc_contains(card, "meals", "laundry") is True
        assert _desc_contains(card, "legal") is False
        assert _desc_contains({"description": None}, "anything") is False


class TestAnswerFromResultsDispatch:
    """answer_from_results routes to correct handlers."""

    def test_dispatches_filter_subcategory(self):
        from app.services.post_results import answer_from_results
        cards = _build_shelter_results(5)["services"]
        cards[0]["service_taxonomies"] = ["Shelter", "Families"]
        result = answer_from_results(
            {"type": "filter_subcategory", "raw_phrase": "families",
             "_original_message": "ones for families"},
            cards,
        )
        assert result is not None
        assert result.get("category") == "post_results_filter"

    def test_dispatches_filter_open(self):
        from app.services.post_results import answer_from_results
        cards = _build_shelter_results(3)["services"]
        cards[0]["is_open"] = "open"
        result = answer_from_results({"type": "filter_open"}, cards)
        assert result is not None
        assert len(result["services"]) >= 1

    def test_dispatches_filter_free(self):
        from app.services.post_results import answer_from_results
        cards = _build_shelter_results(3)["services"]
        result = answer_from_results({"type": "filter_free"}, cards)
        assert result is not None

    def test_legacy_refine_results_compat(self):
        from app.services.post_results import answer_from_results
        cards = _build_shelter_results(5)["services"]
        result = answer_from_results({"type": "refine_results"}, cards)
        assert result is not None
        assert result.get("category") == "post_results_filter"

    def test_empty_services_returns_cant_answer(self):
        from app.services.post_results import answer_from_results
        result = answer_from_results(
            {"type": "filter_subcategory", "raw_phrase": "test"}, []
        )
        assert result is not None
        assert "don't have any results" in result["response"].lower()


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


class TestNeverMindAfterFilter:
    """6.4: 'Never mind' after filtering clears filter only, preserves results."""

    def test_no_thanks_with_filter_shows_full_results(self):
        sid = _fresh()
        services = _build_shelter_results(10)["services"]
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
            "_last_results": services,
            "_filtered_results": services[:3],
            "_filter_phrase": "families",
            "_displayed_count": 3,
        })
        result = _send("no thanks", sid)
        slots = get_session_slots(sid)
        assert slots.get("_filtered_results") is None, \
            "Filter should be cleared"
        assert slots.get("_last_results") is not None, \
            "_last_results must survive for recovery"
        # Should show full results, not welcome screen
        assert len(result.get("services", [])) > 0, \
            "Should re-display full results, not empty welcome"

    def test_no_thanks_without_filter_clears_everything(self):
        sid = _fresh()
        services = _build_shelter_results(10)["services"]
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
            "_last_results": services,
            "_displayed_count": 5,
        })
        result = _send("no thanks", sid)
        slots = get_session_slots(sid)
        assert slots.get("_last_results") is None, \
            "Without filter, _last_results should be cleared"


class TestFrustrationPreservesResults:
    """6.9: Frustration after filtering clears filter but keeps _last_results."""

    def test_frustration_with_filter_preserves_last_results(self):
        sid = _fresh()
        services = _build_shelter_results(10)["services"]
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
            "_last_results": services,
            "_filtered_results": services[:3],
            "_filter_phrase": "families",
            "_displayed_count": 3,
        })
        _send("this is useless", sid)
        slots = get_session_slots(sid)
        assert slots.get("_filtered_results") is None, \
            "Filter should be cleared on frustration"
        assert slots.get("_last_results") is not None, \
            "_last_results must survive for 'show all results' recovery"

    def test_show_all_works_after_frustration(self):
        """User can recover with 'show all results' after frustration."""
        sid = _fresh()
        services = _build_shelter_results(10)["services"]
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
            "_last_results": services,
            "_filtered_results": services[:3],
            "_filter_phrase": "families",
            "_displayed_count": 3,
        })
        _send("this is useless", sid)  # frustration — clears filter, keeps results
        result = _send("show all results", sid)  # should show full results
        assert len(result.get("services", [])) > 0, \
            "Should show results after frustration recovery"

    def test_frustration_without_filter_clears_everything(self):
        """Without filter, frustration still clears _last_results (existing behavior)."""
        sid = _fresh()
        services = _build_shelter_results(10)["services"]
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
            "_last_results": services,
            "_displayed_count": 5,
        })
        _send("this is useless", sid)
        slots = get_session_slots(sid)
        assert slots.get("_last_results") is None, \
            "Without filter, _last_results should still be cleared"


# =======================================================================
# SERVICE_TAXONOMIES: card field present
# =======================================================================


class TestNewSearchClearsFilter:
    """State management: new search clears both _last_results and _filtered_results."""

    def test_new_service_intent_clears_filter(self):
        sid = _fresh()
        services = _build_shelter_results(10)["services"]
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
            "_last_results": services,
            "_filtered_results": services[:3],
            "_filter_phrase": "families",
            "_displayed_count": 3,
        })
        result = _send("I need food in Brooklyn", sid)
        slots = get_session_slots(sid)
        assert slots.get("_filtered_results") is None
        assert slots.get("_filter_phrase") is None

    def test_reset_clears_filter(self):
        sid = _fresh()
        services = _build_shelter_results(10)["services"]
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
            "_last_results": services,
            "_filtered_results": services[:3],
            "_filter_phrase": "families",
        })
        _send("start over", sid)
        slots = get_session_slots(sid)
        assert slots.get("_filtered_results") is None
        assert slots.get("_last_results") is None


# =======================================================================
# GAP-FILLING: Regex bug fix guards, response wording, helpers, dispatch
# =======================================================================


# =======================================================================
# FILTER MONITORING
# =======================================================================

class TestFilterMonitoring:
    """Miss-rate tracking for filter_subcategory events."""

    def test_get_filter_stats_empty(self):
        from app.services.post_results import get_filter_stats, _filter_events
        _filter_events.clear()
        stats = get_filter_stats()
        assert stats["total_events"] == 0
        assert stats["miss_rate"] == 0.0
        assert stats["above_threshold"] is False

    def test_record_and_retrieve(self):
        from app.services.post_results import (
            record_filter_event, get_filter_stats, _filter_events,
        )
        _filter_events.clear()
        record_filter_event(tier="taxonomy", phrase="families",
                           match_count=3, total=10)
        record_filter_event(tier="text_search", phrase="weird thing",
                           match_count=0, total=10)
        record_filter_event(tier="structured", phrase="no referral",
                           match_count=5, total=10)

        stats = get_filter_stats()
        assert stats["total_events"] == 3
        assert stats["misses"] == 1
        assert abs(stats["miss_rate"] - 0.333) < 0.01
        assert stats["above_threshold"] is True  # 33% > 15%
        assert "weird thing" in stats["recent_misses"]

    def test_by_tier_breakdown(self):
        from app.services.post_results import (
            record_filter_event, get_filter_stats, _filter_events,
        )
        _filter_events.clear()
        record_filter_event(tier="taxonomy", phrase="a", match_count=2, total=10)
        record_filter_event(tier="taxonomy", phrase="b", match_count=0, total=10)
        record_filter_event(tier="text_search", phrase="c", match_count=1, total=10)

        stats = get_filter_stats()
        assert stats["by_tier"]["taxonomy"]["total"] == 2
        assert stats["by_tier"]["taxonomy"]["misses"] == 1
        assert stats["by_tier"]["text_search"]["total"] == 1
        assert stats["by_tier"]["text_search"]["misses"] == 0

    def test_below_threshold(self):
        from app.services.post_results import (
            record_filter_event, get_filter_stats, _filter_events,
        )
        _filter_events.clear()
        # 1 miss out of 10 = 10% < 15%
        for i in range(9):
            record_filter_event(tier="taxonomy", phrase=f"hit-{i}",
                               match_count=2, total=10)
        record_filter_event(tier="text_search", phrase="miss",
                           match_count=0, total=10)

        stats = get_filter_stats()
        assert stats["miss_rate"] == 0.1
        assert stats["above_threshold"] is False


# =======================================================================
# CORE FILTERING FUNCTION UNIT TESTS
# =======================================================================


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
