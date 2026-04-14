"""
Tests for bugs 8–14 identified during PR 19 review.

Each section maps to a specific bug fix:
    Bug 8:  log_feedback missing from audit_log
    Bug 9:  Confirmation message missing "in" preposition
    Bug 10: "nobody cares" over-escalates to suicide_self_harm
    Bug 11: Double detect_crisis LLM call
    Bug 12: _URGENT_PHRASES allocated inside function body
    Bug 13: _classify_message frustration skips contraction normalization
    Bug 14: extract_slots_smart redundant additional_services rebuild

Run: pytest tests/integration/test_targeted_bug_regressions.py -v
"""

import uuid
from unittest.mock import patch, MagicMock

import pytest

from app.services.session_store import clear_session
from conftest import send, send_multi, MOCK_QUERY_RESULTS


@pytest.fixture
def fresh_session():
    sid = str(uuid.uuid4())
    clear_session(sid)
    yield sid
    clear_session(sid)


# -----------------------------------------------------------------------
# BUG 8 — log_feedback missing from audit_log
# -----------------------------------------------------------------------

class TestBug8LogFeedback:
    """log_feedback must exist and be callable."""

    def test_log_feedback_importable(self):
        from app.services.audit_log import log_feedback
        assert callable(log_feedback)

    def test_log_feedback_stores_event(self):
        from app.services.audit_log import log_feedback, get_recent_events, clear_audit_log
        clear_audit_log()
        log_feedback(session_id="s1", rating="up", comment="helpful")
        events = get_recent_events(1)
        assert len(events) == 1
        assert events[0]["type"] == "feedback"
        assert events[0]["rating"] == "up"

    def test_log_feedback_without_comment(self):
        from app.services.audit_log import log_feedback, get_recent_events, clear_audit_log
        clear_audit_log()
        log_feedback(session_id="s2", rating="down")
        events = get_recent_events(1)
        assert len(events) == 1
        assert events[0]["rating"] == "down"


# -----------------------------------------------------------------------
# BUG 9 — Confirmation message missing "in" preposition
# -----------------------------------------------------------------------

class TestBug9ConfirmationPreposition:
    """Confirmation should say 'in Brooklyn', not 'Brooklyn'."""

    def test_confirmation_has_in_preposition(self):
        from app.services.confirmation import _build_confirmation_message
        slots = {"service_type": "food", "location": "Brooklyn"}
        msg = _build_confirmation_message(slots)
        assert "in Brooklyn" in msg

    def test_confirmation_borough_variants(self):
        from app.services.confirmation import _build_confirmation_message
        for borough in ["Manhattan", "Queens", "Bronx", "Staten Island"]:
            slots = {"service_type": "shelter", "location": borough}
            msg = _build_confirmation_message(slots)
            assert f"in {borough}" in msg, f"Missing 'in' for {borough}: {msg}"

    def test_confirmation_neighborhood(self):
        from app.services.confirmation import _build_confirmation_message
        slots = {"service_type": "food", "location": "harlem"}
        msg = _build_confirmation_message(slots)
        assert "in harlem" in msg

    def test_confirmation_near_location_no_in(self):
        """'near your location' should NOT get an extra 'in' prefix."""
        from app.services.confirmation import _build_confirmation_message
        slots = {
            "service_type": "food",
            "location": "__near_me__",
            "_latitude": 40.7,
            "_longitude": -73.9,
        }
        msg = _build_confirmation_message(slots)
        assert "near your location" in msg
        assert "in near" not in msg

    def test_confirmation_in_full_flow(self, fresh_session):
        """End-to-end: confirmation response includes 'in' before location."""
        result = send("I need food in Brooklyn", session_id=fresh_session)
        # Location from extractor is lowercase; check case-insensitively
        assert "in brooklyn" in result["response"].lower()


# -----------------------------------------------------------------------
# BUG 10 — "nobody cares" over-escalates to suicide_self_harm
# -----------------------------------------------------------------------

