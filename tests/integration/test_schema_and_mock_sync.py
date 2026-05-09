"""
Boundary Drift Detection Tests

These tests prevent silent data loss at serialization boundaries by
asserting that mock fixtures, Pydantic models, SQL queries, and
format functions all agree on the same field set.

The Pydantic ServiceCard bug (fields silently stripped) was caused by:
1. New fields added to format_service_card() and SQL
2. NOT added to the Pydantic ServiceCard model
3. No test noticed because all chatbot tests use generate_reply()
   (raw dict) and never pass through ChatResponse.model_dump()

These tests make such drift impossible: adding a field anywhere
without updating the other layers causes an immediate test failure.

Run: python -m pytest tests/integration/test_schema_and_mock_sync.py -v
"""

import re

import pytest

from app.models.chat_models import ServiceCard, QuickReply, ChatResponse
from app.rag.query_templates import build_query, format_service_card
from conftest import MOCK_SERVICE_CARD, MOCK_QUERY_RESULTS


# ---------------------------------------------------------------------------
# 1. MOCK ↔ PYDANTIC — mock fixtures must match model fields
# ---------------------------------------------------------------------------

class TestMockDrift:
    """Mock fixtures in conftest.py must stay in sync with Pydantic models."""

    def test_mock_service_card_has_all_pydantic_fields(self):
        """MOCK_SERVICE_CARD must have every field that ServiceCard declares.

        If this fails, a new field was added to ServiceCard but not to the
        mock. Tests using the mock won't exercise the new field.
        """
        pydantic_fields = set(ServiceCard.model_fields.keys())
        mock_fields = set(MOCK_SERVICE_CARD.keys())
        missing = pydantic_fields - mock_fields
        assert not missing, (
            f"MOCK_SERVICE_CARD is missing Pydantic fields: {sorted(missing)}. "
            f"Add them to MOCK_SERVICE_CARD in conftest.py."
        )

    def test_mock_service_card_has_no_extra_fields(self):
        """MOCK_SERVICE_CARD must not have fields that ServiceCard doesn't declare.

        If this fails, a field was removed from ServiceCard but the mock
        still has it — the mock is testing a field that doesn't exist.
        """
        pydantic_fields = set(ServiceCard.model_fields.keys())
        mock_fields = set(MOCK_SERVICE_CARD.keys())
        extra = mock_fields - pydantic_fields
        assert not extra, (
            f"MOCK_SERVICE_CARD has fields not in ServiceCard: {sorted(extra)}. "
            f"Remove them from MOCK_SERVICE_CARD in conftest.py."
        )

    def test_mock_query_results_has_required_keys(self):
        """MOCK_QUERY_RESULTS must have all keys that query_services() returns."""
        required = {
            "services", "result_count", "template_used",
            "params_applied", "relaxed", "execution_ms",
        }
        mock_keys = set(MOCK_QUERY_RESULTS.keys())
        missing = required - mock_keys
        assert not missing, (
            f"MOCK_QUERY_RESULTS is missing keys: {sorted(missing)}. "
            f"Add them to MOCK_QUERY_RESULTS in conftest.py."
        )


# ---------------------------------------------------------------------------
# 2. FORMAT_SERVICE_CARD ↔ PYDANTIC — output must match model exactly
# ---------------------------------------------------------------------------

class TestFormatCardPydanticSync:
    """format_service_card() output keys must match ServiceCard fields."""

    def _make_dummy_row(self):
        """Build a row with all SQL column aliases set to plausible values."""
        sql, _ = build_query("food", {"city": "Brooklyn"})
        aliases = set(re.findall(r'AS\s+(\w+)', sql))
        row = {a: None for a in aliases}
        row["service_name"] = "Test Service"
        row["service_id"] = 1
        return row

    def test_format_output_matches_pydantic_fields(self):
        """Every key in format_service_card output must be a ServiceCard field."""
        row = self._make_dummy_row()
        card = format_service_card(row)
        card_keys = set(card.keys())
        pydantic_keys = set(ServiceCard.model_fields.keys())

        extra = card_keys - pydantic_keys
        assert not extra, (
            f"format_service_card returns keys not in ServiceCard: {sorted(extra)}. "
            f"Add them to ServiceCard in chat_models.py or they'll be silently stripped."
        )

        missing = pydantic_keys - card_keys
        assert not missing, (
            f"ServiceCard has fields not returned by format_service_card: {sorted(missing)}. "
            f"Either add them to format_service_card or remove from ServiceCard."
        )

    def test_format_output_survives_pydantic_roundtrip(self):
        """Data from format_service_card must survive ServiceCard.model_dump()."""
        row = self._make_dummy_row()
        row["also_available"] = ["Shower", "Health"]
        row["last_validated_at"] = None  # common case
        row["requires_membership"] = True

        card_dict = format_service_card(row)
        pydantic_card = ServiceCard(**card_dict)
        roundtrip = pydantic_card.model_dump()

        for key in card_dict:
            assert key in roundtrip, f"Field '{key}' lost during Pydantic roundtrip"
            assert roundtrip[key] == card_dict[key], (
                f"Field '{key}' changed during roundtrip: "
                f"{card_dict[key]!r} → {roundtrip[key]!r}"
            )


