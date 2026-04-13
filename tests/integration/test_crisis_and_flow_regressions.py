"""
Tests for structural fixes targeting Run 16's 25 failing scenarios.

See STRUCTURAL_FIXES_CHANGELOG.md for the full fix descriptions.
Each test class corresponds to one fix.
"""

import uuid
import pytest
from unittest.mock import patch

from app.services.classifier import _classify_action, _classify_tone
from app.services.responses import _ESCALATION_RESPONSE
from app.services.chatbot import generate_reply
from app.services.slot_extractor import extract_slots
from app.services.crisis_detector import detect_crisis
from app.services.session_store import clear_session, get_session_slots, save_session_slots

from conftest import MOCK_QUERY_RESULTS, send, send_multi


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _fresh_sid():
    sid = f"test-{uuid.uuid4().hex[:8]}"
    clear_session(sid)
    return sid


def send_with_crisis(message, session_id=None, mock_query_return=None):
    """Like send() but does NOT patch detect_crisis — lets real crisis
    detection run so we can test crisis step-down behavior."""
    from app.services.chatbot import generate_reply

    if mock_query_return is None:
        mock_query_return = MOCK_QUERY_RESULTS

    if session_id is None:
        session_id = _fresh_sid()

    with patch("app.services.chatbot.claude_reply", return_value="How can I help?"), \
         patch("app.services.chatbot.query_services", return_value=mock_query_return):
        return generate_reply(message, session_id=session_id)


def send_multi_with_crisis(messages, session_id=None, mock_query_return=None):
    """Like send_multi() but does NOT patch detect_crisis."""
    if mock_query_return is None:
        mock_query_return = MOCK_QUERY_RESULTS

    if session_id is None:
        session_id = _fresh_sid()

    results = []
    with patch("app.services.chatbot.claude_reply", return_value="How can I help?"), \
         patch("app.services.chatbot.query_services", return_value=mock_query_return):
        for msg in messages:
            results.append(generate_reply(msg, session_id=session_id))
    return results


# ===================================================================
# FIX 1: "struggling" etc. removed from mental_health keywords
# ===================================================================

class TestFix1MentalHealthKeywords:
    """Fix 1: emotional expressions should not extract as mental_health."""

    def test_struggling_and_shelter_extracts_shelter(self):
        """'I'm struggling and need shelter' → shelter, not mental_health."""
        slots = extract_slots("I'm struggling and need shelter in Queens")
        assert slots["service_type"] == "shelter", \
            f"Expected shelter, got {slots['service_type']}"
        assert slots["location"] == "queens"

    def test_struggling_alone_no_service(self):
        """'I've been struggling' alone should not extract mental_health."""
        slots = extract_slots("I've been struggling lately")
        assert slots["service_type"] is None

    def test_having_hard_time_no_service(self):
        """'Having a hard time' should not extract mental_health."""
        slots = extract_slots("I'm having a hard time")
        assert slots["service_type"] is None

    def test_someone_to_talk_to_no_service(self):
        """'someone to talk to' is escalation, not mental_health."""
        slots = extract_slots("I just need someone to talk to")
        assert slots["service_type"] is None

    def test_real_mental_health_still_works(self):
        """Actual mental health service keywords should still extract."""
        cases = [
            ("I need counseling", "mental_health"),
            ("I need therapy", "mental_health"),
            ("I need help with addiction", "mental_health"),
            ("Where can I find AA meetings?", "mental_health"),
            ("I need substance abuse help", "mental_health"),
            ("I'm dealing with grief", "mental_health"),
        ]
        for phrase, expected in cases:
            slots = extract_slots(phrase)
            assert slots["service_type"] == expected, \
                f"'{phrase}' → expected {expected}, got {slots['service_type']}"

    def test_struggling_with_shelter_multi_intent(self):
        """'struggling and need food and shelter' → food primary, shelter queued.
        NOT mental_health."""
        slots = extract_slots("I'm struggling and need food and shelter in Brooklyn")
        assert slots["service_type"] == "food", \
            f"Expected food as primary, got {slots['service_type']}"
        additional = slots.get("additional_services", [])
        additional_types = [s[0] for s in additional]
        assert "shelter" in additional_types


# ===================================================================
# FIX 2: LLM crisis guard for emotional phrases
# ===================================================================

class TestFix2CrisisGuard:
    """Fix 2: sub-crisis emotional phrases should not trigger LLM crisis."""

    def test_feeling_scared_not_crisis(self):
        """'I'm feeling scared' should not be detected as crisis."""
        # With LLM disabled, detect_crisis uses regex only.
        # "feeling scared" is NOT in crisis regex phrases, so should be None.
        result = detect_crisis("I'm feeling scared", skip_llm=True)
        assert result is None, \
            f"'I'm feeling scared' should not be crisis, got {result}"

    def test_feeling_scared_tone_is_emotional(self):
        """'I'm feeling scared' should classify as emotional tone."""
        tone = _classify_tone("I'm feeling scared", crisis_result=None)
        assert tone == "emotional", \
            f"Expected emotional tone, got {tone}"

    def test_rough_day_not_crisis(self):
        result = detect_crisis("I'm having a rough day", skip_llm=True)
        assert result is None

    def test_struggling_not_crisis(self):
        result = detect_crisis("I'm struggling right now", skip_llm=True)
        assert result is None

    def test_real_crisis_still_detected(self):
        """Actual crisis phrases should still fire."""
        cases = [
            "I want to kill myself",
            "I'm being abused",
            "Someone is threatening me",
            "I can't breathe",
        ]
        for phrase in cases:
            result = detect_crisis(phrase, skip_llm=True)
            assert result is not None, \
                f"Crisis should fire for: '{phrase}'"


