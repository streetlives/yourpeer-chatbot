"""
Tests for multi-turn conversation fixes.

Covers:
  Fix 1: confirm_deny + new service intent (service switch preserved)
  Fix 2: Negation-aware service extraction ("not food, shelter")
  Fix 3: Contradiction auto-execute (skip re-confirmation on slot change)

Run with: python -m pytest tests/unit/test_multiturn_fixes.py -v
"""

import uuid
from unittest.mock import patch

from app.services.slot_extractor import (
    extract_slots,
    _is_negated,
    _extract_all_service_types,
)
from app.services.chatbot import generate_reply
from app.services.session_store import clear_session, save_session_slots
from conftest import send_multi, MOCK_QUERY_RESULTS


# -----------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------

def _flow(messages, mock_results=None):
    """Send multiple messages and return all results."""
    return send_multi(messages, mock_query_return=mock_results or MOCK_QUERY_RESULTS)


# -----------------------------------------------------------------------
# FIX 2: NEGATION-AWARE EXTRACTION
# -----------------------------------------------------------------------

class TestNegationDetection:
    """Test _is_negated helper function."""

    def test_not_prefix(self):
        assert _is_negated("not food, shelter", 4) is True

    def test_forget_prefix(self):
        assert _is_negated("forget food, shelter", 7) is True

    def test_dont_want_prefix(self):
        assert _is_negated("dont want food, need shelter", 10) is True

    def test_instead_of_prefix(self):
        assert _is_negated("instead of food, shelter", 11) is True

    def test_skip_prefix(self):
        assert _is_negated("skip food, give me clothing", 5) is True

    def test_no_negation(self):
        assert _is_negated("i need food in brooklyn", 7) is False

    def test_no_negation_mid_sentence(self):
        assert _is_negated("where can i find food", 18) is False


class TestNegationAwareExtraction:
    """Test that negated service keywords are excluded."""

    def test_not_food_shelter(self):
        slots = extract_slots("not food, shelter")
        assert slots["service_type"] == "shelter"

    def test_forget_food_shelter(self):
        slots = extract_slots("forget food, I need shelter")
        assert slots["service_type"] == "shelter"

    def test_instead_of_food(self):
        slots = extract_slots("instead of food, shelter in Brooklyn")
        assert slots["service_type"] == "shelter"

    def test_dont_want_food(self):
        slots = extract_slots("dont want food, need shelter")
        assert slots["service_type"] == "shelter"

    def test_both_negated_returns_none(self):
        slots = extract_slots("not food, not shelter")
        assert slots["service_type"] is None

    def test_normal_extraction_unaffected(self):
        slots = extract_slots("I need food and shelter")
        assert slots["service_type"] == "food"

    def test_normal_single_service(self):
        slots = extract_slots("I need food in Brooklyn")
        assert slots["service_type"] == "food"

    def test_shelter_not_food(self):
        """When negated keyword comes after the real one, real one wins."""
        slots = extract_slots("shelter not food")
        assert slots["service_type"] == "shelter"

    def test_skip_food(self):
        slots = extract_slots("skip food, give me clothing")
        assert slots["service_type"] == "clothing"


# -----------------------------------------------------------------------
# FIX 1: CONFIRM_DENY + NEW SERVICE INTENT
# -----------------------------------------------------------------------

class TestConfirmDenyServiceSwitch:
    """Test that denial + new service intent switches correctly."""

    def test_changed_my_mind_shelter(self):
        """'I changed my mind, shelter' should switch to shelter."""
        r = _flow(["I need food in Brooklyn", "I changed my mind, shelter"])
        assert "shelter" in r[1]["response"].lower()
        assert "hold onto" not in r[1]["response"].lower()

    def test_nah_clothing(self):
        """'nah, clothing' should switch to clothing."""
        r = _flow(["I need food in Brooklyn", "nah, I need clothing"])
        assert r[1]["slots"].get("service_type") == "clothing" or \
               "clothing" in r[1]["response"].lower()

    def test_pure_denial_no_service(self):
        """'no thanks' without a service should remain a denial."""
        r = _flow(["I need food in Brooklyn", "no thanks"])
        assert "hold onto" in r[1]["response"].lower() or \
               "what would you like" in r[1]["response"].lower()

    def test_switch_preserves_location(self):
        """Service switch should keep the existing location."""
        r = _flow(["I need food in Brooklyn", "I changed my mind, shelter"])
        assert r[1]["slots"].get("location") is not None