# ---------------------------------------------------------------------------
# 3. SQL ↔ FORMAT_SERVICE_CARD — SQL aliases must cover all reads
# ---------------------------------------------------------------------------

class TestSqlFormatSync:
    """format_service_card must only read columns that exist in the SQL query."""

    def test_format_reads_subset_of_sql_aliases(self):
        """Every row.get() key in format_service_card must be a SQL alias.

        If this fails, format_service_card is reading a column that the
        SQL query doesn't return — the value will always be None.
        """
        sql, _ = build_query("food", {"city": "Brooklyn"})
        sql_aliases = set(re.findall(r'AS\s+(\w+)', sql))

        import inspect
        source = inspect.getsource(format_service_card)
        row_reads = set(re.findall(r'row(?:\.get)?\(\s*["\'](\w+)', source))

        # These are internal keys not from SQL:
        non_sql_keys = {"isoformat"}  # hasattr check, not a column
        row_reads -= non_sql_keys

        missing = row_reads - sql_aliases
        assert not missing, (
            f"format_service_card reads columns not in SQL: {sorted(missing)}. "
            f"Either add them to _BASE_QUERY or remove from format_service_card."
        )


# ---------------------------------------------------------------------------
# 4. GENERATE_REPLY ↔ CHATRESPONSE — response dict must match model
# ---------------------------------------------------------------------------

class TestReplyResponseSync:
    """generate_reply() return keys must match ChatResponse fields."""

    def test_reply_keys_match_chatresponse(self):
        """generate_reply must return all ChatResponse required fields."""
        from unittest.mock import patch
        from app.services.chatbot import generate_reply
        from app.services.session_store import clear_session

        sid = "drift-check"
        clear_session(sid)
        with (
            patch("app.services.chatbot.execution.query_services"),
            patch("app.services.chatbot.handlers.meta.claude_reply", return_value="ok"),
            patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None),
        ):
            result = generate_reply("hello", session_id=sid)
        clear_session(sid)

        reply_keys = set(result.keys())
        response_fields = set(ChatResponse.model_fields.keys())

        # session_id is added by the route, not generate_reply
        response_fields_without_session = response_fields - {"session_id"}
        reply_keys_without_session = reply_keys - {"session_id"}

        missing = response_fields_without_session - reply_keys_without_session
        assert not missing, (
            f"generate_reply() missing ChatResponse fields: {sorted(missing)}. "
            f"The HTTP route will fail when constructing ChatResponse."
        )


# ---------------------------------------------------------------------------
# 5. FULL PIPELINE — end-to-end field preservation
# ---------------------------------------------------------------------------

class TestFullPipeline:
    """Data must survive the complete pipeline:
    format_service_card → generate_reply → ChatResponse.model_dump()
    """

    def test_service_fields_survive_full_pipeline(self):
        """Service card fields must not be lost at any layer."""
        from unittest.mock import patch
        from app.services.chatbot import generate_reply
        from app.services.session_store import clear_session

        # Build a realistic mock with ALL fields populated
        mock_results = {
            "services": [{
                "service_id": "svc-test",
                "service_name": "Full Pipeline Pantry",
                "organization": "Test Org",
                "phone": "555-1234",
                "address": "100 Main St, Brooklyn, NY",
                "also_available": ["Shower", "Clothing Pantry"],
                "last_validated_at": "2026-04-01",
                "requires_membership": True,
                "fees": "Free",
                "is_open": "open",
                "hours_today": "9AM-5PM",
            }],
            "result_count": 1,
            "template_used": "FoodQuery",
            "params_applied": {},
            "relaxed": False,
            "execution_ms": 30,
            "freshness": {"fresh": 1, "total": 1, "total_with_date": 1},
        }

        sid = "pipeline-test"
        clear_session(sid)
        with (
            patch("app.services.chatbot.execution.query_services", return_value=mock_results),
            patch("app.services.chatbot.handlers.meta.claude_reply", return_value="ok"),
            patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None),
        ):
            generate_reply("I need food in Brooklyn", session_id=sid)
            result = generate_reply("yes", session_id=sid)
        clear_session(sid)

        # Now simulate the HTTP route serialization
        result["session_id"] = sid
        serialized = ChatResponse(**result).model_dump()

        assert len(serialized["services"]) > 0, "No services in response"
        svc = serialized["services"][0]

        # These fields MUST survive the full pipeline
        assert svc["service_id"] == "svc-test", "service_id lost in pipeline"
        assert svc["also_available"] == ["Shower", "Clothing Pantry"], "also_available lost"
        assert svc["last_validated_at"] == "2026-04-01", "last_validated_at lost"
        assert svc["requires_membership"] is True, "requires_membership lost"
        assert svc["service_name"] == "Full Pipeline Pantry", "service_name lost"

    def test_quick_reply_href_survives_full_pipeline(self):
        """QuickReply href must survive ChatResponse serialization."""
        result = {
            "session_id": "test",
            "response": "Details below",
            "follow_up_needed": False,
            "slots": {},
            "services": [],
            "result_count": 0,
            "relaxed_search": False,
            "quick_replies": [
                {"label": "📞 Call Place", "value": "Call 555", "href": "tel:555"},
                {"label": "🔍 New search", "value": "Start over"},
            ],
        }
        serialized = ChatResponse(**result).model_dump()
        assert serialized["quick_replies"][0]["href"] == "tel:555"
        assert serialized["quick_replies"][1]["href"] is None


