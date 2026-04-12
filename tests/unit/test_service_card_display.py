"""
Tests for service card display features.

Covers:
  Gap 1:  Review highlights on cards
  Gap 2:  Granular sub-category display (also_available label mapping)
  Gap 4:  Eligibility display (_format_eligibility)
  Gap 5:  Accessibility info on cards
  Gap 7:  Pagination / "show more" (fetch 25, display 10, show more handler)
  Gap 13: Phone extensions (_format_phone)
  SQL:    _BASE_QUERY field presence checks

Run with: python -m pytest tests/unit/test_service_card_display.py -v
"""

import uuid
from unittest.mock import patch

from app.rag.query_templates import (
    format_service_card,
    _format_phone,
    _format_eligibility,
    _BASE_QUERY,
)
from app.services.session_store import clear_session, save_session_slots
from app.services.chatbot import generate_reply


# -----------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------

def _mock_row(**overrides):
    """Build a mock DB result row with all current fields."""
    base = {
        "service_id": "test-uuid-123",
        "service_name": "Test Food Pantry",
        "service_description": "Free food distribution",
        "fees": "Free",
        "service_url": "https://example.com",
        "service_email": "info@example.com",
        "organization_name": "Test Org",
        "organization_url": "https://testorg.com",
        "location_id": "loc-uuid-456",
        "location_name": "Main Office",
        "location_slug": "test-food-pantry-brooklyn",
        "address": "123 Main Street",
        "city": "Brooklyn",
        "state": "NY",
        "zip_code": "11201",
        "phone": "212-555-0001",
        "phone_extension": None,
        "today_opens": None,
        "today_closes": None,
        "requires_membership": None,
        "last_validated_at": None,
        "also_available": None,
        "accessibility_info": None,
        "eligibility_rules": None,
        "review_highlight": None,
    }
    base.update(overrides)
    return base


# -----------------------------------------------------------------------
# GAP 13: PHONE EXTENSIONS
# -----------------------------------------------------------------------

class TestFormatPhone:
    """Test _format_phone helper."""

    def test_none_number(self):
        assert _format_phone(None, None) is None

    def test_number_no_extension(self):
        assert _format_phone("212-555-1234", None) == "212-555-1234"

    def test_number_empty_extension(self):
        assert _format_phone("212-555-1234", "") == "212-555-1234"

    def test_number_none_string_extension(self):
        assert _format_phone("212-555-1234", "None") == "212-555-1234"

    def test_number_na_extension(self):
        assert _format_phone("212-555-1234", "n/a") == "212-555-1234"

    def test_number_with_extension(self):
        assert _format_phone("212-555-1234", "456") == "212-555-1234 ext. 456"

    def test_number_with_whitespace_extension(self):
        assert _format_phone("212-555-1234", " 456 ") == "212-555-1234 ext. 456"

    def test_card_includes_extension(self):
        card = format_service_card(_mock_row(phone="212-555-1234", phone_extension="789"))
        assert card["phone"] == "212-555-1234 ext. 789"

    def test_card_no_extension(self):
        card = format_service_card(_mock_row(phone="212-555-1234", phone_extension=None))
        assert card["phone"] == "212-555-1234"


# -----------------------------------------------------------------------
# GAP 4: ELIGIBILITY DISPLAY
# -----------------------------------------------------------------------

class TestFormatEligibility:
    """Test _format_eligibility helper."""

    def test_none_rules(self):
        assert _format_eligibility(None) is None

    def test_empty_list(self):
        assert _format_eligibility([]) is None

    def test_all_ages(self):
        assert _format_eligibility([{"param": "age", "values": [{"all_ages": True}]}]) is None

    def test_age_range(self):
        result = _format_eligibility([{"param": "age", "values": [{"age_min": 18, "age_max": 24}]}])
        assert result == "Ages 18–24"

    def test_age_min_only(self):
        result = _format_eligibility([{"param": "age", "values": [{"age_min": 18}]}])
        assert result == "Ages 18+"

    def test_age_max_only(self):
        result = _format_eligibility([{"param": "age", "values": [{"age_max": 24}]}])
        assert result == "Ages up to 24"

    def test_gender_single(self):
        result = _format_eligibility([{"param": "gender", "values": ["female"]}])
        assert result == "Female only"

    def test_gender_both_open(self):
        assert _format_eligibility([{"param": "gender", "values": ["male", "female"]}]) is None

    def test_combined_age_gender(self):
        result = _format_eligibility([
            {"param": "age", "values": [{"age_min": 18, "age_max": 24}]},
            {"param": "gender", "values": ["female"]},
        ])
        assert result == "Ages 18–24 · Female only"

    def test_family_size(self):
        result = _format_eligibility([{"param": "familySize", "values": [{"min": 2}]}])
        assert result == "Families"

    def test_family_size_one(self):
        assert _format_eligibility([{"param": "familySize", "values": [{"min": 1}]}]) is None

    def test_unknown_param_skipped(self):
        assert _format_eligibility([{"param": "income", "values": [{"max": 50000}]}]) is None

    def test_null_values(self):
        assert _format_eligibility([{"param": "age", "values": None}]) is None

    def test_empty_values_list(self):
        assert _format_eligibility([{"param": "age", "values": []}]) is None

    def test_card_includes_eligibility(self):
        card = format_service_card(_mock_row(
            eligibility_rules=[{"param": "age", "values": [{"age_min": 18, "age_max": 24}]}]
        ))
        assert card["eligibility_summary"] == "Ages 18–24"

    def test_card_no_eligibility(self):
        card = format_service_card(_mock_row(eligibility_rules=None))
        assert card["eligibility_summary"] is None


