"""
Tests for multi-turn change-mind flows and decline phrase handling.

Prevents regressions for:
  - multiturn_change_mind (persistent since R14, regression R24→R25)
  - multi_decline_with_different_phrasing (R24→R25 regression)
  - All service-switch variants during pending confirmation

Run with: python -m pytest tests/unit/test_change_mind_and_decline.py -v
"""

import pytest
from app.services.classifier import _classify_action
from app.services.slot_extractor import extract_slots
from conftest import send_multi, MOCK_QUERY_RESULTS


# -----------------------------------------------------------------------
# CHANGE-MIND: CLASSIFICATION
# -----------------------------------------------------------------------

class TestChangeMindClassification:
    """Verify all change-mind phrasings extract the correct service_type."""

    @pytest.mark.parametrize("msg,expected_service", [
        ("actually, shelter", "shelter"),
        ("actually I need shelter", "shelter"),
        ("actually shelter", "shelter"),
        ("I changed my mind, I need shelter", "shelter"),
        ("no, I need shelter instead", "shelter"),
        ("wait, shelter not food", "shelter"),
        ("I need a place to sleep tonight", "shelter"),
        ("shelter", "shelter"),
        ("I want shelter instead", "shelter"),
        ("can we do shelter", "shelter"),
        ("switch to shelter", "shelter"),
        ("forget food, I need shelter", "shelter"),
        ("not food, shelter", "shelter"),
    ])
    def test_service_extraction(self, msg, expected_service):
        s = extract_slots(msg)
        assert s["service_type"] == expected_service, \
            f'"{msg}" → {s["service_type"]}, expected {expected_service}'


# -----------------------------------------------------------------------
# CHANGE-MIND: 2-TURN FLOWS (food+location → change)
# -----------------------------------------------------------------------

class TestChangeMindTwoTurn:
    """User provides food+location in T1, changes to shelter in T2."""

    @pytest.mark.parametrize("change_msg", [
        "actually, shelter",
        "actually I need shelter",
        "I changed my mind, I need shelter",
        "no, I need shelter instead",
        "wait, shelter not food",
        "I need a place to sleep tonight",
        "shelter",
        "forget food, I need shelter",
        "not food, shelter",
    ])
    def test_switches_to_shelter(self, change_msg):
        r = send_multi(["I need food in Manhattan", change_msg])
        assert r[1]["slots"]["service_type"] == "shelter", \
            f'"{change_msg}" → {r[1]["slots"]["service_type"]}'

    def test_switch_preserves_location(self):
        r = send_multi(["I need food in Manhattan", "actually shelter"])
        assert r[1]["slots"]["location"] is not None

    def test_switch_with_new_location(self):
        r = send_multi(["I need food in Manhattan", "actually shelter in Brooklyn"])
        assert r[1]["slots"]["service_type"] == "shelter"
        assert "brooklyn" in r[1]["slots"].get("location", "")


# -----------------------------------------------------------------------
# CHANGE-MIND: 3-TURN FLOWS (food → location → change)
# -----------------------------------------------------------------------

class TestChangeMindThreeTurn:
    """User provides food, then location, then changes mind."""

    def test_food_then_location_then_shelter(self):
        r = send_multi([
            "I need food",
            "Manhattan",
            "actually, I need shelter",
        ])
        assert r[2]["slots"]["service_type"] == "shelter"

    def test_food_then_location_then_change_mind(self):
        r = send_multi([
            "I need food",
            "Brooklyn",
            "I changed my mind, I need shelter",
        ])
        assert r[2]["slots"]["service_type"] == "shelter"


# -----------------------------------------------------------------------
# CHANGE-MIND: 4-TURN FLOWS (full round-trip)
# -----------------------------------------------------------------------