# ---------------------------------------------------------------------------
# 6. ADMIN API — get_stats() shape must match TypeScript AdminStats
# ---------------------------------------------------------------------------

class TestAdminStatsDrift:
    """Backend get_stats() must return keys the frontend TypeScript expects.

    The frontend AdminStats interface defines what the admin dashboard
    renders. If the backend changes a key name or drops a field, the
    dashboard silently shows undefined/NaN instead of crashing with a
    clear error.
    """

    # These are the top-level keys from the TypeScript AdminStats interface.
    # Update this set when the TypeScript interface changes.
    _TS_ADMIN_STATS_KEYS = {
        "unique_sessions", "total_turns", "total_queries", "total_crises",
        "total_crises_24h",
        "crises_by_category", "crises_by_category_24h",
        "total_escalations", "total_resets", "service_intent_sessions",
        "relaxed_query_rate", "feedback_up", "feedback_down", "feedback_score",
        "slot_confirmation_rate", "slot_correction_rate",
        "data_freshness_rate", "data_freshness_detail",
        "conversation_quality", "confirmation_breakdown",
        "category_distribution", "service_type_distribution",
        "routing", "tone_distribution", "multi_intent",
    }

    _TS_CONFIRMATION_BREAKDOWN_KEYS = {
        "confirm", "change_service", "change_location", "deny",
        "total_actions", "confirm_rate",
        "sessions_at_confirmation", "sessions_abandoned", "abandon_rate",
    }

    _TS_CONVERSATION_QUALITY_KEYS = {
        "emotional_sessions", "emotional_rate",
        "emotional_to_escalation", "emotional_to_service",
        "bot_question_turns", "bot_question_rate", "bot_question_sessions",
        "bot_question_to_frustration",
        "conversational_discovery", "conversational_discovery_rate",
    }

    _TS_TONE_DISTRIBUTION_KEYS = {
        "tones", "total_with_tone", "turns_without_tone",
    }

    _TS_MULTI_INTENT_KEYS = {
        "queue_offers", "queue_declines",
    }

    def test_top_level_keys_present(self):
        """get_stats() must return all keys TypeScript AdminStats expects."""
        from app.services.audit_log import get_stats
        stats = get_stats()
        backend_keys = set(stats.keys())
        missing = self._TS_ADMIN_STATS_KEYS - backend_keys
        assert not missing, (
            f"get_stats() missing keys expected by TypeScript AdminStats: {sorted(missing)}"
        )

    def test_confirmation_breakdown_shape(self):
        from app.services.audit_log import get_stats
        breakdown = get_stats()["confirmation_breakdown"]
        missing = self._TS_CONFIRMATION_BREAKDOWN_KEYS - set(breakdown.keys())
        assert not missing, (
            f"confirmation_breakdown missing keys: {sorted(missing)}"
        )

    def test_conversation_quality_shape(self):
        from app.services.audit_log import get_stats
        quality = get_stats()["conversation_quality"]
        missing = self._TS_CONVERSATION_QUALITY_KEYS - set(quality.keys())
        assert not missing, (
            f"conversation_quality missing keys: {sorted(missing)}"
        )

    def test_tone_distribution_shape(self):
        from app.services.audit_log import get_stats
        tone = get_stats()["tone_distribution"]
        missing = self._TS_TONE_DISTRIBUTION_KEYS - set(tone.keys())
        assert not missing, (
            f"tone_distribution missing keys: {sorted(missing)}"
        )

    def test_multi_intent_shape(self):
        from app.services.audit_log import get_stats
        mi = get_stats()["multi_intent"]
        missing = self._TS_MULTI_INTENT_KEYS - set(mi.keys())
        assert not missing, (
            f"multi_intent missing keys: {sorted(missing)}"
        )