# -----------------------------------------------------------------------
# GAP 2: GRANULAR SUB-CATEGORY DISPLAY
# -----------------------------------------------------------------------

class TestAlsoAvailableLabels:
    """Test also_available label mapping and filtering."""

    def test_raw_taxonomy_names_mapped(self):
        card = format_service_card(_mock_row(also_available=[
            "Substance Use Treatment", "Clothing Pantry", "General Health",
        ]))
        also = card["also_available"]
        assert "Substance Use Help" in also
        assert "Clothing" in also
        assert "Health" in also
        assert "Substance Use Treatment" not in also
        assert "Clothing Pantry" not in also
        assert "General Health" not in also

    def test_other_service_excluded(self):
        card = format_service_card(_mock_row(also_available=["Other service", "Food"]))
        assert "Other service" not in card["also_available"]
        assert "Food" in card["also_available"]

    def test_granular_names_preserved(self):
        card = format_service_card(_mock_row(also_available=[
            "Shower", "Laundry", "Immigration Services", "Food Pantry",
        ]))
        also = card["also_available"]
        assert "Shower" in also
        assert "Laundry" in also
        assert "Immigration Services" in also
        assert "Food Pantry" in also

    def test_deduplication(self):
        card = format_service_card(_mock_row(also_available=[
            "Clothing", "Clothing Pantry",
        ]))
        assert card["also_available"].count("Clothing") == 1

    def test_empty_also_available(self):
        card = format_service_card(_mock_row(also_available=[]))
        assert card["also_available"] is None

    def test_none_also_available(self):
        card = format_service_card(_mock_row(also_available=None))
        assert card["also_available"] is None


# -----------------------------------------------------------------------
# GAP 5: ACCESSIBILITY INFO
# -----------------------------------------------------------------------

class TestAccessibilityOnCards:
    """Test accessibility data on service cards."""

    def test_card_with_accessibility(self):
        card = format_service_card(_mock_row(accessibility_info="Wheelchair accessible"))
        assert card["accessibility"] == "Wheelchair accessible"

    def test_card_without_accessibility(self):
        card = format_service_card(_mock_row(accessibility_info=None))
        assert card["accessibility"] is None


# -----------------------------------------------------------------------
# GAP 1: REVIEW HIGHLIGHTS
# -----------------------------------------------------------------------

class TestReviewHighlights:
    """Test review highlights on service cards."""

    def test_card_with_highlight(self):
        card = format_service_card(_mock_row(
            review_highlight="very friendly and lgbtq safe space"
        ))
        assert card["review_highlight"] == "very friendly and lgbtq safe space"

    def test_card_without_highlight(self):
        card = format_service_card(_mock_row(review_highlight=None))
        assert card["review_highlight"] is None

    def test_review_highlight_in_base_query(self):
        assert "review_highlight" in _BASE_QUERY
        assert "location_comment_highlights" in _BASE_QUERY


# -----------------------------------------------------------------------
# SQL STRUCTURE CHECKS
# -----------------------------------------------------------------------

class TestBaseQueryStructure:
    """Verify _BASE_QUERY includes all new fields."""

    def test_phone_extension_selected(self):
        assert "phone_extension" in _BASE_QUERY

    def test_eligibility_rules_selected(self):
        assert "eligibility_rules" in _BASE_QUERY

    def test_review_highlight_selected(self):
        assert "review_highlight" in _BASE_QUERY
        assert "location_comment_highlights" in _BASE_QUERY

    def test_accessibility_selected(self):
        assert "accessibility_info" in _BASE_QUERY
        assert "accessibility_for_disabilities" in _BASE_QUERY