class TestChangeMindFourTurn:
    """User changes mind after food search, then confirms shelter."""

    def test_food_confirm_change_confirm(self):
        r = send_multi([
            "I need food in Manhattan",     # T1: confirmation
            "actually, I need shelter",      # T2: switch
            "Yes, search",                   # T3: if re-confirmed, execute
        ])
        # By T3, shelter should be the service type
        final = next(
            (res for res in reversed(r) if res["result_count"] > 0),
            r[-1],
        )
        assert final["slots"]["service_type"] == "shelter"

    def test_food_confirm_change_back(self):
        """Change to shelter, then change back to food."""
        r = send_multi([
            "I need food in Manhattan",
            "actually shelter",
            "wait no, food",
        ])
        assert r[2]["slots"]["service_type"] == "food"

    def test_change_mind_twice(self):
        """food → shelter → clothing."""
        r = send_multi([
            "I need food in Manhattan",
            "actually shelter",
            "no wait, clothing",
        ])
        assert r[2]["slots"]["service_type"] == "clothing"


# -----------------------------------------------------------------------
# CHANGE-MIND: EDGE CASES
# -----------------------------------------------------------------------

class TestChangeMindEdgeCases:
    """Edge cases for service switching during confirmation."""

    def test_same_service_not_a_switch(self):
        """'food' when food is pending is confirmation, not a switch."""
        r = send_multi(["I need food in Brooklyn", "yes, food"])
        assert r[1]["result_count"] >= 1

    def test_change_to_unrecognized_service(self):
        """Changing to a service that doesn't exist."""
        r = send_multi(["I need food in Brooklyn", "actually helicopter rides"])
        # Should not deliver food results
        assert r[1]["slots"].get("service_type") != "food" or r[1]["result_count"] == 0

    def test_change_mind_preserves_demographics(self):
        """Age/gender should survive a service switch."""
        r = send_multi(["I'm 19, I need food in Brooklyn", "actually shelter"])
        assert r[1]["slots"].get("age") == 19
        assert r[1]["slots"]["service_type"] == "shelter"


# -----------------------------------------------------------------------
# DECLINE PHRASES: CLASSIFICATION
# -----------------------------------------------------------------------

class TestDeclineClassification:
    """All decline phrases should classify as confirm_deny."""

    @pytest.mark.parametrize("phrase", [
        "no", "nah", "nope", "not yet", "wait", "stop",
        "no thanks", "no thank you",
        "nah I'm good", "nah im good", "I'm good", "im good",
        "all good", "no need",
        "I'm fine", "im fine", "no I'm fine", "no im fine",
    ])
    def test_classifies_as_confirm_deny(self, phrase):
        action = _classify_action(phrase)
        assert action == "confirm_deny", \
            f'"{phrase}" → {action}, expected confirm_deny'


# -----------------------------------------------------------------------
# DECLINE: DURING PENDING CONFIRMATION
# -----------------------------------------------------------------------

class TestDeclineDuringPending:
    """Decline during pending confirmation should hold info and offer options."""

    @pytest.mark.parametrize("decline", [
        "no thanks", "nah", "I'm good", "not right now",
    ])
    def test_decline_during_pending(self, decline):
        r = send_multi(["I need food in Brooklyn", decline])
        resp = r[1]["response"].lower()
        assert "hold onto" in resp or "what would you like" in resp
        assert r[1]["result_count"] == 0

    def test_decline_then_new_search(self):
        """After declining, user can start a new search."""
        r = send_multi(["I need food in Brooklyn", "no thanks", "I need shelter"])
        assert "shelter" in r[2]["response"].lower()


# -----------------------------------------------------------------------
# DECLINE: AFTER RESULTS
# -----------------------------------------------------------------------

class TestDeclineAfterResults:
    """Decline after results should end gracefully, not re-search."""

    @pytest.mark.parametrize("decline", [
        "no thanks", "nah", "nah I'm good", "I'm good",
        "im good", "all good", "I'm fine", "no I'm fine",
    ])
    def test_decline_after_results_graceful(self, decline):
        r = send_multi(["I need food in Brooklyn", "Yes, search", decline])
        resp = r[2]["response"].lower()
        is_graceful = "no problem" in resp or "let me know" in resp
        is_re_search = "search for food" in resp
        assert is_graceful and not is_re_search, \
            f'"{decline}" after results: {r[2]["response"][:80]}'


# -----------------------------------------------------------------------
# DECLINE: AFTER QUEUE OFFER
# -----------------------------------------------------------------------

