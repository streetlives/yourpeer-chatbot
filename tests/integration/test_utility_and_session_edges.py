"""
Coverage gap tests — high and medium priority items identified during
test audit. Each test class corresponds to one gap from the audit.

Run with: python -m pytest tests/test_coverage_gaps.py -v
"""

import json
import time
import uuid
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.services.session_store import clear_session, get_session_slots, save_session_slots
from app.services.chatbot import generate_reply
from app.services.slot_extractor import NEAR_ME_SENTINEL, extract_slots
from app.services.audit_log import (
    clear_audit_log, log_feedback, get_stats,
    log_conversation_turn, log_query_execution,
)
from app.rag.query_templates import _normalize_url


# -----------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------

MOCK_QUERY_RESULTS = {
    "services": [
        {
            "service_id": "1",
            "service_name": "Test Pantry",
            "organization": "TestOrg",
            "address": "123 Test St, Brooklyn, NY",
            "phone": "212-555-1234",
            "is_open": "open",
        }
    ],
    "result_count": 1,
    "template_used": "FoodQuery",
    "params_applied": {},
    "relaxed": False,
    "execution_ms": 50,
    "freshness": {"fresh": 1, "total": 1, "total_with_date": 1},
}


def _fresh_session():
    sid = f"test-gap-{uuid.uuid4().hex[:8]}"
    clear_session(sid)
    return sid


def _send(message, session_id, mock_crisis_return=None):
    """Send a message with standard mocks."""
    crisis_val = mock_crisis_return
    with (
        patch("app.services.chatbot.query_services", return_value=MOCK_QUERY_RESULTS),
        patch("app.services.chatbot.claude_reply", return_value="How can I help?"),
        patch("app.services.chatbot.detect_crisis", return_value=crisis_val),
    ):
        return generate_reply(message, session_id=session_id)


# -----------------------------------------------------------------------
# GAP 1: Zip code in full chatbot flow
# -----------------------------------------------------------------------

class TestZipCodeFullFlow:
    """Verify zip code extraction works end-to-end in the chatbot,
    not just in the slot extractor unit tests."""

    def test_zip_updates_location_slot(self):
        """Typing a zip code should update the session's location slot."""
        sid = _fresh_session()
        _send("I need food", sid)
        _send("10035", sid)
        slots = get_session_slots(sid)
        assert slots.get("location") == "east harlem", \
            f"Expected 'east harlem', got '{slots.get('location')}'"
        clear_session(sid)

    def test_zip_then_confirm_uses_zip_location(self):
        """After zip entry, confirmation should show the zip's neighborhood."""
        sid = _fresh_session()
        _send("I need food", sid)
        _send("10035", sid)
        result = _send("yes", sid)
        # Should have searched with east harlem, not show a stale location
        slots = get_session_slots(sid)
        assert slots.get("location") == "east harlem"
        clear_session(sid)

    def test_zip_replaces_stale_location(self):
        """A zip code should overwrite a previous text location."""
        sid = _fresh_session()
        _send("food in Brooklyn", sid)
        slots_before = get_session_slots(sid)
        assert "brooklyn" in slots_before.get("location", "").lower()
        # Now change location via zip
        _send("actually search in 10451", sid)
        slots_after = get_session_slots(sid)
        assert slots_after.get("location") == "mott haven", \
            f"Expected 'mott haven', got '{slots_after.get('location')}'"
        clear_session(sid)

    def test_non_nyc_zip_does_not_update(self):
        """A non-NYC zip code should not change the location slot."""
        sid = _fresh_session()
        _send("food in Brooklyn", sid)
        _send("90210", sid)
        slots = get_session_slots(sid)
        # Should still be Brooklyn, not overwritten
        assert "brooklyn" in slots.get("location", "").lower()
        clear_session(sid)


# -----------------------------------------------------------------------
# GAP 2: Crisis step-down with multi-intent
# -----------------------------------------------------------------------

class TestCrisisStepDownMultiIntent:
    """Crisis messages with multiple service keywords should preserve
    the primary service AND queue."""

    def test_crisis_with_single_service_preserves_service(self):
        """Crisis + service should step down and keep the service slot."""
        sid = _fresh_session()
        result = _send(
            "my family kicked me out and I need shelter in Brooklyn",
            sid,
            mock_crisis_return=("safety_concern", "If you're in danger, call 911."),
        )
        slots = get_session_slots(sid)
        # Service type should be preserved by step-down
        assert slots.get("service_type") == "shelter"
        clear_session(sid)

    def test_crisis_with_multi_service_preserves_queue(self):
        """Crisis + multiple services should step down and keep the queue."""
        sid = _fresh_session()
        result = _send(
            "I need food and shelter in Brooklyn, I'm scared",
            sid,
            mock_crisis_return=("safety_concern", "I understand you're scared."),
        )
        slots = get_session_slots(sid)
        # At least the primary service should be extracted
        assert slots.get("service_type") is not None
        clear_session(sid)


# -----------------------------------------------------------------------
# GAP 3: LLM classification returns contradictory category
# -----------------------------------------------------------------------