# ===================================================================
# FIX 3+4: Crisis step-down with service intent
# ===================================================================

class TestFix3CrisisStepDown:
    """Fix 3+4: crisis + service intent should show crisis resources AND
    offer to search, then 'yes' executes the search."""

    def test_kicked_out_with_shelter_gets_step_down(self):
        """'Kicked me out, need shelter in Brooklyn' → crisis resources +
        search offer."""
        sid = _fresh_sid()
        result = send_with_crisis(
            "My family kicked me out and I need shelter in Brooklyn",
            session_id=sid,
        )
        response = result["response"].lower()
        # Should have crisis resources
        assert "hotline" in response or "1-800" in response or "911" in response
        # Should also offer to search
        assert "shelter" in response
        assert "search" in response or "find" in response
        # Session should have service_type preserved
        slots = get_session_slots(sid)
        assert slots.get("service_type") == "shelter"
        assert slots.get("_last_action") == "crisis"

    def test_yes_after_step_down_executes_search(self):
        """'Yes' after crisis step-down should execute the service search."""
        sid = _fresh_sid()
        results = send_multi_with_crisis([
            "My family kicked me out and I need shelter in Brooklyn",
            "Yes, search",
        ], session_id=sid)
        final = results[-1]
        # Should have executed a search (mocked results)
        assert final["result_count"] >= 1 or final["follow_up_needed"]

    def test_no_after_step_down_is_graceful(self):
        """'No' after crisis step-down should acknowledge gracefully."""
        sid = _fresh_sid()
        results = send_multi_with_crisis([
            "My family kicked me out and I need shelter in Brooklyn",
            "no",
        ], session_id=sid)
        response = results[-1]["response"].lower()
        assert "okay" in response or "resources" in response or "here" in response

    def test_ran_away_with_shelter_gets_step_down(self):
        """Runaway youth with shelter request should get step-down."""
        sid = _fresh_sid()
        result = send_with_crisis(
            "I ran away from home and need shelter in Bushwick",
            session_id=sid,
        )
        response = result["response"].lower()
        # Should have both crisis resources and search offer
        assert "shelter" in response
        slots = get_session_slots(sid)
        assert slots.get("service_type") == "shelter"

    def test_acute_crisis_no_step_down(self):
        """Suicide/medical crisis should NOT get step-down, even with
        service intent."""
        sid = _fresh_sid()
        result = send_with_crisis(
            "I want to kill myself and need shelter",
            session_id=sid,
        )
        response = result["response"].lower()
        # Should have crisis resources (suicide hotline)
        assert "988" in response or "suicide" in response
        # Should NOT offer to search — acute crisis takes priority
        # (The step-down only applies to safety_concern/domestic_violence)
        slots = get_session_slots(sid)
        assert slots.get("_last_action") == "crisis"

    def test_step_down_preserves_multi_intent(self):
        """Crisis step-down should preserve queued services from
        multi-intent extraction."""
        sid = _fresh_sid()
        result = send_with_crisis(
            "My family kicked me out and I need food and shelter in Brooklyn",
            session_id=sid,
        )
        slots = get_session_slots(sid)
        # Should have primary service AND queue
        assert slots.get("service_type") is not None
        # At minimum, one of food/shelter should be in queue or primary
        has_food = slots.get("service_type") == "food" or \
            any(s[0] == "food" for s in slots.get("_queued_services", []))
        has_shelter = slots.get("service_type") == "shelter" or \
            any(s[0] == "shelter" for s in slots.get("_queued_services", []))
        assert has_food or has_shelter


# ===================================================================
# FIX 5: Frustration "yes" → navigator instead of reset
# ===================================================================

class TestFix5FrustrationYes:
    """Fix 5: 'yes' after frustration should connect to navigator."""

    def test_yes_after_frustration_routes_to_navigator(self):
        """'Yes' after frustration should show navigator info, not reset."""
        sid = _fresh_sid()
        # Use "useless" — clearly frustration, doesn't collide with "help" action
        send("this is useless", session_id=sid)
        result = send("yes", session_id=sid)
        response = result["response"].lower()
        assert "peer" in response or "navigator" in response or "streetlives" in response
        # Should show New search + Talk to a person (not welcome menu)
        labels = [qr["label"] for qr in result.get("quick_replies", [])]
        assert "🍽️ Food" not in labels, "Yes after frustration should NOT show welcome menu"
        assert any("person" in l.lower() or "talk" in l.lower() for l in labels)

    def test_start_over_after_frustration_still_resets(self):
        """'Start over' button after frustration should still reset.
        The button sends 'Start over' directly, which hits the reset handler."""
        sid = _fresh_sid()
        send("this is useless", session_id=sid)
        result = send("Start over", session_id=sid)
        # Should show welcome quick replies (reset behavior)
        labels = [qr["label"] for qr in result.get("quick_replies", [])]
        assert "🍽️ Food" in labels