class TestDeclineAfterQueueOffer:
    """Decline after queue offer should clear queue."""

    @pytest.mark.parametrize("decline", [
        "No thanks", "nah", "nah I'm good", "no I'm fine",
        "I'm good", "im fine",
    ])
    def test_decline_queue_offer(self, decline):
        r = send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
            decline,
        ])
        resp = r[2]["response"].lower()
        assert "no problem" in resp or "let me know" in resp


# -----------------------------------------------------------------------
# DECLINE WITH SERVICE INTENT (confirm_deny + new service)
# -----------------------------------------------------------------------

class TestDeclineWithServiceIntent:
    """Decline phrases that also contain a new service should switch."""

    def test_nah_shelter(self):
        r = send_multi(["I need food in Brooklyn", "nah, I need shelter"])
        assert r[1]["slots"]["service_type"] == "shelter"

    def test_no_clothing_instead(self):
        r = send_multi(["I need food in Brooklyn", "no, clothing instead"])
        resp = r[1]["response"].lower()
        assert "clothing" in resp

    def test_changed_mind_medical(self):
        r = send_multi(["I need food in Brooklyn", "I changed my mind, medical"])
        assert r[1]["slots"]["service_type"] == "medical"


# -----------------------------------------------------------------------
# NEGATION WITH FILLER WORDS (multiturn_change_mind root cause fix)
# -----------------------------------------------------------------------

class TestNegationFillerWords:
    """_is_negated should handle articles/determiners between negation and keyword.

    Root cause of multiturn_change_mind: 'forget the food' was not negating
    'food' because 'the' broke the direct adjacency check.
    """

    @pytest.mark.parametrize("msg,expected_service", [
        # Filler-word negation — food should be negated
        ("forget the food, I need shelter", "shelter"),
        ("forget my food, shelter please", "shelter"),
        ("skip the food, shelter", "shelter"),
        ("not the food, shelter", "shelter"),
        ("forget about the food, I need shelter", "shelter"),
        ("don't want the food, shelter", "shelter"),
        ("don't need the food, give me shelter", "shelter"),
        ("skip my food, I need clothing", "clothing"),
        # The exact eval scenario message
        ("Actually forget the food, I really need a place to sleep tonight", "shelter"),
        # Should NOT negate — no negation word, just filler
        ("the food bank is great", "food"),
        ("I forgot I need food", "food"),
        ("I need my food in Brooklyn", "food"),
    ])
    def test_filler_word_negation(self, msg, expected_service):
        s = extract_slots(msg)
        assert s["service_type"] == expected_service, \
            f'"{msg}" → {s["service_type"]}, expected {expected_service}'


class TestContradictionSignalExtraction:
    """Contradiction signals should reorder services so the new intent is primary."""

    @pytest.mark.parametrize("msg,expected_service", [
        # Strong contradiction signals promote post-signal service
        ("I changed my mind, I need medical", "medical"),
        ("never mind that, I need shelter", "shelter"),
        ("scratch that, I need clothing", "clothing"),
        # "actually" is NOT a contradiction signal (too common in normal speech)
        # — "I actually need food and shelter" would false-positive
        # These rely on negation or the confirm_change_service handler instead
        ("I need food and shelter", "food"),            # no signal — first wins
        ("I actually need food and shelter", "food"),   # "actually" not a signal
    ])
    def test_contradiction_reordering(self, msg, expected_service):
        s = extract_slots(msg)
        assert s["service_type"] == expected_service, \
            f'"{msg}" → {s["service_type"]}, expected {expected_service}'

    def test_contradiction_flag_set(self):
        s = extract_slots("I changed my mind, I need shelter")
        assert s["_contradiction"] is True

    def test_no_contradiction_flag_for_normal(self):
        s = extract_slots("I need food in Brooklyn")
        assert s["_contradiction"] is False


