"""Targeted unit tests for three helpers extracted from `generate_reply`
during the April 2026 decomposition.

These three were called out as the highest-risk extractions because they
either encode a non-obvious precedence order (`_compute_tone_prefix`),
implement a cost-sensitive short-circuit that could silently regress
(`_run_llm_gate`), or use an unusual two-return pattern that a future
contributor could get wrong (`_handle_spanish_detection`).

The broader behavior is covered by the existing conversation-flow tests;
these focus on the specific invariants of each extracted helper.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services import chatbot as chatbot_module
from app.services.chatbot import (
    _SKIP_UNIFIED_ACTIONS,
    _apply_queue_offer,
    _build_db_failure_message,
    _compute_tone_prefix,
    _handle_spanish_detection,
    _run_llm_gate,
)


@pytest.fixture
def llm_enabled(monkeypatch):
    """Enable the LLM gate for a test and inject a mockable ``classify_unified``.

    The real chatbot module imports ``classify_unified`` conditionally — only
    when ``ANTHROPIC_API_KEY`` is set — so in the CI environment (no key)
    the name isn't bound. The gate's first check is ``_USE_LLM`` so nothing
    else runs without a key. This fixture flips both so the gate's
    downstream behavior can be exercised under test.
    """
    mock_classify = MagicMock(return_value=None)
    monkeypatch.setattr(chatbot_module, "_USE_LLM", True)
    monkeypatch.setattr(chatbot_module, "classify_unified", mock_classify,
                        raising=False)
    return mock_classify


# -----------------------------------------------------------------------
# _compute_tone_prefix — precedence ladder
# -----------------------------------------------------------------------

class TestComputeTonePrefix:
    """The precedence order is load-bearing. Tests enumerate each tier and
    a few interactions between tiers."""

    def test_shame_beats_emotional_tone(self):
        """Shame detection must win over a generic emotional tone."""
        prefix, ctx = _compute_tone_prefix(
            message="I'm so embarrassed to need food",
            response_tone="emotional",
            is_service_flow=True,
            prior_emotional_context=None,
        )
        assert "real strength" in prefix
        assert ctx == "shame"

    def test_medical_urgent_requires_both_signals(self):
        """Medical urgency must fire only when BOTH a depletion phrase AND
        a medical keyword are present (prevents false positives on generic
        'ran out of' messages)."""
        # Depletion only — no medical keyword — should NOT fire
        prefix, ctx = _compute_tone_prefix(
            message="I ran out of food money",
            response_tone=None,
            is_service_flow=True,
            prior_emotional_context=None,
        )
        assert "urgent" not in prefix.lower()

        # Medical keyword only — no depletion — should NOT fire
        prefix, ctx = _compute_tone_prefix(
            message="I take insulin daily",
            response_tone=None,
            is_service_flow=True,
            prior_emotional_context=None,
        )
        assert "urgent" not in prefix.lower()

        # Both signals — SHOULD fire
        prefix, ctx = _compute_tone_prefix(
            message="I ran out of insulin",
            response_tone=None,
            is_service_flow=True,
            prior_emotional_context=None,
        )
        assert "urgent" in prefix.lower()
        assert ctx == "medical_urgent"

    def test_medical_urgent_requires_service_flow(self):
        """Medical urgency only fires during service flows — outside service
        flow, the message gets no special prefix (routed to general handler)."""
        prefix, ctx = _compute_tone_prefix(
            message="I ran out of insulin",
            response_tone=None,
            is_service_flow=False,
            prior_emotional_context=None,
        )
        # is_service_flow=False → no prefix of any kind
        assert prefix == ""
        assert ctx is None

    def test_sensitive_context_overrides_everything(self):
        """Sensitive context (foster care, fleeing, just got out of jail)
        runs LAST and overrides any earlier prefix. Also overrides ctx."""
        # Shame + sensitive context — sensitive wins
        prefix, ctx = _compute_tone_prefix(
            message="I'm embarrassed, just got out of prison and need food",
            response_tone="emotional",
            is_service_flow=True,
            prior_emotional_context=None,
        )
        assert "difficult situation" in prefix
        assert ctx == "sensitive"

    def test_sensitive_context_fires_even_outside_service_flow(self):
        """Sensitive context is universal — shouldn't be gated on is_service_flow."""
        prefix, ctx = _compute_tone_prefix(
            message="aging out of foster care",
            response_tone=None,
            is_service_flow=False,
            prior_emotional_context=None,
        )
        assert "difficult situation" in prefix
        assert ctx == "sensitive"

    def test_prior_emotional_context_continuity(self):
        """Second-turn messages inherit prior emotional context as a
        continuity prefix, not a full empathic response."""
        prefix, ctx = _compute_tone_prefix(
            message="in brooklyn",  # routine follow-up, no new signal
            response_tone=None,
            is_service_flow=True,
            prior_emotional_context="shame",
        )
        assert prefix == "Still here with you. "
        # Continuity prefix does NOT re-assert the context
        assert ctx is None

        prefix, ctx = _compute_tone_prefix(
            message="in queens",
            response_tone=None,
            is_service_flow=True,
            prior_emotional_context="medical_urgent",
        )
        assert "right place" in prefix.lower()

    def test_baseline_warmth_on_routine_service_flow(self):
        """When no emotional signal fires and it's a service flow, a random
        warmth prefix is emitted (to avoid 'functional but flat' tone)."""
        # Patch the RNG source so the test is deterministic
        with patch("app.services.chatbot.random_warmth_prefix",
                   return_value="Got it — "):
            prefix, ctx = _compute_tone_prefix(
                message="food in brooklyn",
                response_tone=None,
                is_service_flow=True,
                prior_emotional_context=None,
            )
        assert prefix == "Got it — "
        assert ctx is None

    def test_non_service_flow_non_sensitive_gets_empty_prefix(self):
        """Outside a service flow, with no sensitive context, prefix is empty."""
        prefix, ctx = _compute_tone_prefix(
            message="hello there",
            response_tone=None,
            is_service_flow=False,
            prior_emotional_context=None,
        )
        assert prefix == ""
        assert ctx is None