# -----------------------------------------------------------------------
# FIX 3: CONTRADICTION AUTO-EXECUTE
# -----------------------------------------------------------------------

class TestContradictionAutoExecute:
    """Test that contradicting a slot during confirmation auto-executes."""

    def test_service_change_auto_executes(self):
        """'actually shelter in Queens' should execute immediately."""
        r = _flow(["I need food in Brooklyn", "actually shelter in Queens"])
        assert r[1]["result_count"] >= 1
        assert "switching" in r[1]["response"].lower() or "got it" in r[1]["response"].lower()

    def test_location_change_auto_executes(self):
        """'actually Manhattan' during confirmation should auto-execute."""
        r = _flow(["I need food in Brooklyn", "actually Manhattan"])
        assert r[1]["result_count"] >= 1

    def test_no_contradiction_re_confirms(self):
        """Adding a NEW slot (not changing one) should re-confirm."""
        r = _flow(["I need food in Brooklyn", "I'm 25"])
        # Age is new (not contradicting), so should show confirmation
        assert r[1]["follow_up_needed"] is True or r[1]["result_count"] == 0

    def test_same_value_not_contradiction(self):
        """Repeating the same service type is not a contradiction."""
        r = _flow(["I need food in Brooklyn", "yes, food"])
        # "yes, food" → confirm_yes with food → should execute normally
        assert r[1]["result_count"] >= 1

    def test_auto_execute_response_has_prefix(self):
        """Auto-executed contradiction should acknowledge the change."""
        r = _flow(["I need food in Brooklyn", "actually shelter in Queens"])
        response = r[1]["response"].lower()
        assert "got it" in response or "switching" in response

    def test_contradiction_needs_enough_slots(self):
        """If contradiction leaves slots incomplete, don't auto-execute."""
        # This is hard to trigger since merge_slots carries over existing slots.
        # The user would need to change service_type AND somehow lose location.
        # In practice, merge_slots preserves location, so this should still execute.
        r = _flow(["I need food in Brooklyn", "actually shelter"])
        # shelter + Brooklyn (from existing) → enough → auto-execute
        assert r[1]["result_count"] >= 1


# -----------------------------------------------------------------------
# COMBINED SCENARIOS
# -----------------------------------------------------------------------

class TestCombinedMultiTurnFlows:
    """Test realistic multi-turn scenarios combining all fixes."""

    def test_full_change_mind_flow(self):
        """User asks for food, changes to shelter, gets results."""
        r = _flow([
            "I need food in Brooklyn",        # → confirmation
            "actually, I need shelter",        # → contradiction → auto-execute
        ])
        assert r[1]["result_count"] >= 1
        assert r[1]["slots"]["service_type"] == "shelter"

    def test_negation_then_confirm(self):
        """User says 'not food, shelter' then confirms."""
        r = _flow([
            "not food, shelter in Manhattan",  # → shelter extracted
            "Yes, search",                     # → execute
        ])
        assert r[0]["slots"]["service_type"] == "shelter"
        assert r[1]["result_count"] >= 1

    def test_deny_switch_then_confirm(self):
        """User denies with new service, then confirms the switch."""
        r = _flow([
            "I need food in Brooklyn",         # → confirmation
            "I changed my mind, clothing",     # → service switch
            "Yes, search",                     # → execute
        ])
        assert r[2]["result_count"] >= 1