class TestMergeSlotsContradiction:
    """merge_slots should promote additional_services on contradiction when
    negation failed to filter the old service (defense-in-depth)."""

    def test_promotion_when_same_service_with_contradiction(self):
        from app.services.slot_extractor import merge_slots
        existing = {"service_type": "food", "location": "manhattan"}
        new_vals = {
            "service_type": "food",  # negation didn't catch it
            "additional_services": [("shelter", None, None)],
            "_contradiction": True,
        }
        merged = merge_slots(existing, new_vals)
        assert merged["service_type"] == "shelter"

    def test_no_promotion_without_contradiction(self):
        from app.services.slot_extractor import merge_slots
        existing = {"service_type": "food", "location": "manhattan"}
        new_vals = {
            "service_type": "food",
            "additional_services": [("shelter", None, None)],
            "_contradiction": False,
        }
        merged = merge_slots(existing, new_vals)
        assert merged["service_type"] == "food"

    def test_contradiction_clears_pending_confirmation(self):
        from app.services.slot_extractor import merge_slots
        existing = {
            "service_type": "food",
            "location": "manhattan",
            "_pending_confirmation": True,
        }
        new_vals = {
            "service_type": "shelter",
            "_contradiction": True,
        }
        merged = merge_slots(existing, new_vals)
        assert merged["service_type"] == "shelter"
        assert "_pending_confirmation" not in merged

    def test_contradiction_flag_not_persisted(self):
        from app.services.slot_extractor import merge_slots
        existing = {"service_type": "food"}
        new_vals = {"service_type": "shelter", "_contradiction": True}
        merged = merge_slots(existing, new_vals)
        assert "_contradiction" not in merged

    def test_demographics_preserved_on_contradiction(self):
        from app.services.slot_extractor import merge_slots
        existing = {"service_type": "food", "location": "brooklyn", "age": 19}
        new_vals = {"service_type": "shelter", "_contradiction": True}
        merged = merge_slots(existing, new_vals)
        assert merged["service_type"] == "shelter"
        assert merged["age"] == 19
        assert merged["location"] == "brooklyn"


# -----------------------------------------------------------------------
# EVAL SCENARIO: multiturn_change_mind (4-turn regression guard)
# -----------------------------------------------------------------------

class TestEvalMultiturnChangeMind:
    """Exact reproduction of the multiturn_change_mind eval scenario.

    User: 'I need food'
    User: 'Manhattan'
    User: 'Actually forget the food, I really need a place to sleep tonight'
    User: 'Yes, search'

    Expected: service_type=shelter, location contains manhattan.
    """

    def test_full_eval_scenario(self):
        r = send_multi([
            "I need food",
            "Manhattan",
            "Actually forget the food, I really need a place to sleep tonight",
            "Yes, search",
        ])
        # After turn 3, service_type should be shelter
        assert r[2]["slots"]["service_type"] == "shelter"
        # Location should be preserved from turn 2
        loc = r[2]["slots"].get("location", "")
        assert "manhattan" in loc.lower(), f"location={loc}"

    def test_turn_3_switches_to_shelter(self):
        """Turn 3 alone should switch from food to shelter."""
        r = send_multi([
            "I need food",
            "Manhattan",
            "Actually forget the food, I really need a place to sleep tonight",
        ])
        assert r[2]["slots"]["service_type"] == "shelter"

    def test_final_results_are_shelter(self):
        """The final results (after 'Yes, search') should be for shelter."""
        r = send_multi([
            "I need food",
            "Manhattan",
            "Actually forget the food, I really need a place to sleep tonight",
            "Yes, search",
        ])
        final = next(
            (res for res in reversed(r) if res["result_count"] > 0),
            r[-1],
        )
        assert final["slots"]["service_type"] == "shelter"

    def test_variant_forget_about_the_food(self):
        """Variant phrasing: 'forget about the food'."""
        r = send_multi([
            "I need food in Brooklyn",
            "forget about the food, I need shelter",
        ])
        assert r[1]["slots"]["service_type"] == "shelter"

    def test_variant_skip_the_food(self):
        """Variant phrasing: 'skip the food'."""
        r = send_multi([
            "I need food in Brooklyn",
            "skip the food, I need a shower",
        ])
        assert r[1]["slots"]["service_type"] == "personal_care"
