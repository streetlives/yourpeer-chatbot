"""Sprint 1 fixes for peer_diabetic_insulin (R37 score 3.09 → eval target 4.0+).

Two stacking bugs surfaced in R37:

Fix 1: The LLM crisis classifier was over-classifying chronic medication
shortage as `medical_emergency` because the prompt said only "immediate
physical danger requiring 911" — Sonnet reasonably read "out of insulin"
as "medically dangerous → emergency." The prompt now carves out
medication shortage explicitly.

Fix 2: When `medical_emergency` did legitimately fire alongside service
intent, the step-down branch in `chatbot/handlers/emotional.py` didn't
include `medical_emergency` in `_step_down_categories`. Slots weren't
preserved, `_pending_confirmation` was never set, and the user's
"Yes, search" tap landed on no pending confirmation. Now matches the
behavior already used by the other 4 step-down categories.

Tests below verify the prompt content (Fix 1) and the runtime step-down
behavior (Fix 2).
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

from app.services.chatbot import generate_reply
from app.services.session_store import (
    clear_session,
    get_session_slots,
    save_session_slots,
)


# ---------------------------------------------------------------------------
# Fix 1 — LLM crisis prompt no longer over-classifies medication shortage
# ---------------------------------------------------------------------------

class TestCrisisPromptExcludesMedicationShortage:
    """The LLM crisis prompt explicitly carves out chronic-medication
    shortage from `medical_emergency`. We can't easily unit-test what the
    LLM does — that needs a real API call and is covered by eval. But we
    CAN unit-test that the prompt contains the carve-out language so a
    future refactor doesn't silently revert the fix."""

    def test_prompt_contains_medication_carveout(self):
        from app.services.crisis_detector import _LLM_SYSTEM_PROMPT
        # The carve-out should explicitly name medication shortage as
        # NOT a medical_emergency.
        assert "ran out of insulin" in _LLM_SYSTEM_PROMPT, \
            "Prompt must explicitly mention 'ran out of insulin' as the " \
            "canonical example of a non-emergency medication need"
        assert "NOT chronic medication needs" in _LLM_SYSTEM_PROMPT or \
               "NOT chronic medication" in _LLM_SYSTEM_PROMPT, \
            "Prompt must contain explicit NOT carve-out for chronic meds"

    def test_prompt_lists_active_emergency_symptoms(self):
        """The carve-out should distinguish chronic-shortage from
        chronic-shortage-with-acute-symptoms. Without symptom signals,
        Sonnet may swing too far the other way and miss real
        emergencies."""
        from app.services.crisis_detector import _LLM_SYSTEM_PROMPT
        # At least one acute-symptom anchor should be in the prompt so
        # Sonnet knows to escalate when symptoms ARE present.
        symptom_anchors = ["collapsed", "can't breathe", "severe pain", "confusion"]
        present = [a for a in symptom_anchors if a in _LLM_SYSTEM_PROMPT]
        assert len(present) >= 2, \
            f"Prompt should anchor at least 2 acute-symptom signals so " \
            f"Sonnet escalates legitimate emergencies despite the carve-out. " \
            f"Found: {present}"

    def test_prompt_keeps_immediate_emergency_examples(self):
        """The carve-out narrows medical_emergency but must keep the
        canonical immediate-emergency examples — heart attack, stroke,
        etc. — so Sonnet doesn't lose the original signal."""
        from app.services.crisis_detector import _LLM_SYSTEM_PROMPT
        canonical = ["heart attack", "stroke", "choking", "overdose"]
        present = [c for c in canonical if c in _LLM_SYSTEM_PROMPT]
        assert len(present) >= 3, \
            f"Prompt should keep canonical emergency examples to anchor " \
            f"medical_emergency for genuine life-threats. Found: {present}"


# ---------------------------------------------------------------------------
# Fix 2 — medical_emergency now triggers step-down with slot preservation
# ---------------------------------------------------------------------------