# -----------------------------------------------------------------------
# _run_llm_gate — short-circuit invariants
# -----------------------------------------------------------------------

class TestRunLLMGate:
    """The gate must skip the LLM call when regex/semantic routing already
    produced a confident answer. Missing the short-circuit would burn API
    calls on every small-talk message."""

    def test_skip_when_has_service_intent(self, llm_enabled):
        """If earlier tiers resolved service intent, the LLM gate should
        not run — regardless of other signals."""
        has_si, action, src, tone, llm_action = _run_llm_gate(
            message="I need food in Brooklyn with cats and dogs",
            early_extracted={"service_type": "food"},
            has_service_intent=True,
            action_pre=None,
            regex_tone_pre=None,
            extraction_source="regex",
        )
        llm_enabled.assert_not_called()
        assert has_si is True
        assert tone is None

    def test_skip_on_short_messages(self, llm_enabled):
        """Messages under 4 words shouldn't trigger the expensive LLM gate."""
        _run_llm_gate(
            message="hey there",
            early_extracted={},
            has_service_intent=False,
            action_pre=None,
            regex_tone_pre=None,
            extraction_source=None,
        )
        llm_enabled.assert_not_called()

    @pytest.mark.parametrize("action", sorted(_SKIP_UNIFIED_ACTIONS))
    def test_skip_unified_actions_short_circuit(self, action, llm_enabled):
        """Each member of _SKIP_UNIFIED_ACTIONS must actually short-circuit."""
        _run_llm_gate(
            message="a pretty long message to avoid word-count skip",
            early_extracted={},
            has_service_intent=False,
            action_pre=action,
            regex_tone_pre=None,
            extraction_source=None,
        )
        assert not llm_enabled.called, (
            f"Action '{action}' should short-circuit the LLM gate but didn't"
        )

    def test_gate_fires_when_substantive_and_unresolved(self, llm_enabled):
        """When none of the short-circuits apply, the gate should call the LLM."""
        llm_enabled.return_value = {"service_type": "food"}
        has_si, action, src, tone, llm_action = _run_llm_gate(
            message="i really could use some help with groceries",
            early_extracted={},
            has_service_intent=False,
            action_pre=None,
            regex_tone_pre=None,
            extraction_source=None,
        )
        llm_enabled.assert_called_once()
        assert has_si is True
        assert src == "llm_gate"

    def test_gate_never_raises_on_llm_error(self, llm_enabled):
        """A flaky LLM call must not break routing — the gate logs and
        returns None-y values so the caller falls back to regex."""
        llm_enabled.side_effect = RuntimeError("API timeout")
        has_si, action, src, tone, llm_action = _run_llm_gate(
            message="i really could use some help with groceries",
            early_extracted={},
            has_service_intent=False,
            action_pre=None,
            regex_tone_pre=None,
            extraction_source=None,
        )
        assert has_si is False
        assert tone is None
        assert llm_action is None


# -----------------------------------------------------------------------
# _handle_spanish_detection — two-return pattern
# -----------------------------------------------------------------------

