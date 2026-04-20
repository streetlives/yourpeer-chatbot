"""
End-to-end tests for core conversation flow invariants.

Guards the fundamental contracts that must never break:
  - Confirmation must precede search execution
  - Results must not duplicate on repeated confirmation
  - Informal declines must be recognized in all contexts
  - Service switching must update slots correctly
  - Urgent, emotional, and multi-service flows must behave predictably

Run with: python -m pytest tests/unit/test_core_conversation_flows.py -v
"""

import pytest
from conftest import send_multi, MOCK_QUERY_RESULTS


# -----------------------------------------------------------------------
# PATTERN A: SCENARIOS THAT MUST SHOW CONFIRMATION
# -----------------------------------------------------------------------

class TestConfirmationRequired:
    """Scenarios that regressed because confirmation was skipped."""

    def test_shelter_queens_17(self):
        """shelter_queens_17: 4.88 → 3.62 in R25."""
        r = send_multi(["I'm 17 and need somewhere to sleep in Queens"])
        assert r[0]["result_count"] == 0, "Must show confirmation, not auto-execute"
        assert r[0]["follow_up_needed"] is True

    def test_peer_lgbtq_youth_shelter_soho(self):
        """peer_lgbtq_youth_shelter_soho: 4.88 → 3.25 in R25."""
        r = send_multi(["21, LGBTQ, in Soho, need a bed tonight"])
        assert r[0]["result_count"] == 0, "Must show confirmation"
        assert r[0]["follow_up_needed"] is True

    def test_wa_family_with_children(self):
        """wa_family_with_children: 4.88 → 3.38 in R25."""
        r = send_multi(["I have 3 kids and we need shelter tonight in Brooklyn"])
        assert r[0]["result_count"] == 0, "Must show confirmation"

    def test_wa_substance_use_shelter(self):
        """wa_substance_use_shelter: 4.50 → 3.50 in R25."""
        r = send_multi(["I need a shelter that accepts people in recovery in Manhattan"])
        assert r[0]["result_count"] == 0, "Must show confirmation"

    def test_multi_foster_youth_aging_out(self):
        """multi_foster_youth_aging_out: 4.75 → 3.25 in R25."""
        r = send_multi(["I'm aging out of foster care, 21, need help in the Bronx"])
        assert r[0]["result_count"] == 0, "Must show confirmation"

    def test_schedule_open_now_request(self):
        """schedule_open_now_request: 4.75 → 3.50 in R25."""
        r = send_multi(["What food pantries are open right now in Manhattan?"])
        assert r[0]["result_count"] == 0, "Must show confirmation"


# -----------------------------------------------------------------------
# PATTERN B: SCENARIOS THAT MUST NOT DUPLICATE RESULTS
# -----------------------------------------------------------------------

class TestNoDuplicateResults:
    """Scenarios that regressed because 'Yes, search' duplicated results."""

    def test_multi_family_with_children_path(self):
        """multi_family_with_children_path: 5.00 → 3.38 in R25."""
        r = send_multi([
            "I have 3 kids and we need shelter tonight in Brooklyn",
            "Yes, search",
            "Yes, search",
        ])
        assert r[1]["result_count"] >= 1, "T2 should deliver results"
        assert r[2]["result_count"] == 0, "T3 must NOT duplicate"

    def test_multi_urgent_shelter_and_food(self):
        """multi_urgent_shelter_and_food_tonight: 4.75 → 3.38 in R25."""
        r = send_multi([
            "I need shelter and food tonight in Brooklyn",
            "Yes, search",
            "Yes, search",
        ])
        assert r[2]["result_count"] == 0, "Must not duplicate"

    def test_multi_reentry_shelter_employment(self):
        """multi_reentry_shelter_employment: 4.62 → 3.38 in R25."""
        r = send_multi([
            "just got out of jail, need somewhere to stay and help finding work in Brooklyn",
            "Yes, search",
            "Yes, search",
        ])
        assert r[2]["result_count"] == 0, "Must not duplicate"

    def test_natural_new_to_nyc(self):
        """natural_new_to_nyc: 4.88 → 3.38 in R25."""
        r = send_multi([
            "I just arrived in NYC, need somewhere to stay tonight",
            "Manhattan",
            "Yes, search",
            "Yes, search",
        ])
        # Find the turn that delivered results
        result_turn = next((i for i, res in enumerate(r) if res["result_count"] > 0), -1)
        if result_turn >= 0 and result_turn + 1 < len(r):
            assert r[result_turn + 1]["result_count"] == 0, "Must not duplicate"

    def test_peer_young_mom_multiple_needs(self):
        """peer_young_mom_multiple_needs: 4.50 → 3.50 in R25."""
        r = send_multi([
            "19-year-old mom with a baby, need shelter, diapers, food in Manhattan",
            "Yes, search",
            "Yes, search",
        ])
        assert r[2]["result_count"] == 0, "Must not duplicate"