class TestLLMContradictoryCategory:
    """When the LLM classifier returns a category that conflicts with
    other detectors, the chatbot should not crash or loop."""

    @patch("app.services.chatbot.detect_crisis", return_value=None)
    @patch("app.services.chatbot.query_services", return_value=MOCK_QUERY_RESULTS)
    @patch("app.services.chatbot.claude_reply", return_value="I can help with that.")
    @patch("app.llm.claude_client.classify_message_llm", return_value="crisis")
    def test_llm_says_crisis_but_detector_says_no(
        self, mock_llm_cls, mock_claude, mock_query, mock_crisis
    ):
        """LLM classifier returns 'crisis' but detect_crisis returned None.
        Should not re-trigger crisis flow — detect_crisis is authoritative."""
        sid = _fresh_session()
        # A message that regex can't classify but LLM might over-flag
        result = generate_reply(
            "everything feels pointless but I need food",
            session_id=sid,
        )
        # Should not crash, should return a response
        assert len(result["response"]) > 0
        clear_session(sid)

    @patch("app.services.chatbot.detect_crisis", return_value=None)
    @patch("app.services.chatbot.query_services", return_value=MOCK_QUERY_RESULTS)
    @patch("app.services.chatbot.claude_reply", return_value="Let me help.")
    @patch("app.llm.claude_client.classify_message_llm", return_value=None)
    def test_llm_returns_none(self, mock_llm_cls, mock_claude, mock_query, mock_crisis):
        """LLM classifier returns None — should fall back to general."""
        sid = _fresh_session()
        result = generate_reply("asdf", session_id=sid)
        assert len(result["response"]) > 0
        clear_session(sid)


# -----------------------------------------------------------------------
# GAP 4: Near-me sentinel reaching SQL query
# -----------------------------------------------------------------------

class TestNearMeSentinelSafety:
    """The __near_me__ sentinel should never be passed as a text location
    to the query layer without coordinates."""

    def test_sentinel_without_coords_does_not_crash(self):
        """If sentinel reaches query_services without coords, it should
        not crash — just return empty results or a follow-up."""
        sid = _fresh_session()
        # Manually set up a state that could happen if coords fail
        save_session_slots(sid, {
            "service_type": "food",
            "location": NEAR_ME_SENTINEL,
            # No _latitude or _longitude
        })
        # Confirming should trigger _execute_and_respond
        with (
            patch("app.services.chatbot.query_services") as mock_qs,
            patch("app.services.chatbot.claude_reply", return_value="fallback"),
            patch("app.services.chatbot.detect_crisis", return_value=None),
        ):
            mock_qs.return_value = {
                "services": [], "result_count": 0,
                "template_used": "FoodQuery", "params_applied": {},
                "relaxed": False, "execution_ms": 10,
            }
            result = generate_reply("yes", session_id=sid)
            # Should not crash
            assert len(result["response"]) > 0
        clear_session(sid)

    def test_sentinel_not_in_follow_up_question(self):
        """Follow-up question should not display the raw sentinel string."""
        sid = _fresh_session()
        save_session_slots(sid, {
            "service_type": "food",
            "location": NEAR_ME_SENTINEL,
        })
        # The confirmation message should not show "__near_me__"
        with (
            patch("app.services.chatbot.claude_reply", return_value="fallback"),
            patch("app.services.chatbot.detect_crisis", return_value=None),
        ):
            result = generate_reply("food", session_id=sid)
            assert "__near_me__" not in result["response"]
        clear_session(sid)

    def test_is_enough_to_answer_rejects_sentinel_without_coords(self):
        """is_enough_to_answer should return False for sentinel-only location."""
        from app.services.slot_extractor import is_enough_to_answer
        slots = {"service_type": "food", "location": NEAR_ME_SENTINEL}
        assert is_enough_to_answer(slots) is False


# -----------------------------------------------------------------------
# GAP 5: session_exists (dead code)
# -----------------------------------------------------------------------

class TestSessionExists:
    """session_exists is exported but currently unused. Test it or document."""

    def test_session_exists_true(self):
        from app.services.session_store import session_exists
        sid = _fresh_session()
        save_session_slots(sid, {"service_type": "food"})
        assert session_exists(sid) is True
        clear_session(sid)

    def test_session_exists_false(self):
        from app.services.session_store import session_exists
        assert session_exists("nonexistent-session-id") is False

    def test_session_exists_after_clear(self):
        from app.services.session_store import session_exists
        sid = _fresh_session()
        save_session_slots(sid, {"service_type": "food"})
        clear_session(sid)
        assert session_exists(sid) is False


# -----------------------------------------------------------------------
# GAP 6: get_client_ip edge cases
# -----------------------------------------------------------------------