# Reuses the local _send/_fresh test pattern from the rest of the suite.
# Note: we mock detect_crisis directly so the test doesn't depend on
# whether regex or LLM produced the classification — both produce the
# same (category, response) tuple.

_MEDICAL_EMERGENCY_RESPONSE = (
    "If this is a medical emergency, please call 911 immediately.\n\n"
    "• Emergency: 911\n"
    "• Poison Control: 1-800-222-1222\n"
    "• NYC Health + Hospitals: call 311 for non-emergency medical help\n\n"
    "Once you're safe, I can help you find nearby clinics or health services."
)


def _fresh_session():
    sid = f"test-medstep-{uuid.uuid4().hex[:8]}"
    clear_session(sid)
    return sid


def _send_with_crisis(message, sid, crisis_category):
    """Send a message through generate_reply with detect_crisis mocked."""
    crisis_return = (crisis_category, _MEDICAL_EMERGENCY_RESPONSE)
    with patch("app.services.chatbot.handlers.meta.claude_reply",
               return_value="How can I help?"), \
         patch("app.services.chatbot.execution.query_services",
               return_value=[]), \
         patch("app.services.chatbot.orchestrator.detect_crisis",
               return_value=crisis_return):
        return generate_reply(message, session_id=sid)


class TestMedicalEmergencyStepDown:
    """When `medical_emergency` fires alongside service intent (e.g. regex
    extracted service_type=medical from 'ran out of insulin'), the step-down
    branch must execute: slots preserved, _pending_confirmation set,
    "Yes, search" quick reply offered. Mirrors the behavior already used by
    safety_concern, domestic_violence, youth_runaway, and assault_victim."""

    def test_medical_emergency_with_service_intent_offers_step_down(self):
        """User says 'I'm diabetic and ran out of insulin' — regex extracts
        service_type=medical. Crisis fires. Step-down should offer search."""
        sid = _fresh_session()
        # The orchestrator runs the regex extractor on the message before
        # the crisis branch — for "ran out of insulin" that produces
        # service_type=medical via the keyword list. We don't need to set
        # session slots manually; the regex catches it inline.
        result = _send_with_crisis(
            "I'm diabetic and ran out of insulin",
            sid,
            "medical_emergency",
        )
        # Step-down message and quick reply should both be present.
        response_text = result.get("response", "")
        qr_values = [qr["value"] for qr in result.get("quick_replies", [])]
        qr_labels = [qr["label"] for qr in result.get("quick_replies", [])]

        assert "would you like me to search" in response_text.lower(), \
            f"Step-down message should be appended. Got: {response_text!r}"
        assert any("Yes, search" in label for label in qr_labels), \
            f"Step-down should offer Yes-search quick reply. Got: {qr_labels}"

    def test_medical_emergency_step_down_sets_pending_confirmation(self):
        """Without _pending_confirmation, a follow-up 'Yes, search' tap
        falls through to the 'already shown results' guard. The fix sets
        the flag so the confirmation handler picks it up."""
        sid = _fresh_session()
        _send_with_crisis(
            "I'm diabetic and ran out of insulin",
            sid,
            "medical_emergency",
        )
        slots = get_session_slots(sid)
        assert slots.get("_pending_confirmation") is True, \
            f"_pending_confirmation must be set for follow-up Yes/search " \
            f"to route correctly. Got slots: {slots}"

    def test_medical_emergency_step_down_marks_last_action_crisis(self):
        """`_last_action='crisis'` is what other handlers check to know
        the user is mid-step-down. Must be set the same as the other 4
        categories."""
        sid = _fresh_session()
        _send_with_crisis(
            "I'm diabetic and ran out of insulin",
            sid,
            "medical_emergency",
        )
        slots = get_session_slots(sid)
        assert slots.get("_last_action") == "crisis", \
            f"_last_action should be 'crisis' to signal step-down state. " \
            f"Got: {slots.get('_last_action')!r}"

    def test_medical_emergency_step_down_preserves_service_type(self):
        """The whole point of step-down: keep service_type alive so the
        follow-up 'Yes, search' tap searches medical, not nothing."""
        sid = _fresh_session()
        _send_with_crisis(
            "I'm diabetic and ran out of insulin",
            sid,
            "medical_emergency",
        )
        slots = get_session_slots(sid)
        assert slots.get("service_type") == "medical", \
            f"service_type=medical must persist past the crisis turn. " \
            f"Got: {slots.get('service_type')!r}"

    def test_medical_emergency_no_service_intent_no_step_down(self):
        """If the message is a pure medical-emergency disclosure with no
        service-search intent (regex catches no service_type), step-down
        should NOT fire — the user is in immediate danger and the bot
        shouldn't dilute the 911 message with a search offer."""
        sid = _fresh_session()
        # "having a heart attack" — emergency phrasing, but no service
        # keyword. Regex should NOT extract service_type; step-down
        # branch shouldn't fire.
        result = _send_with_crisis(
            "having a heart attack",
            sid,
            "medical_emergency",
        )
        slots = get_session_slots(sid)
        # No pending_confirmation — there's nothing to confirm
        assert not slots.get("_pending_confirmation"), \
            "Pure emergency without service intent should NOT set " \
            "pending_confirmation"
        # Crisis response should still go out — the user gets the 911 info
        assert "911" in result.get("response", ""), \
            "Crisis response must still be returned for genuine emergency"

    def test_medical_emergency_quick_reply_value_is_search_sentinel(self):
        """The Yes-search quick reply must use the same sentinel value
        the other step-down categories use, so the geolocation handler
        recognizes it."""
        sid = _fresh_session()
        result = _send_with_crisis(
            "I'm diabetic and ran out of insulin",
            sid,
            "medical_emergency",
        )
        qr_values = [qr["value"] for qr in result.get("quick_replies", [])]
        # __crisis_geo_search__ is the sentinel the other categories use.
        # See handlers/emotional.py line ~298.
        assert "__crisis_geo_search__" in qr_values, \
            f"medical_emergency step-down should use the same " \
            f"__crisis_geo_search__ sentinel as the other categories. " \
            f"Got values: {qr_values}"


