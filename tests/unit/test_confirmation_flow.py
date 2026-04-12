"""
Tests for confirmation flow changes.

Covers:
  - confirm_change_service expanded phrase patterns
  - confirm_deny + new service intent interception
  - Contradiction auto-execute during pending confirmation

Run with: python -m pytest tests/unit/test_confirmation_flow.py -v
"""

import pytest
from app.services.classifier import _classify_action
from conftest import send_multi, MOCK_QUERY_RESULTS


# -----------------------------------------------------------------------
# CONFIRM CHANGE SERVICE PATTERNS
# -----------------------------------------------------------------------

class TestConfirmChangeServicePatterns:
    """Expanded patterns should classify as confirm_change_service."""

    @pytest.mark.parametrize("phrase", [
        "change to shelter",
        "switch to clothing",
        "can i change to food",
        "can we do shelter",
        "i want to switch",
        "let me change",
        "i'd rather have",
    ])
    def test_new_pattern(self, phrase):
        assert _classify_action(phrase) == "confirm_change_service"

    @pytest.mark.parametrize("phrase", [
        "change service", "different service", "wrong service",
        "change what i need", "change service type",
    ])
    def test_existing_patterns_unchanged(self, phrase):
        assert _classify_action(phrase) == "confirm_change_service"

    def test_during_pending_confirmation(self):
        """'Change to shelter' during pending should clear and re-prompt."""
        r = send_multi(["I need food in Brooklyn", "change to shelter"])
        # Should either show new confirmation for shelter, or prompt for service
        resp = r[1]["response"].lower()
        has_shelter = "shelter" in resp
        has_prompt = "what kind" in resp or "what do you need" in resp
        assert has_shelter or has_prompt


# -----------------------------------------------------------------------
# CONFIRM DENY + NEW SERVICE INTENT
# -----------------------------------------------------------------------

class TestConfirmDenyServiceSwitch:
    """Denial messages containing a new service intent should switch, not deny."""

    def test_changed_mind_shelter(self):
        r = send_multi(["I need food in Brooklyn", "I changed my mind, shelter"])
        assert "shelter" in r[1]["response"].lower()
        assert "hold onto" not in r[1]["response"].lower()

    def test_nah_clothing(self):
        r = send_multi(["I need food in Brooklyn", "nah, I need clothing"])
        resp = r[1]["response"].lower()
        assert "clothing" in resp or r[1]["slots"].get("service_type") == "clothing"

    def test_pure_denial_no_service(self):
        """'no thanks' without a service keyword remains a denial."""
        r = send_multi(["I need food in Brooklyn", "no thanks"])
        resp = r[1]["response"].lower()
        assert "hold onto" in resp or "what would you like" in resp

    def test_switch_preserves_location(self):
        r = send_multi(["I need food in Brooklyn", "I changed my mind, shelter"])
        assert r[1]["slots"].get("location") is not None

    def test_switch_with_new_location(self):
        """Denial + new service + new location."""
        r = send_multi(["I need food in Brooklyn", "no, shelter in Manhattan"])
        resp = r[1]["response"].lower()
        assert "shelter" in resp
        assert "manhattan" in resp or r[1]["slots"].get("location") == "manhattan"

    def test_same_service_stays_denial(self):
        """'nah, food' (same service) should be a denial, not a switch."""
        r = send_multi(["I need food in Brooklyn", "nah, food"])
        # Same service → pure denial
        resp = r[1]["response"].lower()
        assert "hold onto" in resp or "what would you like" in resp


# -----------------------------------------------------------------------
# CONTRADICTION AUTO-EXECUTE
# -----------------------------------------------------------------------

class TestContradictionAutoExecute:
    """Contradicting a slot during pending confirmation should auto-execute."""

    def test_service_change_auto_executes(self):
        r = send_multi(["I need food in Brooklyn", "actually shelter in Queens"])
        assert r[1]["result_count"] >= 1
        resp = r[1]["response"].lower()
        assert "switching" in resp or "got it" in resp

    def test_location_change_auto_executes(self):
        r = send_multi(["I need food in Brooklyn", "actually Manhattan"])
        assert r[1]["result_count"] >= 1

    def test_both_changed_auto_executes(self):
        r = send_multi(["I need food in Brooklyn", "actually shelter in Manhattan"])
        assert r[1]["result_count"] >= 1
        assert r[1]["slots"]["service_type"] == "shelter"

    def test_new_slot_no_contradiction_reconfirms(self):
        """Adding age (not contradicting) should re-confirm, not auto-execute."""
        r = send_multi(["I need food in Brooklyn", "I'm 25"])
        assert r[1]["follow_up_needed"] is True or r[1]["result_count"] == 0

    def test_same_value_not_contradiction(self):
        """Repeating the same service type is not a contradiction."""
        r = send_multi(["I need food in Brooklyn", "yes, food"])
        assert r[1]["result_count"] >= 1

    def test_auto_execute_has_prefix(self):
        r = send_multi(["I need food in Brooklyn", "actually shelter in Queens"])
        resp = r[1]["response"].lower()
        assert "got it" in resp or "switching" in resp

    def test_full_change_mind_flow(self):
        """User asks food, changes to shelter, gets results."""
        r = send_multi([
            "I need food in Brooklyn",
            "actually, I need shelter",
        ])
        assert r[1]["result_count"] >= 1
        assert r[1]["slots"]["service_type"] == "shelter"