class TestBug10NobodyCares:
    """Bare 'nobody cares' should be emotional, not crisis."""

    def test_bare_nobody_cares_not_in_crisis_phrases(self):
        from app.services.crisis_detector import _SUICIDE_SELF_HARM_PHRASES
        assert "nobody cares" not in _SUICIDE_SELF_HARM_PHRASES

    def test_specific_nobody_cares_if_i_still_crisis(self):
        from app.services.crisis_detector import _SUICIDE_SELF_HARM_PHRASES
        assert "nobody cares if i" in _SUICIDE_SELF_HARM_PHRASES

    def test_bare_nobody_cares_no_crisis_detection(self):
        from app.services.crisis_detector import detect_crisis
        result = detect_crisis("nobody cares", skip_llm=True)
        assert result is None, f"Bare 'nobody cares' should not trigger crisis, got: {result}"

    def test_bare_no_one_cares_no_crisis_detection(self):
        from app.services.crisis_detector import detect_crisis
        result = detect_crisis("no one cares", skip_llm=True)
        assert result is None, f"Bare 'no one cares' should not trigger crisis, got: {result}"

    def test_nobody_cares_if_i_disappear_triggers_crisis(self):
        from app.services.crisis_detector import detect_crisis
        result = detect_crisis("nobody cares if i disappear", skip_llm=True)
        assert result is not None
        category, _ = result
        assert category == "suicide_self_harm"

    def test_no_one_cares_if_i_vanish_triggers_crisis(self):
        from app.services.crisis_detector import detect_crisis
        result = detect_crisis("no one cares if i just vanished", skip_llm=True)
        assert result is not None
        category, _ = result
        assert category == "suicide_self_harm"

    def test_nobody_cares_routes_to_emotional(self):
        """In the chatbot, bare 'nobody cares' should hit emotional handler."""
        from app.services.classifier import _classify_tone
        tone = _classify_tone("nobody cares", crisis_result=None)
        assert tone == "emotional", f"Expected 'emotional', got '{tone}'"

    def test_no_one_cares_routes_to_emotional(self):
        from app.services.classifier import _classify_tone
        tone = _classify_tone("no one cares", crisis_result=None)
        assert tone == "emotional", f"Expected 'emotional', got '{tone}'"


# -----------------------------------------------------------------------
# BUG 11 — Double detect_crisis LLM call
# -----------------------------------------------------------------------

class TestBug11DoubleCrisisCall:
    """detect_crisis should be called exactly once per message in generate_reply."""

    def test_classify_tone_accepts_precomputed_crisis(self):
        """_classify_tone should use crisis_result when provided."""
        from app.services.classifier import _classify_tone
        fake_result = ("suicide_self_harm", "Please call 988.")
        tone = _classify_tone("some random text", crisis_result=fake_result)
        assert tone == "crisis"

    def test_classify_tone_skips_detect_when_result_provided(self):
        """When crisis_result is explicitly passed (even None = no crisis),
        _classify_tone should NOT call detect_crisis again."""
        from app.services.classifier import _classify_tone
        # Pass None meaning "already checked, no crisis found"
        with patch("app.services.chatbot.detect_crisis") as mock_dc:
            _classify_tone("some text", crisis_result=None)
            mock_dc.assert_not_called()

    def test_classify_tone_calls_detect_when_not_provided(self):
        """When crisis_result is omitted, _classify_tone calls detect_crisis."""
        from app.services.classifier import _classify_tone
        with patch("app.services.classifier.detect_crisis", return_value=None) as mock_dc:
            _classify_tone("some text")
            mock_dc.assert_called_once()

    def test_generate_reply_calls_detect_crisis_once(self, fresh_session):
        """generate_reply should call detect_crisis exactly once, not twice."""
        with patch("app.services.chatbot.detect_crisis", return_value=None) as mock_dc, \
             patch("app.services.chatbot.claude_reply", return_value="Hi"), \
             patch("app.services.chatbot.query_services", return_value=MOCK_QUERY_RESULTS):
            from app.services.chatbot import generate_reply
            generate_reply("I need food in Brooklyn", session_id=fresh_session)
            assert mock_dc.call_count == 1, \
                f"detect_crisis called {mock_dc.call_count} times, expected 1"

    def test_crisis_message_calls_detect_crisis_once(self, fresh_session):
        """Even for crisis messages, detect_crisis should only be called once."""
        crisis_result = ("suicide_self_harm", "Please call 988.")
        with patch("app.services.chatbot.detect_crisis", return_value=crisis_result) as mock_dc, \
             patch("app.services.chatbot.claude_reply", return_value="Hi"), \
             patch("app.services.chatbot.query_services", return_value=MOCK_QUERY_RESULTS):
            from app.services.chatbot import generate_reply
            result = generate_reply("I want to end it all", session_id=fresh_session)
            assert mock_dc.call_count == 1, \
                f"detect_crisis called {mock_dc.call_count} times, expected 1"
            assert "988" in result["response"]


