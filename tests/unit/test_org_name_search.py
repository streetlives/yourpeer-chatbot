"""
Tests for organization name search (Gap 3).

Covers:
  - Org name extraction: known orgs, abbreviations, false positive rejection
  - Slot integration: extract_slots, is_enough_to_answer, merge behavior
  - Confirmation messages: org-only, org+location, org+service
  - Query template: OrgNameQuery exists, uses correct filters
  - Query routing: org_name → org_name template with ILIKE pattern
  - Chatbot integration: full pipeline from message to confirmation

Run with: python -m pytest tests/unit/test_org_name_search.py -v
"""

import uuid
from unittest.mock import patch

from app.services.slot_extraction_regex import (
    _extract_org_name,
    extract_slots,
    is_enough_to_answer,
)
from app.services.confirmation import _build_confirmation_message
from app.rag.query_templates import TEMPLATES


# -----------------------------------------------------------------------
# ORG NAME EXTRACTION
# -----------------------------------------------------------------------

class TestOrgNameExtraction:
    """Test _extract_org_name regex matching."""

    def test_known_orgs_matched(self):
        assert _extract_org_name("tell me about covenant house") == "Covenant House"
        assert _extract_org_name("where is safe horizon") == "Safe Horizon"
        assert _extract_org_name("ali forney center info") == "Ali Forney Center"
        assert _extract_org_name("salvation army in brooklyn") == "Salvation Army"
        assert _extract_org_name("catholic charities programs") == "Catholic Charities"
        assert _extract_org_name("women in need shelter") == "Women In Need"
        assert _extract_org_name("legal aid society near me") == "Legal Aid Society"
        assert _extract_org_name("make the road new york") == "Make the Road New York"

    def test_abbreviations_matched(self):
        assert _extract_org_name("what does YMCA offer") == "YMCA"
        assert _extract_org_name("CAMBA programs") == "CAMBA"
        assert _extract_org_name("DYCD youth centers") == "DYCD"
        assert _extract_org_name("tell me about MRNY") == "Make the Road New York"

    def test_false_positives_rejected(self):
        assert _extract_org_name("I went to the door") is None
        assert _extract_org_name("I need to win") is None
        assert _extract_org_name("on the path to recovery") is None
        assert _extract_org_name("I need a safe haven tonight") is None
        assert _extract_org_name("DHS sent me here") is None
        assert _extract_org_name("legal aid for my case") is None

    def test_no_org_in_regular_message(self):
        assert _extract_org_name("I need food in Brooklyn") is None
        assert _extract_org_name("shelter near me") is None
        assert _extract_org_name("hello") is None
        assert _extract_org_name("") is None

    def test_case_insensitive(self):
        assert _extract_org_name("COVENANT HOUSE") == "Covenant House"
        assert _extract_org_name("safe HORIZON in harlem") == "Safe Horizon"


# -----------------------------------------------------------------------
# SLOT INTEGRATION
# -----------------------------------------------------------------------

class TestOrgNameInSlots:
    """Test org_name flows through extract_slots and is_enough_to_answer."""

    def test_extract_slots_includes_org_name(self):
        slots = extract_slots("tell me about covenant house in manhattan")
        assert slots["org_name"] == "Covenant House"
        assert slots["location"] is not None

    def test_extract_slots_no_org(self):
        slots = extract_slots("I need food in Brooklyn")
        assert slots["org_name"] is None

    def test_is_enough_with_org_name_only(self):
        """org_name alone is sufficient — no location required."""
        assert is_enough_to_answer({"org_name": "Covenant House"}) is True

    def test_is_enough_without_org_needs_location(self):
        """Without org_name, both service_type and location are required."""
        assert is_enough_to_answer({"service_type": "food"}) is False
        assert is_enough_to_answer({"service_type": "food", "location": "Brooklyn"}) is True

    def test_org_name_with_service_type(self):
        """Both org_name and service_type can coexist."""
        slots = extract_slots("I need food at catholic charities")
        assert slots["org_name"] == "Catholic Charities"
        assert slots["service_type"] == "food"


# -----------------------------------------------------------------------
# CONFIRMATION MESSAGES
# -----------------------------------------------------------------------