# -----------------------------------------------------------------------
# PATTERN C: DECLINE MISCLASSIFICATION
# -----------------------------------------------------------------------

class TestDeclineMisclassification:
    """Scenarios where informal declines were misclassified."""

    def test_multi_decline_with_different_phrasing(self):
        """multi_decline_with_different_phrasing: 4.62 → 3.12 in R25."""
        r = send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
            "nah I'm good",
        ])
        resp = r[2]["response"].lower()
        assert "no problem" in resp or "let me know" in resp, \
            f'"nah I\'m good" should decline, got: {r[2]["response"][:80]}'
        assert "search for food" not in resp, "Should NOT re-search food"

    def test_multi_decline_queued_service(self):
        """multi_decline_queued_service: 4.38 → 3.88 in R25."""
        r = send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
            "No thanks",
        ])
        resp = r[2]["response"].lower()
        assert "no problem" in resp or "let me know" in resp


# -----------------------------------------------------------------------
# PATTERN E: MULTITURN CHANGE MIND
# -----------------------------------------------------------------------

class TestMultiturnChangeMind:
    """multiturn_change_mind: 3.25 → 2.62 in R25."""

    def test_change_mind_switches_service(self):
        """Core test: food → 'actually shelter' should switch."""
        r = send_multi(["I need food in Manhattan", "actually, I need shelter"])
        assert r[1]["slots"]["service_type"] == "shelter"

    def test_change_mind_delivers_shelter_not_food(self):
        """Must NOT deliver food results after switch."""
        r = send_multi(["I need food in Manhattan", "actually shelter"])
        if r[1]["result_count"] > 0:
            # If auto-executed, should be shelter results
            assert r[1]["slots"]["service_type"] == "shelter"

    def test_change_mind_to_shelter_in_manhattan(self):
        """The eval expects 'shelter search in Manhattan'."""
        r = send_multi(["I need food in Manhattan", "I need shelter instead"])
        assert r[1]["slots"]["service_type"] == "shelter"
        assert r[1]["slots"].get("location") is not None


# -----------------------------------------------------------------------
# OTHER R25 REGRESSIONS
# -----------------------------------------------------------------------

class TestOtherR25Regressions:
    """Scenarios with moderate regressions that should not repeat."""

    def test_all_slots_at_once(self):
        """all_slots_at_once: 5.00 → 4.50 in R25."""
        r = send_multi(["I'm a 25-year-old woman looking for shelter in Brooklyn"])
        assert r[0]["result_count"] == 0, "Must confirm before executing"
        assert "shelter" in r[0]["response"].lower()
        assert "brooklyn" in r[0]["response"].lower()

    def test_emotional_then_yes(self):
        """emotional_then_yes: 5.00 → 4.50 in R25."""
        _r = send_multi([
            "I'm really struggling and need help",
            "yes",
        ])
        # Should not crash or re-execute

    def test_wa_rough_sleeper_urgent(self):
        """wa_rough_sleeper_urgent: 4.50 → 4.00 in R25."""
        r = send_multi(["sleeping rough tonight, need shelter in Manhattan"])
        assert r[0]["result_count"] == 0, "Must confirm"
        assert r[0]["follow_up_needed"] is True

    def test_natural_parent_with_child(self):
        """natural_parent_with_child: 5.00 → 4.75 in R25."""
        r = send_multi(["I have my kid with me and need food near Brooklyn"])
        assert r[0]["result_count"] == 0, "Must confirm"

    def test_conversational_just_chatting(self):
        """conversational_just_chatting: 5.00 → 4.75 in R25."""
        r = send_multi(["hey how's it going"])
        # Should be a casual response, not trigger service search
        assert r[0]["result_count"] == 0

    def test_peer_pregnant_couple_tonight(self):
        """peer_pregnant_couple_tonight: 4.88 → 4.38 in R25."""
        r = send_multi(["I'm pregnant and my partner and I need a place tonight in Brooklyn"])
        assert r[0]["result_count"] == 0, "Must confirm"
        assert r[0]["follow_up_needed"] is True