class TestHandleSpanishDetection:
    """The helper returns (result, acknowledgment_prefix) where exactly one
    is non-empty. Getting this wrong would either swallow a valid search
    (result when we should have fallen through) or double-post a bilingual
    message (prefix when we should have returned)."""

    def test_english_message_returns_neither(self):
        """A non-Spanish message gets (None, '') — caller proceeds normally."""
        result, prefix = _handle_spanish_detection(
            session_id="s1",
            message="I need food in Brooklyn",
            redacted_message="I need food in Brooklyn",
            existing={},
            has_service_intent=True,
            tone=None,
            request_id="r1",
        )
        assert result is None
        assert prefix == ""

    def test_spanish_only_returns_result(self):
        """Spanish + no service intent → full bilingual message, no prefix."""
        result, prefix = _handle_spanish_detection(
            session_id="s1",
            message="hola, necesito ayuda",
            redacted_message="hola, necesito ayuda",
            existing={},
            has_service_intent=False,
            tone=None,
            request_id="r1",
        )
        assert result is not None
        assert "Lo siento" in result["response"]
        assert prefix == ""

    def test_spanish_with_service_returns_prefix(self):
        """Spanish + service intent → fall through with bilingual prefix."""
        result, prefix = _handle_spanish_detection(
            session_id="s1",
            message="necesito comida en Brooklyn",
            redacted_message="necesito comida en Brooklyn",
            existing={},
            has_service_intent=True,
            tone=None,
            request_id="r1",
        )
        assert result is None  # caller should continue processing
        assert "Spanish" in prefix
        assert "do my best" in prefix

    def test_exactly_one_output_is_ever_non_empty(self):
        """Invariant check: result and prefix are mutually exclusive."""
        for has_si in (True, False):
            for msg in ("hello world", "hola mundo"):
                result, prefix = _handle_spanish_detection(
                    session_id="s1",
                    message=msg,
                    redacted_message=msg,
                    existing={},
                    has_service_intent=has_si,
                    tone=None,
                    request_id="r1",
                )
                # Not both non-empty
                assert not (result is not None and prefix), (
                    f"Both outputs non-empty for msg={msg!r}, has_si={has_si}"
                )


# -----------------------------------------------------------------------
# _apply_queue_offer + _build_db_failure_message — extracted from
# _execute_and_respond during the follow-up decomposition (April 2026)
# -----------------------------------------------------------------------