class TestOrgNameConfirmation:
    """Test _build_confirmation_message with org_name."""

    def test_org_only(self):
        msg = _build_confirmation_message({"org_name": "Covenant House"})
        assert "Covenant House" in msg
        assert "services at" in msg.lower()

    def test_org_with_location(self):
        msg = _build_confirmation_message({
            "org_name": "Safe Horizon",
            "location": "Harlem",
        })
        assert "Safe Horizon" in msg
        assert "Harlem" in msg

    def test_org_with_service_type(self):
        msg = _build_confirmation_message({
            "org_name": "Safe Horizon",
            "service_type": "legal",
            "location": "Manhattan",
        })
        assert "Safe Horizon" in msg
        assert "legal" in msg.lower()

    def test_normal_without_org(self):
        msg = _build_confirmation_message({
            "service_type": "food",
            "location": "Brooklyn",
        })
        assert "org_name" not in msg.lower()
        assert "food" in msg
        assert "Brooklyn" in msg


# -----------------------------------------------------------------------
# QUERY TEMPLATE
# -----------------------------------------------------------------------

class TestOrgNameQueryTemplate:
    """Test the OrgNameQuery template configuration."""

    def test_org_name_template_exists(self):
        assert "org_name" in TEMPLATES

    def test_org_name_template_has_no_taxonomy_filter(self):
        """OrgNameQuery should NOT require taxonomy filters."""
        template = TEMPLATES["org_name"]
        filter_names = [frag[1] for frag in template["required_filters"]]
        assert ["taxonomy_names"] not in filter_names

    def test_org_name_template_uses_org_filter(self):
        """OrgNameQuery should use FILTER_BY_ORG_NAME."""
        template = TEMPLATES["org_name"]
        filter_params = [frag[1] for frag in template["required_filters"]]
        assert ["org_name_pattern"] in filter_params


# -----------------------------------------------------------------------
# QUERY ROUTING
# -----------------------------------------------------------------------

class TestOrgNameRouting:
    """Test org_name routing through query_services."""

    def test_org_name_routes_to_org_template(self):
        """When org_name is set, query_services should use the org_name template."""
        from app.rag import query_services

        with patch("app.rag.execute_service_query") as mock_exec:
            mock_exec.return_value = {
                "services": [], "result_count": 0,
                "template_used": "OrgNameQuery", "params_applied": {},
                "relaxed": False, "execution_ms": 0,
            }
            query_services(
                service_type=None,
                org_name="Covenant House",
            )
            call_kwargs = mock_exec.call_args
            assert call_kwargs.kwargs["template_key"] == "org_name"
            assert "%Covenant House%" in call_kwargs.kwargs["user_params"]["org_name_pattern"]

    def test_org_name_with_location(self):
        """Location should still be applied as optional filter."""
        from app.rag import query_services

        with patch("app.rag.execute_service_query") as mock_exec:
            mock_exec.return_value = {
                "services": [], "result_count": 0,
                "template_used": "OrgNameQuery", "params_applied": {},
                "relaxed": False, "execution_ms": 0,
            }
            query_services(
                service_type=None,
                location="Manhattan",
                org_name="Safe Horizon",
            )
            params = mock_exec.call_args.kwargs["user_params"]
            assert "org_name_pattern" in params
            # Borough-level search surfaces as city_list (pa.city = ANY(...))
            # since pa.borough doesn't exist. Neighborhood searches surface
            # as city + city_list. Either shape proves location was applied.
            assert "city_list" in params or "city" in params


# -----------------------------------------------------------------------
# CHATBOT INTEGRATION
# -----------------------------------------------------------------------

class TestOrgNameChatbotIntegration:
    """Test org_name flows through the full chatbot pipeline."""

    def test_org_name_triggers_confirmation(self):
        """Saying an org name should trigger confirmation flow."""
        from conftest import send
        result = send("tell me about covenant house")
        assert "Covenant House" in result["response"]

    def test_org_name_with_location_confirms(self):
        from conftest import send
        result = send("safe horizon in harlem")
        assert "Safe Horizon" in result["response"]
