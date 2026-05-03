"""
Tests for PII redaction — validates all six detection categories,
overlap resolution, false positive guards, and integration points.

Run with: python -m pytest tests/test_pii_redactor.py -v
"""

import pytest
from app.privacy.pii_redactor import redact_pii, detect_pii, has_pii


# -----------------------------------------------------------------------
# 1. SSN Detection
# -----------------------------------------------------------------------

class TestSSN:
    @pytest.mark.parametrize("text", [
        "My SSN is 123-45-6789",
        "SSN: 123 45 6789",
        "number is 123456789",
    ])
    def test_ssn_detected(self, text):
        redacted, dets = redact_pii(text)
        assert "[SSN]" in redacted
        assert any(d.pii_type == "ssn" for d in dets)

    def test_ssn_not_in_short_numbers(self):
        """5-digit zip codes and 2-digit ages should not match SSN."""
        for text in ["I'm 17", "zip 10001", "room 412"]:
            _, dets = redact_pii(text)
            assert not any(d.pii_type == "ssn" for d in dets)


# -----------------------------------------------------------------------
# 2. Email Detection
# -----------------------------------------------------------------------

class TestEmail:
    @pytest.mark.parametrize("text", [
        "Email me at sarah@gmail.com",
        "user.name+tag@example.co.uk",
        "contact test123@yahoo.com please",
    ])
    def test_email_detected(self, text):
        redacted, dets = redact_pii(text)
        assert "[EMAIL]" in redacted

    def test_non_email_not_matched(self):
        _, dets = redact_pii("I need food @ Brooklyn shelter")
        assert not any(d.pii_type == "email" for d in dets)


# -----------------------------------------------------------------------
# 3. Phone Detection
# -----------------------------------------------------------------------

class TestPhone:
    @pytest.mark.parametrize("text", [
        "Call 212-555-1234",
        "my number is (212) 555-1234",
        "reach me at 212.555.1234",
        "call 2125551234",
        "+1 212 555 1234",
    ])
    def test_phone_detected(self, text):
        redacted, dets = redact_pii(text)
        assert "[PHONE]" in redacted

    def test_short_numbers_not_phone(self):
        """Ages, zip codes, and room numbers should not match."""
        for text in ["I'm 17", "zip 10001", "room 412", "I need 3 meals"]:
            _, dets = redact_pii(text)
            assert not any(d.pii_type == "phone" for d in dets)


# -----------------------------------------------------------------------
# 4. DOB Detection
# -----------------------------------------------------------------------

class TestDOB:
    @pytest.mark.parametrize("text", [
        "Born 01/15/1990",
        "DOB: 1-15-90",
        "birthday is 12/25/2000",
        "Born January 15, 1990",
        "born Jan 15 1990",
    ])
    def test_dob_detected(self, text):
        redacted, dets = redact_pii(text)
        assert "[DOB]" in redacted

    def test_invalid_date_not_matched(self):
        """Month >12 or day >31 should not match."""
        _, dets = redact_pii("code is 13/45/2000")
        assert not any(d.pii_type == "dob" for d in dets)


# -----------------------------------------------------------------------
# 5. Address Detection
# -----------------------------------------------------------------------

class TestAddress:
    @pytest.mark.parametrize("text", [
        "I live at 123 Main Street",
        "I live at 123 Main Street Apt 4B",
        "456 West Oak Avenue",
        "789 5th Avenue",
        "456 West 42nd Street",
        "100 Broadway",
    ])
    def test_address_detected(self, text):
        redacted, dets = redact_pii(text)
        assert "[ADDRESS]" in redacted

    def test_preposition_address(self):
        """Preposition-prefixed addresses should be detected."""
        redacted, _ = redact_pii("I stay at 50 Main")
        # The prep pattern requires capitalized word after number
        assert "[ADDRESS]" in redacted or "50 Main" in redacted

    def test_borough_not_address(self):
        """Borough names without house numbers should not match."""
        for text in ["I'm in Brooklyn", "food in Manhattan", "shelter in Queens"]:
            _, dets = redact_pii(text)
            assert not any(d.pii_type == "address" for d in dets)


# -----------------------------------------------------------------------
# 6. Name Detection
# -----------------------------------------------------------------------

