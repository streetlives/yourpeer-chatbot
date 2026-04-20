"""
Tests for peer_diabetic_insulin fixes:
  1. Medication urgency detection in _extract_urgency
  2. Chronic condition sub-type labels in _NOTABLE_SUB_TYPES
  3. Medical urgency tone prefix in chatbot with context persistence

These fixes address the longest-standing eval failure: peer_diabetic_insulin
scored 2.91–3.18 across Runs 28–32 with Dialog Efficiency=1. Root cause was
NOT a confirmation flow bug — the routing works correctly. The Opus judge
scored low because the bot showed no understanding of the medical urgency
("ran out of insulin" is medically dangerous) and used a generic label
("health care") instead of acknowledging the specific need.
"""

import uuid
import pytest

from app.services.slot_extractor import extract_slots
from app.services.session_store import clear_session, get_session_slots

from conftest import send, send_multi


# ===================================================================
# FIX 1: Medication urgency detection
# ===================================================================

class TestMedicationUrgency:
    """Running out of essential medication should set urgency=high."""

    @pytest.mark.parametrize("phrase,expected", [
        # Core scenario
        ("I'm diabetic and ran out of insulin", "high"),
        ("I ran out of my insulin", "high"),
        ("I ran out of my medication", "high"),
        ("I ran out of my meds", "high"),
        # Variations on depletion signal
        ("I'm running out of my medication", "high"),
        ("I don't have my insulin", "high"),
        ("I lost my medication", "high"),
        ("I can't get my prescription", "high"),
        # Other medication types
        ("I ran out of my inhaler", "high"),
        ("I don't have my epipen", "high"),
        ("I ran out of my pills", "high"),
        ("I lost my medicine", "high"),
    ])
    def test_medication_depletion_is_high_urgency(self, phrase, expected):
        slots = extract_slots(phrase)
        assert slots["urgency"] == expected, \
            f"Expected urgency='{expected}' for: {phrase} → {slots['urgency']}"

    @pytest.mark.parametrize("phrase", [
        # No medication word — should NOT trigger
        "I ran out of food",
        "I ran out of options",
        "I ran out of the house",
        # No depletion signal — should NOT trigger
        "I need a doctor",
        "I need insulin",  # need ≠ ran out
        "I need medical help",
        "I have diabetes",
        # Neither depletion nor medication
        "I need food in Brooklyn",
        "I need shelter tonight",
    ])
    def test_non_medication_depletion_no_false_positive(self, phrase):
        slots = extract_slots(phrase)
        # Some of these may have urgency for other reasons (e.g., "tonight")
        # but should NOT get urgency from the medication depletion rule.
        # We verify by checking that phrases without ANY urgency keyword
        # return None.
        if "tonight" not in phrase.lower() and "urgent" not in phrase.lower():
            assert slots["urgency"] is None, \
                f"False positive urgency for: {phrase} → {slots['urgency']}"

    def test_existing_urgency_keywords_still_work(self):
        """Ensure existing urgency detection is not broken."""
        assert extract_slots("I need shelter tonight")["urgency"] == "high"
        assert extract_slots("This is urgent")["urgency"] == "high"
        assert extract_slots("I need help soon")["urgency"] == "medium"
        assert extract_slots("I need food in Brooklyn")["urgency"] is None

    @pytest.mark.parametrize("phrase", [
        # "need my medication" was removed from depletion signals because
        # "medication" self-matches as both depletion AND medication word,
        # creating false positives for routine requests.
        "I need my medication refilled",
        "I need my medication adjusted",
        "I need my medicine from the pharmacy",
        "I need my medication",
    ])
    def test_need_my_medication_not_urgent(self, phrase):
        """'I need my medication [refilled/adjusted]' is a routine request,
        not a depletion emergency. Should NOT trigger urgency."""
        slots = extract_slots(phrase)
        assert slots["urgency"] is None, \
            f"False urgency for routine request: {phrase} → {slots['urgency']}"


# ===================================================================
# FIX 2: Chronic condition sub-type labels
# ===================================================================