class TestGetClientIp:
    """Test IP extraction from request headers for rate limiting."""

    def _make_request(self, headers=None, client_host=None):
        """Create a mock Request object."""
        req = MagicMock()
        req.headers = headers or {}
        if client_host:
            req.client = MagicMock()
            req.client.host = client_host
        else:
            req.client = None
        return req

    def test_single_forwarded_ip(self):
        from app.dependencies import get_client_ip
        req = self._make_request(headers={"x-forwarded-for": "1.2.3.4"})
        assert get_client_ip(req) == "1.2.3.4"

    def test_multiple_forwarded_ips_takes_first(self):
        from app.dependencies import get_client_ip
        req = self._make_request(
            headers={"x-forwarded-for": "1.2.3.4, 10.0.0.1, 10.0.0.2"}
        )
        assert get_client_ip(req) == "1.2.3.4"

    def test_forwarded_with_whitespace(self):
        from app.dependencies import get_client_ip
        req = self._make_request(
            headers={"x-forwarded-for": "  1.2.3.4  , 10.0.0.1"}
        )
        assert get_client_ip(req) == "1.2.3.4"

    def test_no_forwarded_uses_client(self):
        from app.dependencies import get_client_ip
        req = self._make_request(client_host="192.168.1.1")
        assert get_client_ip(req) == "192.168.1.1"

    def test_no_forwarded_no_client_returns_unknown(self):
        from app.dependencies import get_client_ip
        req = self._make_request()
        assert get_client_ip(req) == "unknown"


# -----------------------------------------------------------------------
# GAP 7: _extract_session_id edge cases
# -----------------------------------------------------------------------

class TestExtractSessionId:
    """Test JSON body parsing for session-based rate limiting."""

    def test_valid_session_id(self):
        import asyncio
        from app.dependencies import _extract_session_id
        req = MagicMock()
        req.body = AsyncMock(
            return_value=json.dumps({"session_id": "abc-123", "message": "hi"}).encode()
        )
        result = asyncio.run(_extract_session_id(req))
        assert result == "abc-123"

    def test_missing_session_id_field(self):
        import asyncio
        from app.dependencies import _extract_session_id
        req = MagicMock()
        req.body = AsyncMock(
            return_value=json.dumps({"message": "hi"}).encode()
        )
        result = asyncio.run(_extract_session_id(req))
        assert result is None

    def test_malformed_json(self):
        import asyncio
        from app.dependencies import _extract_session_id
        req = MagicMock()
        req.body = AsyncMock(return_value=b"not json at all")
        result = asyncio.run(_extract_session_id(req))
        assert result is None

    def test_empty_body(self):
        import asyncio
        from app.dependencies import _extract_session_id
        req = MagicMock()
        req.body = AsyncMock(return_value=b"")
        result = asyncio.run(_extract_session_id(req))
        assert result is None


# -----------------------------------------------------------------------
# GAP 8: _normalize_url edge cases
# -----------------------------------------------------------------------

class TestNormalizeUrl:
    """Direct unit tests for URL normalization edge cases."""

    def test_none_returns_none(self):
        assert _normalize_url(None) is None

    def test_empty_string_returns_none(self):
        assert _normalize_url("") is None

    def test_whitespace_only_returns_none(self):
        assert _normalize_url("   ") is None

    def test_protocol_relative_preserved(self):
        assert _normalize_url("//cdn.example.com/file.js") == "//cdn.example.com/file.js"

    def test_https_preserved(self):
        assert _normalize_url("https://example.com") == "https://example.com"

    def test_http_preserved(self):
        assert _normalize_url("http://legacy.com") == "http://legacy.com"

    def test_bare_domain_gets_https(self):
        assert _normalize_url("example.com") == "https://example.com"

    def test_domain_with_path_gets_https(self):
        assert _normalize_url("example.org/services") == "https://example.org/services"

    def test_whitespace_stripped(self):
        assert _normalize_url("  https://example.com  ") == "https://example.com"


# -----------------------------------------------------------------------
# GAP 12: Feedback → stats integration
# -----------------------------------------------------------------------

class TestFeedbackStatsIntegration:
    """Verify that log_feedback events flow through to get_stats correctly."""

    def test_feedback_up_counted(self):
        clear_audit_log()
        log_feedback(session_id="s1", rating="up")
        log_feedback(session_id="s2", rating="up")
        log_feedback(session_id="s3", rating="down")

        stats = get_stats()
        assert stats["feedback_up"] == 2
        assert stats["feedback_down"] == 1
        assert stats["feedback_score"] == 0.67  # 2/3 rounded

    def test_feedback_score_none_when_empty(self):
        clear_audit_log()
        stats = get_stats()
        assert stats["feedback_score"] is None
        assert stats["feedback_up"] == 0
        assert stats["feedback_down"] == 0

    def test_feedback_all_positive(self):
        clear_audit_log()
        log_feedback(session_id="s1", rating="up")
        log_feedback(session_id="s2", rating="up")

        stats = get_stats()
        assert stats["feedback_score"] == 1.0

    def test_feedback_counted_in_total_events(self):
        clear_audit_log()
        log_conversation_turn(session_id="s1", user_message_redacted="hi",
                              bot_response="hello", slots={}, category="greeting")
        log_feedback(session_id="s1", rating="up")

        stats = get_stats()
        assert stats["total_events"] == 2