# -----------------------------------------------------------------------
# GAP 7: PAGINATION / "SHOW MORE"
# -----------------------------------------------------------------------

class TestShowMore:
    """Test fetch-25-display-10 pagination and show more handler."""

    def _build_many_results(self, n):
        """Build a mock query result with n services."""
        services = []
        for i in range(n):
            services.append({
                "service_name": f"Service {i+1}",
                "organization": f"Org {i+1}",
                "address": f"{i+1} Main St, Brooklyn, NY 11201",
                "city": "Brooklyn",
                "phone": f"212-555-{i:04d}",
                "service_id": f"svc-{i+1}",
                "fees": "Free",
                "hours_today": None,
                "is_open": "unknown",
                "yourpeer_url": None,
                "requires_membership": False,
                "last_validated_at": None,
                "also_available": None,
            })
        return {
            "services": services,
            "result_count": n,
            "template_used": "FoodQuery",
            "params_applied": {},
            "relaxed": False,
            "execution_ms": 50,
            "freshness": {"fresh": n, "total": n, "total_with_date": n},
        }

    def test_initial_results_capped_at_display_limit(self):
        from conftest import send_multi
        results = send_multi(
            ["I need food", "Brooklyn", "Yes, search"],
            mock_query_return=self._build_many_results(20),
        )
        final = results[-1]
        assert len(final["services"]) <= 10

    def test_show_more_qr_when_more_exist(self):
        from conftest import send_multi
        results = send_multi(
            ["I need food", "Brooklyn", "Yes, search"],
            mock_query_return=self._build_many_results(20),
        )
        final = results[-1]
        qr_labels = [qr["label"] for qr in final.get("quick_replies", [])]
        assert any("more result" in label.lower() for label in qr_labels), \
            f"Expected 'show more' QR, got: {qr_labels}"

    def test_no_show_more_when_few_results(self):
        from conftest import send_multi
        results = send_multi(
            ["I need food", "Brooklyn", "Yes, search"],
            mock_query_return=self._build_many_results(5),
        )
        final = results[-1]
        qr_labels = [qr["label"] for qr in final.get("quick_replies", [])]
        assert not any("more result" in label.lower() for label in qr_labels)

    def test_show_more_returns_remaining(self):
        from conftest import send_multi
        results = send_multi(
            ["I need food", "Brooklyn", "Yes, search", "Show more results"],
            mock_query_return=self._build_many_results(15),
        )
        assert len(results[2]["services"]) == 10
        show_more = results[3]
        assert len(show_more["services"]) == 5
        assert "more" in show_more["response"].lower()

    def test_show_more_patterns_recognized(self):
        patterns = [
            "show more", "more results", "any others",
            "what else", "show more results", "any more",
        ]
        from app.services.chatbot import generate_reply

        for pattern in patterns:
            sid = f"test-{uuid.uuid4().hex[:8]}"
            clear_session(sid)
            save_session_slots(sid, {
                "service_type": "food",
                "location": "Brooklyn",
                "_last_results": [{"service_name": f"Svc {i}"} for i in range(15)],
                "_displayed_count": 10,
            })
            with patch("app.services.chatbot.claude_reply", return_value=""), \
                 patch("app.services.chatbot.query_services", return_value={"services": [], "result_count": 0}), \
                 patch("app.services.chatbot.detect_crisis", return_value=None):
                result = generate_reply(pattern, session_id=sid)
                assert len(result["services"]) == 5, \
                    f"Pattern '{pattern}' returned {len(result['services'])} services, expected 5"
            clear_session(sid)

    def test_show_all_returns_everything(self):
        sid = f"test-{uuid.uuid4().hex[:8]}"
        clear_session(sid)
        save_session_slots(sid, {
            "service_type": "food",
            "location": "Brooklyn",
            "_last_results": [{"service_name": f"Svc {i}"} for i in range(15)],
            "_displayed_count": 15,
        })
        with patch("app.services.chatbot.claude_reply", return_value=""), \
             patch("app.services.chatbot.query_services", return_value={"services": [], "result_count": 0}), \
             patch("app.services.chatbot.detect_crisis", return_value=None):
            result = generate_reply("show all results", session_id=sid)
            assert len(result["services"]) == 15
        clear_session(sid)