# ---------------------------------------------------------------------------
# 7. PERSISTENCE FAILURE ISOLATION — main flow must survive SQLite errors
# ---------------------------------------------------------------------------

class TestPersistenceFailureIsolation:
    """When SQLite is broken, the main application flow must continue.

    persistence.py functions have internal try/except blocks that log
    errors without propagating. These tests verify that contract by
    actually breaking SQLite and running the main flow.
    """

    def _corrupt_db(self):
        """Set up a SQLite DB then drop all tables."""
        import tempfile
        import os
        from app.services import persistence

        db_path = os.path.join(tempfile.mkdtemp(), "corrupt.db")
        self._original_path = persistence.PILOT_DB_PATH
        self._original_conn = persistence._conn

        persistence.PILOT_DB_PATH = db_path
        persistence._conn = None
        persistence._get_conn()  # creates tables
        # Now corrupt
        persistence._conn.execute("DROP TABLE events")
        persistence._conn.execute("DROP TABLE sessions")
        persistence._conn.execute("DROP TABLE eval_data")
        persistence._conn.commit()

    def _restore_db(self):
        from app.services import persistence
        persistence.close()
        persistence.PILOT_DB_PATH = self._original_path
        persistence._conn = self._original_conn

    def test_log_conversation_turn_survives(self):
        """Logging a turn must not crash when SQLite tables are missing."""
        self._corrupt_db()
        try:
            from app.services.audit_log import log_conversation_turn, get_stats, clear_audit_log
            clear_audit_log()
            # Should not raise
            log_conversation_turn("s1", "hi", "hello", {}, "greeting")
            assert get_stats()["total_turns"] == 1
        finally:
            self._restore_db()

    def test_log_query_execution_survives(self):
        self._corrupt_db()
        try:
            from app.services.audit_log import log_query_execution, get_stats, clear_audit_log
            clear_audit_log()
            log_query_execution("s1", "FoodQuery", {}, 3, False, 40)
            assert get_stats()["total_queries"] == 1
        finally:
            self._restore_db()

    def test_log_feedback_survives(self):
        self._corrupt_db()
        try:
            from app.services.audit_log import log_feedback, get_stats, clear_audit_log
            clear_audit_log()
            log_feedback(session_id="s1", rating="up")
            assert get_stats()["feedback_up"] == 1
        finally:
            self._restore_db()

    def test_save_session_survives(self):
        self._corrupt_db()
        try:
            from app.services.session_store import save_session_slots, get_session_slots, _SESSION_STATE, _lock
            with _lock:
                _SESSION_STATE.clear()
            save_session_slots("s1", {"food": True})
            assert get_session_slots("s1")["food"] is True
        finally:
            self._restore_db()

    def test_clear_session_survives(self):
        self._corrupt_db()
        try:
            from app.services.session_store import save_session_slots, clear_session, session_exists, _SESSION_STATE, _lock
            with _lock:
                _SESSION_STATE.clear()
            save_session_slots("s1", {"food": True})
            clear_session("s1")  # should not raise
            assert not session_exists("s1")
        finally:
            self._restore_db()

    def test_full_generate_reply_survives(self):
        """The entire chatbot flow must work with broken SQLite."""
        self._corrupt_db()
        try:
            from unittest.mock import patch
            from app.services.chatbot import generate_reply
            from app.services.session_store import clear_session

            sid = "persist-fail-test"
            clear_session(sid)
            with (
                patch("app.services.chatbot.execution.query_services", return_value={
                    "services": [{"service_name": "Test", "service_id": "1",
                                  "is_open": None, "hours_today": None,
                                  "phone": "555", "address": "100 Main", "fees": "Free"}],
                    "result_count": 1, "template_used": "FoodQuery",
                    "params_applied": {}, "relaxed": False, "execution_ms": 30,
                    "freshness": {"fresh": 1, "total": 1, "total_with_date": 1},
                }),
                patch("app.services.chatbot.handlers.meta.claude_reply", return_value="ok"),
                patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None),
            ):
                r1 = generate_reply("I need food in Brooklyn", session_id=sid)
                assert "food" in r1["response"].lower()
                r2 = generate_reply("yes", session_id=sid)
                assert r2["result_count"] == 1

            clear_session(sid)
        finally:
            self._restore_db()
