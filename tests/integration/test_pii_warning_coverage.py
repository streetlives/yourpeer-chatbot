"""Tests for PII safety warnings firing on every handler path.

Before May 2026, the PII warning ("I've removed your phone number...")
fired only on the late service-flow path of the orchestrator. A user
sharing PII in turn 3 (after results were delivered) would route
through `_handle_post_results_interaction` and never see the warning,
which surfaced as `pre_llm_redact_phone_in_followup` scoring 3.4 in
both flag-ON and flag-OFF Phase 2 evals.

The fix: every category-specific handler return is now wrapped with
`_apply_pii_warning(_pii_warning, ...)`. These tests verify each
wrap fires correctly when the user shares phone/SSN.

Crisis is intentionally NOT wrapped — safety resources (988, etc.)
must come first; a privacy reminder ahead of crisis hotlines would
invert the priority order. There's a test asserting this remains
the case.
"""

from __future__ import annotations
import sys
import pytest
from unittest.mock import patch
from app.services.chatbot import generate_reply
from app.services.session_store import clear_session
from app.services.chatbot.pipeline import _apply_pii_warning


# Ensure backend is importable
import os
_BACKEND = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "backend")
)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


# ---------------------------------------------------------------------------
# _apply_pii_warning helper — the building block
# ---------------------------------------------------------------------------

class TestApplyPIIWarning:
    """Unit tests for the helper itself."""

    def test_no_warning_returns_response_unchanged(self):
        """When _pii_warning is empty, the helper is a no-op."""
        response = {"response": "Hello, what do you need?"}
        result = _apply_pii_warning("", response)
        assert result is response  # same object — not modified
        assert result["response"] == "Hello, what do you need?"

    def test_warning_prepends_to_response_text(self):
        """When PII was warned about, the warning prepends."""
        warning = "Just a heads up — I've removed your phone number.\n\n"
        response = {"response": "Let's find shelter for you."}
        result = _apply_pii_warning(warning, response)
        assert result["response"].startswith(warning)
        assert "Let's find shelter" in result["response"]

    def test_warning_idempotent_on_already_prepended(self):
        """If the response already starts with the warning, don't double it."""
        warning = "Just a heads up — I've removed your phone number.\n\n"
        already_prepended = warning + "Let's find shelter for you."
        response = {"response": already_prepended}
        result = _apply_pii_warning(warning, response)
        # Should not be doubled
        assert result["response"].count(warning) == 1
        assert result["response"] == already_prepended

    def test_none_response_returns_none(self):
        """Handlers signal 'fall through to next dispatch' by returning None.
        The helper must preserve that signal."""
        result = _apply_pii_warning("any warning", None)
        assert result is None

    def test_response_without_response_key_handled(self):
        """Defensive: if the response dict somehow lacks 'response',
        the helper shouldn't crash."""
        warning = "Warning prefix.\n\n"
        response = {"session_id": "x", "services": []}
        result = _apply_pii_warning(warning, response)
        # Should add "response" key with just the warning
        assert result["response"] == warning


# ---------------------------------------------------------------------------
# End-to-end orchestrator tests — PII warning fires across handler paths
# ---------------------------------------------------------------------------
#
# These exercise generate_reply with messages that route through each
# of the previously-uncovered handlers (post_results, bot_question,
# greeting, etc.) and assert the PII warning appears in the response.
#
# Each test uses a unique session_id to avoid state bleed.

_PHONE_WARNING_PHRASE = "removed your phone number"
_SSN_WARNING_PHRASE = (
    "please don't share your Social Security number"
)


def _has_phone_warning(response_text: str) -> bool:
    """Robust to phrasing edits — checks for the distinctive substring."""
    return _PHONE_WARNING_PHRASE in response_text


def _has_ssn_warning(response_text: str) -> bool:
    return _SSN_WARNING_PHRASE in response_text


@pytest.fixture
def fresh_session(request):
    """A unique session_id per test, cleared on entry and exit."""
    sid = f"test-pii-{request.node.name}"
    clear_session(sid)
    yield sid
    clear_session(sid)


