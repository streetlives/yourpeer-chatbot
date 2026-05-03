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

from app.services.chatbot.handlers import _immigration_acknowledgment
from app.services.chatbot.handlers.accessibility import (
    _detect_immigration_context,
    _handle_demographic_skip,
    _handle_location_unknown,
    _immigration_context_detail,
)
from app.services.chatbot.handlers.confirmation import (
    _handle_context_aware_confirm,
    _handle_pending_confirmation,
)
from conftest import make_ctx


@pytest.fixture
def llm_enabled(monkeypatch):
    """Enable the LLM gate for a test and inject a mockable
    ``slot_extraction.extract``.

    Phase 4 (April 2026): the gate now routes through
    ``app.services.slot_extraction.extract()`` rather than the
    legacy ``classify_unified``. The fixture monkey-patches the
    ``extract`` symbol on the slot_extraction package so the gate
    sees a controllable mock when it does its lazy import.

    The gate also checks ``_USE_LLM`` on the pipeline module to
    decide whether to fire — flip that to True so the test exercises
    the full gate-fires path even without an API key.
    """
    from app.services import slot_extraction as slot_extraction_module
    from app.services.chatbot import pipeline as pipeline_module
    mock_extract = MagicMock(return_value=None)
    monkeypatch.setattr(pipeline_module, "_USE_LLM", True)
    monkeypatch.setattr(slot_extraction_module, "extract", mock_extract,
                        raising=False)
    return mock_extract


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
        with patch("app.services.chatbot.tone.random_warmth_prefix",
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
        has_si, action, src, tone, llm_action, unified = _run_llm_gate(
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

    def test_skip_when_regex_tone_already_classified(self, llm_enabled):
        """When the regex tone classifier already found a tone (e.g.
        emotional, frustrated), the gate must NOT fire — the gap-filler
        only exists to backfill what regex/keyword classification
        missed. Ported from legacy
        `test_llm_classifier.test_regex_found_tone_skips_gate`.
        """
        _run_llm_gate(
            message="i'm so scared and i don't know what to do anymore",
            early_extracted={},
            has_service_intent=False,
            action_pre=None,
            regex_tone_pre="emotional",  # regex classifier resolved
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
        has_si, action, src, tone, llm_action, unified = _run_llm_gate(
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
        has_si, action, src, tone, llm_action, unified = _run_llm_gate(
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
        ctx = make_ctx(
            message="I need food in Brooklyn",
            has_service_intent=True,
        )
        result, prefix = _handle_spanish_detection(ctx)
        assert result is None
        assert prefix == ""

    def test_spanish_only_returns_result(self):
        """Spanish + no service intent → full bilingual message, no prefix."""
        ctx = make_ctx(
            message="hola, necesito ayuda",
            has_service_intent=False,
        )
        result, prefix = _handle_spanish_detection(ctx)
        assert result is not None
        assert "Lo siento" in result["response"]
        assert prefix == ""

    def test_spanish_with_service_returns_prefix(self):
        """Spanish + service intent → fall through with bilingual prefix."""
        ctx = make_ctx(
            message="necesito comida en Brooklyn",
            has_service_intent=True,
        )
        result, prefix = _handle_spanish_detection(ctx)
        assert result is None  # caller should continue processing
        assert "Spanish" in prefix
        assert "do my best" in prefix

    def test_exactly_one_output_is_ever_non_empty(self):
        """Invariant check: result and prefix are mutually exclusive."""
        for has_si in (True, False):
            for msg in ("hello world", "hola mundo"):
                ctx = make_ctx(message=msg, has_service_intent=has_si)
                result, prefix = _handle_spanish_detection(ctx)
                # Not both non-empty
                assert not (result is not None and prefix), (
                    f"Both outputs non-empty for msg={msg!r}, has_si={has_si}"
                )


# -----------------------------------------------------------------------
# _handle_demographic_skip — TEST-GAP-1
# -----------------------------------------------------------------------
#
# The handler fires when:
#   1. existing has service_type AND location AND NOT _pending_confirmation
#   2. existing is missing age OR family_status (still demographic-pending)
#   3. user message matches a "rather not say" phrase
# Returns a result dict with confirmation message; otherwise None.
#
# These tests pin the precondition-gating, the apostrophe normalization
# (mobile keyboards autocorrect to U+2019 — straight phrases would silently
# fail to match without normalization), and the "skipped" sentinel side
# effect on existing.

class TestHandleDemographicSkip:
    """Direct unit tests for the demographic-skip handler.

    See PHASE_AC_AFTERMATH.md TEST-GAP-1 — integration-test coverage was
    hard to reach because the natural conversation flow auto-sets
    ``_pending_confirmation`` (which gates this handler off). These
    direct tests bypass that.
    """

    def _existing(self, **overrides):
        """Build an `existing` dict in the demographic-pending state:
        service_type and location set, age and family_status unset,
        no pending confirmation."""
        base = {
            "service_type": "food",
            "location": "brooklyn",
            "age": None,
            "family_status": None,
            "_pending_confirmation": False,
        }
        base.update(overrides)
        return base

    def _save_session_called(self, monkeypatch):
        """Patch save_session_slots so tests don't touch real session
        storage; return a list that records each call."""
        calls = []
        monkeypatch.setattr(
            "app.services.chatbot.handlers.accessibility.save_session_slots",
            lambda sid, slots: calls.append((sid, dict(slots))),
        )
        return calls

    def test_skip_phrase_marks_demographics_skipped(self, monkeypatch):
        """The canonical happy path — phrase matches, demographics
        flip from None to 'skipped', confirmation message is returned
        with the warm "No problem at all." prefix."""
        self._save_session_called(monkeypatch)
        existing = self._existing()
        ctx = make_ctx(
            message="I'd rather not say",
            existing=existing,
            session_id="s1",
            request_id="r1",
        )
        result = _handle_demographic_skip(ctx)
        assert result is not None
        assert result["session_id"] == "s1"
        assert result["follow_up_needed"] is True
        assert result["response"].startswith("No problem at all.")
        # Side effects on existing:
        assert existing["age"] == "skipped"
        assert existing["family_status"] == "skipped"
        assert existing["_pending_confirmation"] is True

    def test_curly_apostrophe_still_matches(self, monkeypatch):
        """Mobile keyboards autocorrect "don't" to "don\u2019t" (U+2019).
        Without normalization, "don\u2019t want to say" matches NEITHER
        "don't want to say" (different apostrophe) NOR "dont want to say"
        (the apostrophe-less alternate is no longer a substring once the
        curly char is in the haystack). The "rather not say" phrase has
        a substring fallback that masks the missing-normalize bug, so
        we deliberately use "don't want to say" to ensure this test
        actually exercises the normalization path. This test would FAIL
        if `normalize_apostrophes` were removed from the handler."""
        self._save_session_called(monkeypatch)
        # Curly apostrophe in user input — picks the "don't want to say"
        # phrase variant where no substring fallback exists
        msg = "I don\u2019t want to say"
        assert "\u2019" in msg  # sanity: confirm we're testing curly
        existing = self._existing()
        ctx = make_ctx(message=msg, existing=existing)
        result = _handle_demographic_skip(ctx)
        assert result is not None, (
            "Curly apostrophe should still match the skip phrase — "
            "if this fails, normalize_apostrophes was removed or "
            "stopped firing in _handle_demographic_skip."
        )
        assert existing["age"] == "skipped"

    def test_only_age_unset_marks_only_age(self, monkeypatch):
        """Family status already set; only age should get 'skipped'."""
        self._save_session_called(monkeypatch)
        existing = self._existing(family_status="single")
        ctx = make_ctx(message="prefer not to say", existing=existing)
        result = _handle_demographic_skip(ctx)
        assert result is not None
        assert existing["age"] == "skipped"
        # family_status was already set — don't overwrite
        assert existing["family_status"] == "single"

    def test_returns_none_when_no_service_type(self, monkeypatch):
        """If the user hasn't told us what they need yet, the
        demographic-skip path must NOT fire — we'd be marking demos
        skipped on a session that hasn't begun the service search."""
        self._save_session_called(monkeypatch)
        existing = self._existing(service_type=None)
        ctx = make_ctx(message="I'd rather not say", existing=existing)
        result = _handle_demographic_skip(ctx)
        assert result is None

    def test_returns_none_when_no_location(self, monkeypatch):
        self._save_session_called(monkeypatch)
        existing = self._existing(location=None)
        ctx = make_ctx(message="rather not say", existing=existing)
        result = _handle_demographic_skip(ctx)
        assert result is None

    def test_returns_none_when_already_pending_confirmation(self, monkeypatch):
        """If we're already at the confirmation step, demographics are
        no longer being collected — the skip path is irrelevant."""
        self._save_session_called(monkeypatch)
        existing = self._existing(_pending_confirmation=True)
        ctx = make_ctx(message="skip", existing=existing)
        result = _handle_demographic_skip(ctx)
        assert result is None

    def test_returns_none_when_demographics_already_filled(self, monkeypatch):
        """Both age and family_status set — nothing left to skip."""
        self._save_session_called(monkeypatch)
        existing = self._existing(age=25, family_status="single")
        ctx = make_ctx(message="I'd rather not say", existing=existing)
        result = _handle_demographic_skip(ctx)
        assert result is None

    def test_non_skip_phrase_returns_none(self, monkeypatch):
        """A message that doesn't match the skip-phrase list must NOT
        fire — otherwise we'd swallow real answers as skips."""
        self._save_session_called(monkeypatch)
        existing = self._existing()
        ctx = make_ctx(message="I'm 25", existing=existing)
        result = _handle_demographic_skip(ctx)
        assert result is None

    def test_exact_skip_match(self, monkeypatch):
        """Bare 'skip' (exact match) is in the explicit-match branch
        of the predicate, separate from the substring-match branch."""
        self._save_session_called(monkeypatch)
        existing = self._existing()
        ctx = make_ctx(message="skip", existing=existing)
        result = _handle_demographic_skip(ctx)
        assert result is not None
        assert existing["age"] == "skipped"

    def test_exact_pass_match(self, monkeypatch):
        """Bare 'pass' likewise matches via the explicit branch."""
        self._save_session_called(monkeypatch)
        existing = self._existing()
        ctx = make_ctx(message="pass", existing=existing)
        result = _handle_demographic_skip(ctx)
        assert result is not None

    def test_save_session_called_twice(self, monkeypatch):
        """Implementation detail worth pinning: the handler saves
        session state TWICE — once after marking demographics
        skipped, once after setting `_pending_confirmation=True`. If a
        future refactor consolidates these, the intermediate state
        wouldn't be observable to other readers, which could matter
        for any code that snoops the session between the two saves."""
        calls = self._save_session_called(monkeypatch)
        existing = self._existing()
        ctx = make_ctx(message="rather not say", existing=existing)
        _handle_demographic_skip(ctx)
        assert len(calls) == 2


# -----------------------------------------------------------------------
# _handle_location_unknown — TEST-GAP-1 sibling coverage
# -----------------------------------------------------------------------
#
# The audit identified _handle_demographic_skip as the gap, but the
# sibling handler shares the same apostrophe-normalization risk profile
# (mobile users sending "I don't know" with curly apostrophes). Adding
# a curly-apostrophe test here too costs nothing and pins the same
# class of regression for both handlers.

class TestHandleLocationUnknown:
    """Direct unit tests for the location-unknown handler — covering
    the same apostrophe-normalization risk identified for the sibling
    demographic-skip handler."""

    def _existing(self, **overrides):
        """Service set but no location — the precondition for the
        location picker to surface."""
        base = {
            "service_type": "food",
            "location": None,
            "_pending_confirmation": False,
        }
        base.update(overrides)
        return base

    def test_idk_phrase_offers_location_picker(self):
        existing = self._existing()
        ctx = make_ctx(message="I don't know", existing=existing)
        result = _handle_location_unknown(ctx)
        assert result is not None
        assert "location" in result["response"].lower()

    def test_curly_apostrophe_still_matches(self):
        """Same apostrophe-normalization pin as
        TestHandleDemographicSkip. Would fail if normalize_apostrophes
        were removed."""
        msg = "I don\u2019t know"
        assert "\u2019" in msg
        existing = self._existing()
        ctx = make_ctx(message=msg, existing=existing)
        result = _handle_location_unknown(ctx)
        assert result is not None, (
            "Curly apostrophe should still match — if this fails, "
            "normalize_apostrophes was removed from "
            "_handle_location_unknown."
        )

    def test_returns_none_when_location_already_set(self):
        existing = self._existing(location="brooklyn")
        ctx = make_ctx(message="I don't know", existing=existing)
        result = _handle_location_unknown(ctx)
        assert result is None


# -----------------------------------------------------------------------
# Snapshot-arg semantics — TEST-GAP-2
# -----------------------------------------------------------------------
#
# Two dispatchers receive a captured snapshot of session-state as a
# positional arg, separate from the same key on `existing`:
#
#   _handle_context_aware_confirm(..., last_action, ...)
#       — orchestrator captures `last_action = existing.get("_last_action")`
#         BEFORE calling, then the handler pops `_last_action` from
#         `existing` as part of its dispatch logic. The handler must
#         route on the SNAPSHOT, not re-read from existing.
#
#   _handle_pending_confirmation(..., pending, ...)
#       — orchestrator captures `pending = existing.get("_pending_confirmation")`
#         BEFORE calling. The handler may pop `_pending_confirmation`
#         from `existing` mid-flight. Same contract: route on snapshot.
#
# The snapshot pattern matters because if a future refactor "fixed" the
# handler to re-read from `existing`, the dispatch logic would silently
# break — the handler would see the post-pop value (None), miss its
# branch, and return None. These tests pin the snapshot contract by
# constructing intentionally divergent values for the snapshot arg vs
# the dict, then verifying the handler routes on the snapshot.
#
# See PHASE_AC_AFTERMATH.md TEST-GAP-2 for full background.

class TestSnapshotArgSemantics:
    """Pin the snapshot-arg routing contract for the two confirmation
    dispatchers. Each test constructs divergent values (snapshot says
    one thing, ``existing[key]`` says another) and asserts the handler
    follows the snapshot.

    These tests exercise the dispatchers directly (no orchestrator,
    no MessageContext) so the divergence is observable. Integration
    tests that go through ``generate_reply`` can't construct this
    divergence — orchestrator captures the snapshot atomically with
    the dict read.
    """

    @pytest.fixture(autouse=True)
    def _stub_session_io(self, monkeypatch):
        """Don't touch real session storage in any of these tests."""
        monkeypatch.setattr(
            "app.services.chatbot.handlers.confirmation.save_session_slots",
            lambda sid, slots: None,
        )

    # ------------------------------------------------------------------
    # _handle_context_aware_confirm — last_action snapshot
    # ------------------------------------------------------------------

    def test_context_aware_uses_snapshot_when_existing_is_empty(self):
        """existing._last_action is None, but ctx.snapshot_last_action
        says 'emotional'. Handler must dispatch the emotional path
        (snapshot wins).

        If a future refactor changed the handler to re-read
        ``ctx.existing.get("_last_action")``, this would return None — the
        emotional branch would never fire."""
        existing = {"_last_action": None}
        ctx = make_ctx(
            message="yes",
            existing=existing,
            category="confirm_yes",
            snapshot_last_action="emotional",  # ← snapshot says emotional
        )
        result = _handle_context_aware_confirm(ctx)
        assert result is not None, (
            "Handler must use ctx.snapshot_last_action, not "
            "ctx.existing.get('_last_action'). If this fails, the "
            "snapshot contract is broken."
        )
        # The emotional branch returns the escalation response
        assert "search" in result["response"].lower() or \
               result.get("quick_replies"), \
               "Expected emotional escalation response"

    def test_context_aware_ignores_existing_when_snapshot_is_none(self):
        """existing._last_action='emotional', but snapshot is None.
        Handler must NOT dispatch (snapshot wins, no value to route on).

        If a refactor read from existing, this would wrongly dispatch."""
        existing = {"_last_action": "emotional"}
        ctx = make_ctx(
            message="yes",
            existing=existing,
            category="confirm_yes",
            snapshot_last_action=None,  # ← snapshot says nothing
        )
        result = _handle_context_aware_confirm(ctx)
        assert result is None, (
            "Handler must use the snapshot (None), not "
            "ctx.existing.get('_last_action'). If this fails, the "
            "handler is incorrectly re-reading from existing."
        )

    def test_context_aware_escalation_dispatch_via_snapshot(self):
        """Same divergence pattern, different branch: escalation."""
        existing = {"_last_action": None}
        ctx = make_ctx(
            message="yes",
            existing=existing,
            category="confirm_yes",
            snapshot_last_action="escalation",
        )
        result = _handle_context_aware_confirm(ctx)
        assert result is not None
        # Escalation response mentions sharing contact info
        assert "contact" in result["response"].lower() or \
               "shared" in result["response"].lower()

    # ------------------------------------------------------------------
    # _handle_pending_confirmation — pending snapshot
    # ------------------------------------------------------------------

    def test_pending_uses_snapshot_for_routing(self, monkeypatch):
        """existing._pending_confirmation is False, but snapshot says
        True. The handler should treat this as an active confirmation
        and route accordingly. If it re-read from existing, it would
        fall through to the queue-offer branch instead.

        We use category='confirm_deny' which has distinct behavior in
        the two branches — the queue-offer branch returns a response
        about declining; the pending branch handles 'no' to confirmation
        differently."""
        monkeypatch.setattr(
            "app.services.chatbot.handlers.confirmation._log_turn",
            lambda *a, **kw: None,
        )

        existing = {
            "_pending_confirmation": False,  # ← dict says NOT pending
            "service_type": "food",
            "location": "brooklyn",
        }
        ctx = make_ctx(
            message="no",
            existing=existing,
            category="confirm_deny",
            snapshot_pending=True,  # ← snapshot says PENDING
        )
        result = _handle_pending_confirmation(ctx)
        # When snapshot_pending=True and category=confirm_deny, handler
        # resets to a "what do you need" prompt — distinctly different
        # from the queue-offer-decline path that fires when not pending.
        assert result is not None, (
            "Handler must route on ctx.snapshot_pending, not "
            "ctx.existing.get('_pending_confirmation')."
        )

    def test_pending_falls_through_when_snapshot_false(self, monkeypatch):
        """existing._pending_confirmation=True (dict has stale value),
        but snapshot pending=False. The handler should NOT take the
        pending branch — it should evaluate the no-pending branch
        (queue-offer logic).

        This pins the inverse direction of the snapshot contract."""
        monkeypatch.setattr(
            "app.services.chatbot.handlers.confirmation._log_turn",
            lambda *a, **kw: None,
        )

        existing = {
            "_pending_confirmation": True,  # ← dict says pending
            # No queue-offer state set, so queue-offer branch returns None too
        }
        ctx = make_ctx(
            message="something else",
            existing=existing,
            category="service",
            snapshot_pending=False,  # ← snapshot says NOT pending
        )
        result = _handle_pending_confirmation(ctx)
        # No pending, no queue-offer state, no relevant category —
        # the snapshot=False branch finds nothing to do and returns
        # None. If the handler re-read from existing, it would wrongly
        # try the pending branch (which would dispatch on category).
        assert result is None, (
            "Handler must use the snapshot (False), not "
            "ctx.existing.get('_pending_confirmation')."
        )


# -----------------------------------------------------------------------
# _immigration_acknowledgment — A.1.b cultural-responsiveness prefix
# -----------------------------------------------------------------------
# Narrow detector by design: fires only on explicit asylum/immigration
# mentions surfaced by the extractor via service_detail, and only when
# the primary service isn't already legal (no double-up when the user
# is getting immigration help directly).

class TestDetectImmigrationContext:
    """Detection predicate — true when asylum or immigration services
    appears in the primary detail or anywhere in the additional_services
    queue.
    """

    def test_primary_asylum_detail(self):
        assert _detect_immigration_context({
            "service_type": "legal",
            "service_detail": "asylum services",
            "additional_services": [],
        })

    def test_primary_immigration_detail(self):
        assert _detect_immigration_context({
            "service_type": "legal",
            "service_detail": "immigration services",
            "additional_services": [],
        })

    def test_asylum_queued_behind_food(self):
        """The A.1 primary scenario — food primary, asylum queued."""
        assert _detect_immigration_context({
            "service_type": "food",
            "service_detail": None,
            "additional_services": [
                ("legal", "asylum services", None),
                ("other", "food stamps / SNAP", None),
            ],
        })

    def test_asylum_in_queued_services_key(self):
        """Runtime reality: orchestrator converts `additional_services`
        → `_queued_services` during merge_slots. The detector must
        check both keys so it works regardless of where in the
        pipeline it's called. If a future refactor drops this dual
        check, this test fires immediately."""
        assert _detect_immigration_context({
            "service_type": "food",
            "service_detail": None,
            # Note: no `additional_services` — only `_queued_services`,
            # which is the state at orchestrator.py:405 where the
            # helper actually fires.
            "_queued_services": [
                ("legal", "asylum services", None),
                ("other", "food stamps / SNAP", None),
            ],
        })

    def test_immigration_queued_behind_food(self):
        assert _detect_immigration_context({
            "service_type": "food",
            "service_detail": None,
            "additional_services": [("legal", "immigration services", None)],
        })

    def test_no_immigration_context(self):
        """Pure food search — no mention of asylum/immigration anywhere."""
        assert not _detect_immigration_context({
            "service_type": "food",
            "service_detail": None,
            "additional_services": [],
        })

    def test_unrelated_legal_detail_does_not_fire(self):
        """Non-immigration legal (e.g., eviction) must not trigger."""
        assert not _detect_immigration_context({
            "service_type": "legal",
            "service_detail": "eviction help",
            "additional_services": [],
        })

    def test_empty_slots_is_safe(self):
        """Robustness: totally empty slot dict returns False, no exception."""
        assert not _detect_immigration_context({})


class TestImmigrationContextDetail:
    """The word used in the acknowledgment phrase — 'asylum' vs
    'immigration'. Prefers the primary detail, falls back to the
    first matching queued service, defaults safely if unreachable.
    """

    def test_primary_asylum_returns_asylum(self):
        assert _immigration_context_detail({
            "service_detail": "asylum services",
            "additional_services": [],
        }) == "asylum"

    def test_primary_immigration_returns_immigration(self):
        assert _immigration_context_detail({
            "service_detail": "immigration services",
            "additional_services": [],
        }) == "immigration"

    def test_queued_asylum_returns_asylum(self):
        assert _immigration_context_detail({
            "service_detail": None,
            "additional_services": [("legal", "asylum services", None)],
        }) == "asylum"

    def test_queued_immigration_returns_immigration(self):
        assert _immigration_context_detail({
            "service_detail": None,
            "additional_services": [("legal", "immigration services", None)],
        }) == "immigration"

    def test_asylum_wins_over_immigration_when_both_present(self):
        """When both appear in the queue, the first match wins. Primary
        takes precedence over queue."""
        assert _immigration_context_detail({
            "service_detail": None,
            "additional_services": [
                ("legal", "asylum services", None),
                ("legal", "immigration services", None),  # unreachable in practice
            ],
        }) == "asylum"


class TestImmigrationAcknowledgment:
    """Integrated behavior — prefix string fires/suppresses based on
    trigger conditions.
    """

    def test_fires_when_asylum_queued_behind_food(self):
        """The A.1 primary scenario — confirmation turn should get
        acknowledgment prefix."""
        ack = _immigration_acknowledgment({
            "service_type": "food",
            "service_detail": None,
            "additional_services": [("legal", "asylum services", None)],
        })
        assert ack != ""
        assert "asylum" in ack
        assert "immigration legal services" in ack
        # Trailing newlines for concatenation readability
        assert ack.endswith("\n\n")

    def test_fires_when_immigration_queued_behind_food(self):
        ack = _immigration_acknowledgment({
            "service_type": "food",
            "service_detail": None,
            "additional_services": [("legal", "immigration services", None)],
        })
        assert ack != ""
        assert "immigration case" in ack

    def test_suppressed_when_primary_is_legal_asylum(self):
        """No double-up: user asking about asylum directly gets legal
        results, no meta-acknowledgment needed."""
        ack = _immigration_acknowledgment({
            "service_type": "legal",
            "service_detail": "asylum services",
            "additional_services": [],
        })
        assert ack == ""

    def test_suppressed_when_primary_is_legal_immigration(self):
        ack = _immigration_acknowledgment({
            "service_type": "legal",
            "service_detail": "immigration services",
            "additional_services": [],
        })
        assert ack == ""

    def test_suppressed_when_no_immigration_context(self):
        """Baseline — pure food search gets empty string, safe to
        unconditionally concatenate."""
        assert _immigration_acknowledgment({
            "service_type": "food",
            "service_detail": None,
            "additional_services": [],
        }) == ""

    def test_suppressed_when_legal_detail_is_not_immigration(self):
        """Eviction help is legal but not immigration — no fire."""
        assert _immigration_acknowledgment({
            "service_type": "food",
            "service_detail": None,
            "additional_services": [("legal", "eviction help", None)],
        }) == ""

    def test_safe_on_empty_slots(self):
        """Empty dict must not raise — orchestrator concatenates
        unconditionally."""
        assert _immigration_acknowledgment({}) == ""

    def test_phrase_matches_queue_offer_tonal_convention(self):
        """Phrasing mirrors _apply_queue_offer's 'You also mentioned'
        opener for tonal consistency across the confirmation + results
        flow. If either changes, both should change together."""
        ack = _immigration_acknowledgment({
            "service_type": "food",
            "additional_services": [("legal", "asylum services", None)],
        })
        assert ack.startswith("You also mentioned")


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