class TestChronicConditionSubTypes:
    """Chronic condition keywords should populate service_detail with
    a specific label instead of leaving it None (generic 'health care')."""

    @pytest.mark.parametrize("phrase,expected_type,expected_detail", [
        ("I'm diabetic and ran out of insulin", "medical", "diabetes / insulin care"),
        ("I have diabetes", "medical", "diabetes / insulin care"),
        ("my blood sugar is really high", "medical", "diabetes care"),
        ("I ran out of my inhaler", "medical", "asthma care"),
        ("I have asthma and need help", "medical", "asthma care"),
        # "dialysis" removed from sub-types — DB verified April 16, 2026:
        # 0 service descriptions match dialysis|kidney|renal. The sub-type
        # label would be misleading (shows "dialysis services" in confirmation
        # but can't narrow results).
        ("I need my epipen", "medical", "allergy / EpiPen care"),
    ])
    def test_chronic_condition_detail(self, phrase, expected_type, expected_detail):
        slots = extract_slots(phrase)
        assert slots["service_type"] == expected_type, \
            f"Wrong service_type for: {phrase} → {slots['service_type']}"
        assert slots["service_detail"] == expected_detail, \
            f"Wrong service_detail for: {phrase} → {slots['service_detail']}"

    def test_generic_medical_no_detail(self):
        """Generic medical keywords should still have no service_detail."""
        slots = extract_slots("I need a doctor")
        assert slots["service_type"] == "medical"
        assert slots["service_detail"] is None

    def test_existing_sub_types_preserved(self):
        """Existing sub-types (dental, vision, etc.) should not be broken."""
        assert extract_slots("I need dental care")["service_detail"] == "dental care"
        assert extract_slots("I need an eye doctor")["service_detail"] == "vision care"
        assert extract_slots("I need counseling")["service_detail"] == "counseling"


# ===================================================================
# FIX 3: Medical urgency tone prefix + context persistence
# ===================================================================

class TestMedicalUrgencyTonePrefix:
    """Medication depletion messages should get a specific urgency
    prefix and the context should persist across turns."""

    def test_insulin_turn1_has_urgency_prefix(self):
        """Turn 1 should show the medical urgency prefix."""
        results = send_multi([
            "I'm diabetic and ran out of insulin",
        ])
        response = results[0]["response"]
        assert "urgent" in response.lower(), \
            f"Expected urgency acknowledgment in Turn 1: {response}"

    def test_insulin_turn2_has_specific_label(self):
        """Turn 2 (confirmation) should show 'diabetes / insulin care',
        not generic 'health care'."""
        results = send_multi([
            "I'm diabetic and ran out of insulin",
            "East Harlem",
        ])
        response = results[1]["response"]
        assert "diabetes" in response.lower() or "insulin" in response.lower(), \
            f"Expected specific label in confirmation: {response}"
        assert "health care" not in response.lower(), \
            f"Should NOT show generic 'health care' for insulin: {response}"

    def test_insulin_turn3_returns_results(self):
        """Turn 3 (confirm yes) should execute search and return results."""
        results = send_multi([
            "I'm diabetic and ran out of insulin",
            "East Harlem",
            "Yes, search",
        ])
        assert results[2]["result_count"] >= 1, \
            f"Expected results after confirmation, got {results[2]['result_count']}"

    def test_insulin_full_flow_3_turns(self):
        """The full peer_diabetic_insulin scenario should complete in 3 turns
        with urgency, specific labels, and results."""
        results = send_multi([
            "I'm diabetic and ran out of insulin",
            "East Harlem",
            "Yes, search",
        ])

        # Turn 1: urgency prefix + follow-up for location
        assert "urgent" in results[0]["response"].lower()
        assert results[0]["follow_up_needed"] is True

        # Turn 2: specific label in confirmation
        assert "insulin" in results[1]["response"].lower() or \
               "diabetes" in results[1]["response"].lower()
        assert results[1]["follow_up_needed"] is True

        # Turn 3: results returned
        assert results[2]["result_count"] >= 1
        assert results[2]["follow_up_needed"] is False

    def test_inhaler_gets_urgency_and_detail(self):
        """'Ran out of inhaler' should also trigger urgency + specific label."""
        results = send_multi([
            "I ran out of my inhaler",
            "Brooklyn",
        ])
        # Turn 1: urgency
        assert "urgent" in results[0]["response"].lower()

        # Turn 2: specific label
        assert "asthma" in results[1]["response"].lower()

    def test_medical_urgency_context_persists(self):
        """The medical_urgent emotional context should persist to Turn 2
        so the confirmation stays warm instead of resetting to default."""
        sid = f"test-{uuid.uuid4().hex[:8]}"
        clear_session(sid)
        _results = send_multi([
            "I'm diabetic and ran out of insulin",
            "East Harlem",
        ], session_id=sid)

        slots = get_session_slots(sid)
        assert slots.get("_emotional_context") == "medical_urgent", \
            f"Expected emotional_context='medical_urgent', got {slots.get('_emotional_context')}"

    def test_generic_doctor_no_urgency_prefix(self):
        """'I need a doctor' should NOT trigger medical urgency prefix."""
        results = send_multi([
            "I need a doctor",
        ])
        response = results[0]["response"]
        assert "urgent" not in response.lower(), \
            f"False urgency for generic medical request: {response}"

    def test_insulin_without_depletion_no_urgency(self):
        """'I need insulin' without a depletion signal should NOT trigger
        the medical urgency prefix (it's a normal medical request)."""
        results = send_multi([
            "I need insulin",
        ])
        response = results[0]["response"]
        # Should get baseline warmth or standard prefix, not urgency
        assert "urgent" not in response.lower(), \
            f"False urgency for 'I need insulin' without depletion: {response}"