class TestPIIWarningFiresAcrossHandlers:
    """Verify PII warning lands on each handler path."""

    def test_warning_fires_on_post_results_path(self, fresh_session):
        """The motivating bug: turn 3 PII share after results delivered.

        This was `pre_llm_redact_phone_in_followup` scoring 3.4 because
        the post-results handler didn't surface the warning.
        """
        # Set up session as if results were already delivered. We do
        # this by walking through the bot's normal flow.
        with patch(
            "app.services.chatbot.execution.query_services",
            return_value={
                "services": [
                    {"service_name": "Test Shelter", "address": "1 Main",
                     "phone": "555-0001", "fees": "Free",
                     "description": "ok", "is_open": "open",
                     "yourpeer_url": "https://yourpeer.nyc/x"},
                ],
                "result_count": 1,
                "template_used": "ShelterQuery",
                "params_applied": {},
                "relaxed": False,
                "execution_ms": 10,
            },
        ):
            # Turn 1: state intent
            generate_reply("I need shelter in Brooklyn", session_id=fresh_session)
            # Turn 2: confirm
            generate_reply("Yes, search", session_id=fresh_session)
            # Turn 3: share phone — this is the path that was broken
            result = generate_reply(
                "can you call them at 212-555-1212",
                session_id=fresh_session,
            )

        assert _has_phone_warning(result["response"]), (
            f"Post-results handler should fire phone warning. "
            f"Got: {result['response'][:200]}"
        )

    def test_warning_fires_on_greeting_path(self, fresh_session):
        """User greets while sharing PII."""
        result = generate_reply(
            "Hi! My SSN is 123-45-6789",
            session_id=fresh_session,
        )
        assert _has_ssn_warning(result["response"])

    def test_warning_fires_on_thanks_path(self, fresh_session):
        """User says thanks with PII (rare but real)."""
        result = generate_reply(
            "Thanks, my number is 212-555-1234",
            session_id=fresh_session,
        )
        assert _has_phone_warning(result["response"])

    def test_warning_fires_on_bot_question_path(self, fresh_session):
        """User asks about the bot while sharing PII."""
        result = generate_reply(
            "Are you a real person? My phone is 212-555-9999",
            session_id=fresh_session,
        )
        assert _has_phone_warning(result["response"])

    def test_warning_fires_on_general_conversation_path(self, fresh_session):
        """Off-topic message with PII falls through to general handler."""
        result = generate_reply(
            "I love rainbows and my phone is 555-867-5309",
            session_id=fresh_session,
        )
        assert _has_phone_warning(result["response"])

    def test_warning_still_fires_on_service_flow_path(self, fresh_session):
        """The pre-existing path — must not regress under the new wrap."""
        result = generate_reply(
            "I need food in Brooklyn, my number is 212-555-1111",
            session_id=fresh_session,
        )
        assert _has_phone_warning(result["response"])

    def test_warning_not_doubled_on_service_flow_path(self, fresh_session):
        """Idempotency check: the late service-flow prefix injection
        already prepends `_pii_warning`, so when the new wrap fires too,
        the warning must appear exactly once, not twice."""
        result = generate_reply(
            "I need food in Brooklyn, call me at 212-555-2222",
            session_id=fresh_session,
        )
        text = result["response"]
        # Count occurrences of the distinctive warning phrase
        assert text.count(_PHONE_WARNING_PHRASE) == 1, (
            f"Warning should appear exactly once. Got {text.count(_PHONE_WARNING_PHRASE)} "
            f"occurrences. Text: {text[:300]}"
        )


class TestPIIWarningDoesNotInterfereWithCrisis:
    """Crisis must not have the PII warning prepended.

    A user sharing PII in a crisis message ('I'm at 145 Main and I want
    to die, call me at 555-1234') should see the crisis hotline numbers
    FIRST. Prepending a privacy reminder ahead of crisis resources
    inverts the priority order.
    """

    def test_crisis_response_lacks_pii_warning(self, fresh_session):
        """Even when PII is shared with crisis language, the crisis
        response is not preceded by the privacy reminder."""
        result = generate_reply(
            "I want to die, my number is 555-867-5309",
            session_id=fresh_session,
        )
        text = result["response"]
        # Crisis resources should be present
        assert "988" in text or "crisis" in text.lower(), (
            f"Crisis resources must fire. Got: {text[:200]}"
        )
        # The privacy reminder should NOT be the first thing the
        # user sees in a crisis. It may appear later in the response
        # (e.g. if a follow-up is also generated), but the crisis
        # response itself should lead with safety info.
        first_100 = text[:100]
        assert _PHONE_WARNING_PHRASE not in first_100, (
            f"Crisis must lead with safety info, not privacy warning. "
            f"First 100 chars: {first_100}"
        )


class TestPIIWarningOnlyForSensitiveTypes:
    """The warning fires only for SSN and phone — names/addresses/emails
    are quietly redacted but don't trigger the user-facing warning.
    Verifies the wrap doesn't suddenly produce noisy warnings for every
    name-mention."""

    def test_name_share_does_not_trigger_warning(self, fresh_session):
        """User says 'I'm Sarah' — name redacted internally but no warning."""
        result = generate_reply(
            "Hi, I'm Sarah, are you a real person?",
            session_id=fresh_session,
        )
        assert _PHONE_WARNING_PHRASE not in result["response"]
        assert _SSN_WARNING_PHRASE not in result["response"]

    def test_email_share_does_not_trigger_warning(self, fresh_session):
        """Email is redacted but doesn't warn."""
        result = generate_reply(
            "thanks, my email is jane@example.com",
            session_id=fresh_session,
        )
        assert _PHONE_WARNING_PHRASE not in result["response"]
        assert _SSN_WARNING_PHRASE not in result["response"]

    def test_address_share_does_not_trigger_warning(self, fresh_session):
        """Address is redacted but doesn't warn."""
        result = generate_reply(
            "I need food, my address is 145 East 3rd Street",
            session_id=fresh_session,
        )
        assert _PHONE_WARNING_PHRASE not in result["response"]
        assert _SSN_WARNING_PHRASE not in result["response"]