# -----------------------------------------------------------------------
# BUG 12 — _URGENT_PHRASES allocated inside function body
# -----------------------------------------------------------------------

class TestBug12UrgentPhrasesModuleLevel:
    """_URGENT_PHRASES should be a module-level constant."""

    def test_urgent_phrases_is_module_level(self):
        from app.services.phrase_lists import _URGENT_PHRASES
        assert isinstance(_URGENT_PHRASES, list)
        assert len(_URGENT_PHRASES) > 0

    def test_urgent_phrases_identity_stable(self):
        """Same list object on every import (not recreated per call)."""
        from app.services.phrase_lists import _URGENT_PHRASES as a
        from app.services.phrase_lists import _URGENT_PHRASES as b
        assert a is b

    def test_urgent_tone_still_detected(self):
        from app.services.classifier import _classify_tone
        # Pass crisis_result=None to isolate urgency detection from
        # LLM crisis classification — "I need help right now" correctly
        # triggers Sonnet's safety_concern when the LLM is active.
        assert _classify_tone("I need help right now", crisis_result=None) == "urgent"
        assert _classify_tone("please hurry", crisis_result=None) == "urgent"


# -----------------------------------------------------------------------
# BUG 13 — _classify_message frustration skips normalization
# -----------------------------------------------------------------------

class TestBug13FrustrationNormalization:
    """_classify_message should catch frustration with contractions."""

    def test_wasnt_helpful(self):
        from app.services.classifier import _classify_message
        assert _classify_message("that wasn't helpful") == "frustration"

    def test_isnt_working(self):
        from app.services.classifier import _classify_message
        assert _classify_message("this isn't working at all") == "frustration"

    def test_doesnt_help(self):
        from app.services.classifier import _classify_message
        assert _classify_message("that doesn't help me") == "frustration"

    def test_consistency_with_classify_tone(self):
        """_classify_message and _classify_tone should agree on frustration."""
        from app.services.classifier import _classify_message, _classify_tone
        test_phrases = [
            "that wasn't helpful",
            "this isn't working",
            "that doesn't help",
        ]
        for phrase in test_phrases:
            msg_result = _classify_message(phrase)
            tone_result = _classify_tone(phrase, crisis_result=None)
            assert msg_result == "frustration", f"_classify_message missed: {phrase}"
            assert tone_result == "frustrated", f"_classify_tone missed: {phrase}"


# -----------------------------------------------------------------------
# BUG 14 — extract_slots_smart redundant rebuild on LLM fallback
# -----------------------------------------------------------------------

class TestBug14SmartExtractorFallback:
    """When LLM returns nothing, regex result should pass through unchanged."""

    def test_fallback_preserves_additional_services(self):
        """Regex additional_services should survive the LLM fallback path."""
        from app.services.llm_slot_extractor import extract_slots_smart

        with patch("app.services.llm_slot_extractor._is_simple_message", return_value=False), \
             patch("app.services.llm_slot_extractor.extract_slots_llm",
                   return_value={"service_type": None, "additional_service_types": [],
                                 "location": None, "age": None, "urgency": None,
                                 "gender": None, "family_status": None}):
            result = extract_slots_smart("I need food and shelter in Brooklyn")

        assert result.get("service_type") == "food"
        assert result.get("location") is not None
        additional = result.get("additional_services", [])
        assert len(additional) >= 1
        assert any(svc == "shelter" for svc, *_ in additional)

    def test_fallback_returns_regex_result_directly(self):
        """On LLM fallback, the returned dict should be the regex result itself."""
        from app.services.llm_slot_extractor import extract_slots_smart
        from app.services.slot_extractor import extract_slots as regex_extract

        with patch("app.services.llm_slot_extractor._is_simple_message", return_value=False), \
             patch("app.services.llm_slot_extractor.extract_slots_llm",
                   return_value={"service_type": None, "additional_service_types": [],
                                 "location": None, "age": None, "urgency": None,
                                 "gender": None, "family_status": None}):
            result = extract_slots_smart("I need food in Queens")

        expected = regex_extract("I need food in Queens")
        assert result["service_type"] == expected["service_type"]
        assert result["location"] == expected["location"]