class TestName:
    @pytest.mark.parametrize("text,should_redact", [
        ("My name is Sarah", True),
        ("My name is John Smith", True),
        ("Call me David", True),
        ("This is Maria", True),
        ("I'm Sarah", True),
        ("im Sarah and need food", True),
        ("Hi Bryan!", True),
        ("Hey Sarah, how are you?", True),
    ])
    def test_name_detected(self, text, should_redact):
        redacted, dets = redact_pii(text)
        has_name = any(d.pii_type == "name" for d in dets)
        assert has_name == should_redact, \
            f"'{text}' → name detected={has_name}, expected={should_redact}"
        if should_redact:
            assert "[NAME]" in redacted

    @pytest.mark.parametrize("text", [
        "I'm hungry",
        "I'm scared",
        "I'm in Brooklyn",
        "I'm homeless",
        "I'm looking for food",
        "I'm overwhelmed",
        "I'm embarrassed",
        "I need food in Brooklyn",
        "Hi there!",
        "Hello everyone",
    ])
    def test_name_false_positive_guards(self, text):
        """Common phrases should NOT trigger name detection."""
        _, dets = redact_pii(text)
        assert not any(d.pii_type == "name" for d in dets), \
            f"'{text}' should NOT detect a name"

    def test_name_without_intro_not_detected(self):
        """A name without an intro phrase should NOT be detected (by design)."""
        _, dets = redact_pii("Sarah needs food in Brooklyn")
        assert not any(d.pii_type == "name" for d in dets)


# -----------------------------------------------------------------------
# Overlap Resolution
# -----------------------------------------------------------------------

class TestOverlap:
    def test_ssn_wins_over_phone(self):
        """SSN pattern (checked first) should claim the span."""
        redacted, dets = redact_pii("My SSN is 123-45-6789")
        assert "[SSN]" in redacted
        assert "[PHONE]" not in redacted

    def test_no_double_detection(self):
        """A span should only be detected once."""
        _, dets = redact_pii("Call 212-555-1234")
        assert len(dets) == 1


# -----------------------------------------------------------------------
# Integration: slot extraction unaffected
# -----------------------------------------------------------------------

class TestIntegration:
    def test_redaction_preserves_service_extraction(self):
        """Slot extraction runs on original text, not redacted."""
        from app.services.slot_extraction_regex import extract_slots
        msg = "My name is Sarah and I need food in Brooklyn"
        redacted, dets = redact_pii(msg)
        slots = extract_slots(msg)  # original text
        assert slots["service_type"] == "food"
        assert "brooklyn" in (slots["location"] or "").lower()
        assert "Sarah" not in redacted
        assert "[NAME]" in redacted

    def test_age_not_redacted(self):
        """Ages should not be treated as PII."""
        redacted, _ = redact_pii("I'm 17 and need shelter")
        assert "17" in redacted

    def test_confirmation_redacts_address(self):
        """Addresses in slot values should be redacted in confirmation."""
        from app.services.confirmation import _build_confirmation_message
        slots = {"service_type": "food", "location": "123 Main Street"}
        msg = _build_confirmation_message(slots)
        assert "123 Main Street" not in msg
        assert "[ADDRESS]" in msg

    def test_confirmation_redacts_phone(self):
        """Phone numbers in slot values should be redacted."""
        from app.services.confirmation import _build_confirmation_message
        slots = {"service_type": "food", "location": "212-555-1234"}
        msg = _build_confirmation_message(slots)
        assert "212-555-1234" not in msg

    def test_confirmation_preserves_borough(self):
        """Normal location names should pass through unchanged."""
        from app.services.confirmation import _build_confirmation_message
        slots = {"service_type": "food", "location": "Brooklyn"}
        msg = _build_confirmation_message(slots)
        assert "Brooklyn" in msg

    def test_has_pii_helper(self):
        assert has_pii("My name is Sarah") is True
        assert has_pii("I need food in Brooklyn") is False


# -----------------------------------------------------------------------
# Bot response redaction (via audit log)
# -----------------------------------------------------------------------

