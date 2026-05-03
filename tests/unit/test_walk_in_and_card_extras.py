"""
Tests for walk-in filter, required documents, and languages on cards.

Covers:
  Gap 10: Requirement filter ("walk-in only")
    - _extract_no_requirements: phrase detection, false negatives
    - Filter wiring through query_services
  Gap 11: Required documents on service cards
    - _clean_list helper
    - format_service_card includes required_documents
  Gap 12: Languages spoken on service cards
    - format_service_card includes languages
    - SQL structure checks

Run with: python -m pytest tests/unit/test_walk_in_and_card_extras.py -v
"""

from unittest.mock import patch

from app.rag.query_templates import (
    format_service_card,
    _clean_list,
    _BASE_QUERY,
)
from app.services.slot_extraction_regex import (
    _extract_no_requirements,
    extract_slots,
)


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
        "required_documents": None,
        "languages_spoken": None,
    }
    base.update(overrides)
    return base


# -----------------------------------------------------------------------
# GAP 10: WALK-IN / NO-REQUIREMENTS FILTER
# -----------------------------------------------------------------------

class TestExtractNoRequirements:
    """Test _extract_no_requirements phrase detection."""

    def test_walk_in_phrases(self):
        assert _extract_no_requirements("walk-in only shelters in Brooklyn") is True
        assert _extract_no_requirements("I need a walk in only clinic") is True

    def test_no_referral_phrases(self):
        assert _extract_no_requirements("no referral needed") is True
        assert _extract_no_requirements("I need food without referral") is True
        assert _extract_no_requirements("don't need a referral") is True

    def test_no_appointment_phrases(self):
        assert _extract_no_requirements("no appointment needed") is True
        assert _extract_no_requirements("without appointment please") is True
        assert _extract_no_requirements("don't need an appointment") is True

    def test_open_to_anyone(self):
        assert _extract_no_requirements("open to anyone services") is True
        assert _extract_no_requirements("open to all") is True

    def test_no_membership(self):
        assert _extract_no_requirements("no membership required") is True
        assert _extract_no_requirements("no registration needed") is True

    def test_negative_cases(self):
        """Regular messages should NOT trigger no_requirements."""
        assert _extract_no_requirements("I need food in Brooklyn") is False
        assert _extract_no_requirements("shelter near me") is False
        assert _extract_no_requirements("hello") is False
        assert _extract_no_requirements("") is False
        assert _extract_no_requirements("I need a walk-in clinic") is False  # service type, not requirement
        assert _extract_no_requirements("the appointment was good") is False

    def test_extract_slots_includes_no_requirements(self):
        slots = extract_slots("walk-in only shelter in Brooklyn")
        assert slots["no_requirements"] is True

    def test_extract_slots_default_false(self):
        slots = extract_slots("I need food in Brooklyn")
        assert slots["no_requirements"] is False


class TestNoRequirementsRouting:
    """Test no_requirements flows through query_services."""

    def test_no_requirements_adds_param(self):
        from app.rag import query_services

        with patch("app.rag.execute_service_query") as mock_exec:
            mock_exec.return_value = {
                "services": [], "result_count": 0,
                "template_used": "test", "params_applied": {},
                "relaxed": False, "execution_ms": 0,
            }
            query_services(
                service_type="shelter",
                location="Brooklyn",
                no_requirements=True,
            )
            params = mock_exec.call_args.kwargs["user_params"]
            assert params.get("no_requirements") is True

    def test_no_requirements_false_omits_param(self):
        from app.rag import query_services

        with patch("app.rag.execute_service_query") as mock_exec:
            mock_exec.return_value = {
                "services": [], "result_count": 0,
                "template_used": "test", "params_applied": {},
                "relaxed": False, "execution_ms": 0,
            }
            query_services(
                service_type="shelter",
                location="Brooklyn",
                no_requirements=False,
            )
            params = mock_exec.call_args.kwargs["user_params"]
            assert "no_requirements" not in params


# -----------------------------------------------------------------------
# GAP 11: REQUIRED DOCUMENTS
# -----------------------------------------------------------------------

class TestCleanList:
    """Test _clean_list helper used for required_documents and languages."""

    def test_none_input(self):
        assert _clean_list(None) is None

    def test_empty_list(self):
        assert _clean_list([]) is None

    def test_all_none_values(self):
        assert _clean_list([None, None]) is None

    def test_filters_none_string(self):
        assert _clean_list(["State ID", "None", "Proof of address"]) == ["State ID", "Proof of address"]

    def test_filters_empty_strings(self):
        assert _clean_list(["State ID", "", "  "]) == ["State ID"]

    def test_normal_list(self):
        assert _clean_list(["State ID", "Proof of address"]) == ["State ID", "Proof of address"]

    def test_single_item(self):
        assert _clean_list(["Photo ID"]) == ["Photo ID"]


class TestRequiredDocumentsOnCards:
    """Test required_documents flows through format_service_card."""

    def test_card_with_documents(self):
        card = format_service_card(_mock_row(
            required_documents=["State ID", "Proof of address"]
        ))
        assert card["required_documents"] == ["State ID", "Proof of address"]

    def test_card_without_documents(self):
        card = format_service_card(_mock_row(required_documents=None))
        assert card["required_documents"] is None

    def test_card_filters_none_documents(self):
        card = format_service_card(_mock_row(
            required_documents=["State ID", "None", None]
        ))
        assert card["required_documents"] == ["State ID"]

    def test_card_empty_documents_list(self):
        card = format_service_card(_mock_row(required_documents=[]))
        assert card["required_documents"] is None


# -----------------------------------------------------------------------
# GAP 12: LANGUAGES SPOKEN
# -----------------------------------------------------------------------

class TestLanguagesOnCards:
    """Test languages flows through format_service_card."""

    def test_card_with_languages(self):
        card = format_service_card(_mock_row(
            languages_spoken=["English", "Spanish", "Mandarin"]
        ))
        assert card["languages"] == ["English", "Spanish", "Mandarin"]

    def test_card_without_languages(self):
        card = format_service_card(_mock_row(languages_spoken=None))
        assert card["languages"] is None

    def test_card_filters_none_languages(self):
        card = format_service_card(_mock_row(
            languages_spoken=["English", None, "Spanish"]
        ))
        assert card["languages"] == ["English", "Spanish"]

    def test_card_empty_languages_list(self):
        card = format_service_card(_mock_row(languages_spoken=[]))
        assert card["languages"] is None


# -----------------------------------------------------------------------
# SQL STRUCTURE CHECKS
# -----------------------------------------------------------------------

class TestBaseQueryNewFields:
    """Verify _BASE_QUERY includes all Gap 10-12 fields."""

    def test_required_documents_selected(self):
        assert "required_documents" in _BASE_QUERY

    def test_languages_spoken_selected(self):
        assert "languages_spoken" in _BASE_QUERY
        assert "service_languages" in _BASE_QUERY

    def test_no_requirements_filter_exists(self):
        from app.rag.query_templates import FILTER_BY_NO_REQUIREMENTS
        sql_fragment, param_keys = FILTER_BY_NO_REQUIREMENTS
        assert "membership" in sql_fragment
        assert "no_requirements" in param_keys