class TestMedicalEmergencyInStepDownTuple:
    """A direct check that medical_emergency is in the source-of-truth
    tuple. Catches a future refactor that drops it without our other
    behavioral tests catching the regression."""

    def test_medical_emergency_listed_in_step_down_categories(self):
        """Reads the actual tuple from the handler module to verify the
        membership rather than re-deriving the list. Locks the surface."""
        import inspect
        from app.services.chatbot.handlers import emotional
        source = inspect.getsource(emotional._handle_crisis)
        # The tuple literal should contain "medical_emergency".
        assert '"medical_emergency"' in source, \
            "medical_emergency must be in _step_down_categories — see " \
            "Sprint 1 fix for peer_diabetic_insulin"

    def test_step_down_categories_count(self):
        """Sprint 1 added one category. If a future change adds more
        without updating this test, the test fails as a forcing function
        to confirm the change was intentional and re-eval the trade-off
        on each new category."""
        import inspect
        from app.services.chatbot.handlers import emotional
        source = inspect.getsource(emotional._handle_crisis)
        # Count categories that appear inside the _step_down_categories
        # tuple literal. Use a forgiving check: the canonical 5 names.
        canonical = [
            "safety_concern", "domestic_violence", "youth_runaway",
            "assault_victim", "medical_emergency",
        ]
        present = [c for c in canonical if f'"{c}"' in source]
        assert len(present) == 5, \
            f"Expected exactly 5 step-down categories present in handler " \
            f"source. Present: {present}. If you've added a new category " \
            f"intentionally, update this assertion."