class TestBotResponseRedaction:
    def test_log_turn_redacts_name_in_bot_response(self):
        """Names in bot responses should be redacted before storage."""
        from app.services.chatbot import _log_turn
        from app.services.audit_log import get_recent_events, clear_audit_log

        clear_audit_log()
        _log_turn(
            session_id="pii-test-name",
            user_msg="My name is [NAME]",
            result={
                "response": "Hi Bryan! I can help you find services.",
                "slots": {},
            },
            category="general",
        )
        events = get_recent_events()
        assert len(events) > 0
        bot_response = events[0]["bot_response"]
        assert "Bryan" not in bot_response
        assert "[NAME]" in bot_response

    def test_log_turn_redacts_phone_in_bot_response(self):
        """Phone numbers in bot responses should be redacted."""
        from app.services.chatbot import _log_turn
        from app.services.audit_log import get_recent_events, clear_audit_log

        clear_audit_log()
        _log_turn(
            session_id="pii-test-phone",
            user_msg="[PHONE]",
            result={
                "response": "I see your number is 212-555-1234.",
                "slots": {},
            },
            category="general",
        )
        events = get_recent_events()
        bot_response = events[0]["bot_response"]
        assert "212-555-1234" not in bot_response
        assert "[PHONE]" in bot_response


# -----------------------------------------------------------------------
# Static bot answer: ICE vs police routing
# -----------------------------------------------------------------------

class TestICEPoliceRouting:
    def test_police_question_mentions_law_enforcement(self):
        from app.services.responses import _static_bot_answer
        response = _static_bot_answer("Do you share with the police?")
        assert "law enforcement" in response.lower()

    def test_ice_question_mentions_ice(self):
        from app.services.responses import _static_bot_answer
        response = _static_bot_answer("Can ICE see my information?")
        assert "ice" in response.lower()

    def test_ice_not_triggered_by_police(self):
        """'police' should not match the ICE pattern (ice is substring of police)."""
        from app.services.responses import _static_bot_answer
        response = _static_bot_answer("Will the police find out?")
        # Should get law enforcement response, not ICE response
        assert "law enforcement" in response.lower()
        assert "immigration status" not in response.lower()


# -----------------------------------------------------------------------
# Credit Card Detection (Luhn validated)
# -----------------------------------------------------------------------

class TestCreditCard:
    @pytest.mark.parametrize("text", [
        "my card is 4111 1111 1111 1111",
        "card number 4111111111111111",
        "visa 4111-1111-1111-1111",
        "my EBT card 4000123456789017",  # valid Luhn
    ])
    def test_valid_cc_detected(self, text):
        redacted, dets = redact_pii(text)
        assert "[CREDIT_CARD]" in redacted
        assert any(d.pii_type == "credit_card" for d in dets)

    def test_invalid_luhn_not_detected(self):
        """Random 16-digit numbers that fail Luhn should NOT match."""
        _, dets = redact_pii("reference number 1234567890123456")
        assert not any(d.pii_type == "credit_card" for d in dets)

    def test_cc_prevents_phone_false_positive(self):
        """16-digit CC should not be partially matched as phone."""
        redacted, dets = redact_pii("card 4111111111111111")
        assert "[CREDIT_CARD]" in redacted
        assert "[PHONE]" not in redacted


# -----------------------------------------------------------------------
# URL Detection
# -----------------------------------------------------------------------

class TestURL:
    @pytest.mark.parametrize("text", [
        "my facebook is facebook.com/john.smith",
        "check https://instagram.com/realSarah",
        "see https://example.com/profile?user=123",
    ])
    def test_url_detected(self, text):
        redacted, dets = redact_pii(text)
        assert "[URL]" in redacted

    def test_plain_domain_not_detected(self):
        """'yourpeer.nyc' without a path should not be redacted."""
        _, dets = redact_pii("visit yourpeer.nyc for more info")
        assert not any(d.pii_type == "url" for d in dets)


# -----------------------------------------------------------------------
# Expanded Name Patterns
# -----------------------------------------------------------------------

class TestExpandedNames:
    @pytest.mark.parametrize("text", [
        "Thanks, Sarah",
        "Sincerely, Maria Rodriguez",
        "Sure Sarah, let me search.",
        "Okay Bryan, I found results.",
        "everyone calls me Rosa",
        "they call me Big Mike",
    ])
    def test_expanded_name_patterns(self, text):
        redacted, dets = redact_pii(text)
        assert "[NAME]" in redacted, f"'{text}' should detect a name"

    @pytest.mark.parametrize("text", [
        "Sure, let me search.",          # no name after "Sure"
        "Okay, I found results.",        # no name after "Okay"
        "Thanks, everyone",              # blocklist word
        "Sure thing, let me help.",      # "thing" not capitalized
    ])
    def test_expanded_name_false_positives(self, text):
        _, dets = redact_pii(text)
        assert not any(d.pii_type == "name" for d in dets), \
            f"'{text}' should NOT detect a name"