class TestApplyQueueOffer:
    """Multi-intent queue offer. The helper's interaction with the
    orchestrator's pagination-suppression logic is subtle — see the
    ``queued_before_offer`` snapshot in `_execute_and_respond`. These tests
    document and lock that contract."""

    def test_no_queue_returns_default_quick_replies(self, tmp_path, monkeypatch):
        """No queued services → bot_response unchanged, default QR set."""
        monkeypatch.setattr(chatbot_module, "save_session_slots", lambda *a, **k: None)
        slots = {"service_type": "food", "location": "Brooklyn"}
        resp, qr = _apply_queue_offer(
            session_id="s1",
            slots=slots,
            services_list=[{"id": 1}],
            bot_response="I found 3 option(s) for you:",
        )
        assert resp == "I found 3 option(s) for you:"
        assert {q["value"] for q in qr} == {"Start over", "Connect with peer navigator"}

    def test_empty_services_returns_default_quick_replies(self, monkeypatch):
        """Queue exists but no results → don't offer the next queued search
        (user got zero results, offering more of the same is unhelpful)."""
        monkeypatch.setattr(chatbot_module, "save_session_slots", lambda *a, **k: None)
        slots = {"_queued_services": [("shelter", None, None)]}
        resp, qr = _apply_queue_offer(
            session_id="s1",
            slots=slots,
            services_list=[],
            bot_response="I didn't find any results.",
        )
        assert resp == "I didn't find any results."
        # Default QR, not queue-offer QR
        assert "No thanks" not in {q["value"] for q in qr}

    def test_single_queued_service_offered_and_popped(self, monkeypatch):
        """Single queued service → appended offer, _queued_services popped,
        _queue_offer_pending set."""
        saved = []
        monkeypatch.setattr(chatbot_module, "save_session_slots",
                            lambda sid, s: saved.append(dict(s)))
        slots = {
            "service_type": "food",
            "location": "Brooklyn",
            "_queued_services": [("shelter", None, None)],
        }
        resp, qr = _apply_queue_offer(
            session_id="s1",
            slots=slots,
            services_list=[{"id": 1}],
            bot_response="I found 3 option(s) for you:",
        )
        assert "You also mentioned" in resp
        assert "shelter" in resp.lower()
        # Queue popped; offer pending flag set
        assert "_queued_services" not in slots
        assert slots["_queue_offer_pending"] is True
        # QR shows yes/no for the queued service
        qr_values = {q["value"] for q in qr}
        assert "No thanks" in qr_values
        assert any("shelter" in v.lower() for v in qr_values)

    def test_multi_queued_first_offered_rest_kept(self, monkeypatch):
        """Multiple queued services → offer the first, retain the rest."""
        monkeypatch.setattr(chatbot_module, "save_session_slots", lambda *a, **k: None)
        slots = {
            "service_type": "food",
            "location": "Brooklyn",
            "_queued_services": [("shelter", None, None), ("legal", None, None)],
        }
        resp, _qr = _apply_queue_offer(
            session_id="s1",
            slots=slots,
            services_list=[{"id": 1}],
            bot_response="I found 3 option(s) for you:",
        )
        # shelter offered, legal still queued
        assert "shelter" in resp.lower()
        assert slots["_queued_services"] == [("legal", None, None)]

    def test_cross_borough_queued_location_surfaced(self, monkeypatch):
        """If the queued service's location differs from the primary search
        location, the offer phrase includes the borough and _queued_location
        is saved for the next search."""
        monkeypatch.setattr(chatbot_module, "save_session_slots", lambda *a, **k: None)
        slots = {
            "service_type": "food",
            "location": "Brooklyn",
            "_queued_services": [("shelter", None, "Manhattan")],
        }
        resp, qr = _apply_queue_offer(
            session_id="s1",
            slots=slots,
            services_list=[{"id": 1}],
            bot_response="I found 3 option(s) for you:",
        )
        assert "in Manhattan" in resp
        assert slots.get("_queued_location") == "Manhattan"
        # Follow-up search value must include the new location
        yes_value = next(q["value"] for q in qr if q["label"].startswith("✅"))
        assert "Manhattan" in yes_value

    def test_pagination_suppression_invariant(self, monkeypatch):
        """REGRESSION GUARD: The orchestrator's 'Show more' suppression
        check reads ``queued_before_offer`` (pre-helper snapshot), not
        ``slots.get("_queued_services")`` after the helper. This test
        documents that contract by verifying the helper pops the queue
        even when a single item was queued — meaning a post-call
        ``slots.get()`` would return ``[]`` (falsy) and incorrectly add
        "Show more" alongside the queue-offer yes/no buttons.
        """
        monkeypatch.setattr(chatbot_module, "save_session_slots", lambda *a, **k: None)
        slots = {
            "service_type": "food",
            "location": "Brooklyn",
            "_queued_services": [("shelter", None, None)],
        }
        _apply_queue_offer(
            session_id="s1",
            slots=slots,
            services_list=[{"id": 1}],
            bot_response="I found 3 option(s) for you:",
        )
        # Queue is now empty — a naive post-call read would think
        # "no queue" and add "Show more". The orchestrator snapshots
        # BEFORE the call to avoid this.
        assert slots.get("_queued_services") in (None, [])


class TestBuildDbFailureMessage:
    """The DB-failure message must NEVER reach the LLM code path — when
    the DB is down, LLM-generated follow-ups produce a confirmation loop.
    These tests lock down the static-message + fail-count escalation."""

    def test_first_failure_offers_retry_and_link(self, monkeypatch):
        monkeypatch.setattr(chatbot_module, "save_session_slots", lambda *a, **k: None)
        slots = {}
        msg = _build_db_failure_message("s1", slots)
        assert "try again" in msg.lower()
        assert "yourpeer.nyc" in msg
        assert slots["_search_fail_count"] == 1

    def test_second_failure_escalates_language(self, monkeypatch):
        """Second+ failure uses stronger 'still having trouble' language
        and drops 'try again in a moment' — don't pretend it's transient."""
        monkeypatch.setattr(chatbot_module, "save_session_slots", lambda *a, **k: None)
        slots = {"_search_fail_count": 1}
        msg = _build_db_failure_message("s1", slots)
        assert "still having trouble" in msg.lower()
        assert slots["_search_fail_count"] == 2

    def test_fail_count_monotonic(self, monkeypatch):
        """Every call increments — never resets on the failure path."""
        monkeypatch.setattr(chatbot_module, "save_session_slots", lambda *a, **k: None)
        slots = {"_search_fail_count": 5}
        _build_db_failure_message("s1", slots)
        assert slots["_search_fail_count"] == 6

    def test_message_never_calls_llm(self):
        """Invariant: the function should be pure string-selection logic.
        If someone ever adds an LLM call here, this test will need the
        mock to fire — and the import would show up in coverage."""
        import inspect
        src = inspect.getsource(_build_db_failure_message)
        # Simple source-level check: no claude / LLM calls
        assert "claude_reply" not in src
        assert "classify_unified" not in src
        assert "_fallback_response" not in src