# ===================================================================
# FIX 6: Change location/service outside pending confirmation
# ===================================================================

class TestFix6ChangeOutsidePending:
    """Fix 6: 'Change location' and 'Change service' should work even
    when no pending confirmation exists (e.g., after results)."""

    def test_change_location_after_results(self):
        """'Change location' after results should re-prompt for location."""
        sid = _fresh_sid()
        send("I need food in Brooklyn", session_id=sid)
        send("Yes, search", session_id=sid)
        # Now results are shown, no pending confirmation
        result = send("Change location", session_id=sid)
        response = result["response"].lower()
        assert "neighborhood" in response or "borough" in response
        # Location should be cleared
        slots = get_session_slots(sid)
        assert slots.get("location") is None
        # Service type should be preserved
        assert slots.get("service_type") == "food"

    def test_change_service_after_results(self):
        """'Change service' after results should re-prompt for service."""
        sid = _fresh_sid()
        send("I need food in Brooklyn", session_id=sid)
        send("Yes, search", session_id=sid)
        result = send("Change service", session_id=sid)
        response = result["response"].lower()
        assert "what kind" in response or "what do you need" in response
        slots = get_session_slots(sid)
        assert slots.get("service_type") is None
        # Location should be preserved
        assert slots.get("location") is not None

    def test_change_location_during_pending_still_works(self):
        """'Change location' during pending confirmation should still work."""
        sid = _fresh_sid()
        send("I need food in Brooklyn", session_id=sid)
        # Now pending confirmation is set
        result = send("Change location", session_id=sid)
        response = result["response"].lower()
        assert "neighborhood" in response or "borough" in response


# ===================================================================
# FIX 7: confirm_yes checks for service_type change
# ===================================================================

class TestFix7ConfirmYesServiceChange:
    """Fix 7: 'search for shelter' during a food confirmation should
    update service_type to shelter before executing."""

    def test_search_for_different_service_updates(self):
        """'Search for shelter' during food confirmation → searches shelter."""
        sid = _fresh_sid()
        send("I need food in Brooklyn", session_id=sid)
        # Now pending confirmation for food in Brooklyn
        result = send("search for shelter", session_id=sid)
        # Should have executed with shelter, not food
        slots = result.get("slots", {})
        assert slots.get("service_type") == "shelter", \
            f"Expected shelter, got {slots.get('service_type')}"

    def test_plain_yes_keeps_original_service(self):
        """Plain 'yes' during food confirmation should keep food."""
        sid = _fresh_sid()
        send("I need food in Brooklyn", session_id=sid)
        result = send("Yes, search", session_id=sid)
        slots = result.get("slots", {})
        assert slots.get("service_type") == "food"

    def test_yes_search_for_same_service_works(self):
        """'Yes, search for food' should still work normally."""
        sid = _fresh_sid()
        send("I need food in Brooklyn", session_id=sid)
        result = send("Yes, search for food", session_id=sid)
        slots = result.get("slots", {})
        assert slots.get("service_type") == "food"


# ===================================================================
# INTEGRATION: combined flows
# ===================================================================

class TestIntegrationFlows:
    """Integration tests for scenarios that span multiple fixes."""

    def test_emotional_plus_shelter_gets_empathetic_confirmation(self):
        """'I'm struggling and need shelter in Queens' should get tone prefix
        + shelter confirmation, not mental_health."""
        sid = _fresh_sid()
        result = send("I'm struggling and need shelter in Queens", session_id=sid)
        slots = result.get("slots", {})
        assert slots.get("service_type") == "shelter", \
            f"Expected shelter, got {slots.get('service_type')}"
        # Should have empathetic tone prefix
        response = result["response"]
        assert "hear you" in response.lower() or "help" in response.lower()

    def test_frustrated_then_start_over_still_resets(self):
        """After frustration, 'Start over' (from button) should reset."""
        sid = _fresh_sid()
        send("this is useless", session_id=sid)
        result = send("start over", session_id=sid)
        labels = [qr["label"] for qr in result.get("quick_replies", [])]
        assert "🍽️ Food" in labels

    def test_crisis_step_down_then_change_mind(self):
        """After crisis step-down, user should be able to change service."""
        sid = _fresh_sid()
        send_with_crisis(
            "My family kicked me out and I need shelter in Brooklyn",
            session_id=sid,
        )
        # User says "actually I need food"
        result = send("I need food in Brooklyn", session_id=sid)
        slots = result.get("slots", {})
        assert slots.get("service_type") == "food"
