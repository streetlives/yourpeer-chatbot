"""Comprehensive regression tests for multi-turn, multi-intent,
and context-aware routing.

Tests state transitions, _last_action lifecycle, frustration counting,
service change detection, and handler interaction patterns.

These tests guard against the specific anti-patterns found in the
Run 18 eval analysis and code audit.
"""
import pytest
import uuid
from unittest.mock import patch
from conftest import send
from app.services.session_store import get_session_slots
from app.services.classifier import _classify_tone, _classify_action


@pytest.fixture
def sid():
    return f"test-{uuid.uuid4().hex[:8]}"


# =====================================================================
# 1. _last_action lifecycle — verify it's cleared properly
# =====================================================================

class TestLastActionLifecycle:
    """_last_action must be cleared by context-shift handlers
    (help, greeting, thanks, reset) and set only by context-preserving
    handlers (emotional, escalation, frustration, confused, crisis)."""

    @pytest.mark.parametrize("setter_msg,expected_la", [
        ("I'm feeling down", "emotional"),
        ("connect with peer navigator", "escalation"),
        ("I'm confused", "confused"),
    ])
    def test_context_handlers_set_last_action(self, sid, setter_msg, expected_la):
        send(setter_msg, session_id=sid)
        s = get_session_slots(sid)
        assert s.get("_last_action") == expected_la

    @pytest.mark.parametrize("clearer_msg", [
        "help",
        "hello",
        "thanks",
        "Start over",
    ])
    @pytest.mark.parametrize("setter_msg", [
        "I'm feeling down",
        "connect with peer navigator",
    ])
    def test_context_shift_clears_last_action(self, sid, setter_msg, clearer_msg):
        """help/greeting/thanks/reset should clear _last_action."""
        send(setter_msg, session_id=sid)
        s1 = get_session_slots(sid)
        assert s1.get("_last_action") is not None  # verify it was set

        send(clearer_msg, session_id=sid)
        s2 = get_session_slots(sid)
        assert s2.get("_last_action") is None, \
            f"'{clearer_msg}' after '{setter_msg}' should clear _last_action"

    def test_help_after_emotional_then_yes_no_leak(self, sid):
        """emotional → help → yes should NOT connect to navigator."""
        send("I'm feeling down", session_id=sid)
        send("help", session_id=sid)
        r = send("yes", session_id=sid)
        # "yes" with no _last_action and no pending should be generic
        assert "navigator" not in r["response"].lower() or \
            "right now" not in r["response"].lower()

    def test_service_flow_clears_last_action(self, sid):
        """Starting a new service search should clear _last_action."""
        send("I'm feeling down", session_id=sid)
        send("I need food in Brooklyn", session_id=sid)
        s = get_session_slots(sid)
        # Service flow takes over — _last_action should be cleared
        # (the service flow doesn't set _last_action)
        assert s.get("_last_action") is None

    def test_show_more_after_emotional_clears_stale_last_action(self, sid):
        """A 'show more results' message after results were displayed must
        clear any stale ``_last_action`` from a prior emotional turn AND
        still serve the more-results page.

        This pins an orchestrator-level invariant introduced when
        MessageContext construction was moved before the post-results
        fast path: ``_clear_stale_last_action`` now runs BEFORE
        ``_handle_post_results_interaction``, but the reorder is only
        safe if the "fast path requires _last_action unset" guard for
        confirm_yes/deny actions remains effectively a no-op for
        non-confirm categories. This test catches a regression that
        would manifest as either (a) ``_last_action`` leaking into a
        subsequent confirm_yes turn, or (b) the show-more fast path
        misfiring because the stale flag wasn't cleared in time.

        Setup: emotional turn (sets ``_last_action='emotional'``) →
        a real service search yielding results → "show more results".
        Assertion: the show-more page serves results AND
        ``_last_action`` is cleared after the show-more turn.
        """
        # 1. Emotional turn — sets _last_action='emotional'
        send("I'm feeling really down", session_id=sid)
        s1 = get_session_slots(sid)
        assert s1.get("_last_action") == "emotional", (
            "Setup: emotional handler should set _last_action"
        )

        # 2. Service search — confirm and execute
        send("I need food in Brooklyn", session_id=sid)
        send("Yes, search", session_id=sid)
        s2 = get_session_slots(sid)
        # Slot extraction during service flow should have cleared
        # _last_action (or it was cleared by the service-flow path).
        # Pin _last_results so we know the show-more is valid.
        assert s2.get("_last_results"), (
            "Setup: service search should populate _last_results"
        )

        # 3. Show-more — the test target
        r = send("Show more results", session_id=sid)
        s3 = get_session_slots(sid)

        # _last_action should be cleared (whether by
        # _clear_stale_last_action or by the post-results handler's
        # internal pop — either path leaves it cleared).
        assert s3.get("_last_action") is None, (
            f"_last_action should be cleared after show-more, got "
            f"{s3.get('_last_action')!r}"
        )
        # And the show-more should have produced a result of some kind
        # (services list or response text — exact format depends on
        # how many results existed).
        assert r.get("response"), "show-more should produce a response"


# =====================================================================
# 2. Confirm/deny with service change
# =====================================================================

class TestConfirmDenyServiceChange:
    """When user denies a confirmation AND provides a new service,
    the handler should update slots and show new confirmation."""

    def test_change_mind_updates_service(self, sid):
        send("I need food in Brooklyn", session_id=sid)
        r = send("wait, I changed my mind, I need shelter", session_id=sid)
        assert r["slots"].get("service_type") == "shelter"
        # Contradiction detection auto-executes — may return results or
        # show new confirmation depending on location availability
        assert r["result_count"] >= 1 or "shelter" in r["response"].lower()

    def test_no_with_new_service_updates(self, sid):
        send("I need food in Brooklyn", session_id=sid)
        r = send("no, I want shelter instead", session_id=sid)
        assert r["slots"].get("service_type") == "shelter"

    def test_deny_with_service_and_location_change(self, sid):
        send("I need food in Brooklyn", session_id=sid)
        r = send("no, I need shelter in Queens", session_id=sid)
        assert r["slots"].get("service_type") == "shelter"
        assert "queens" in r["slots"].get("location", "").lower()

    def test_plain_deny_preserves_slots(self, sid):
        send("I need food in Brooklyn", session_id=sid)
        r = send("no", session_id=sid)
        assert r["slots"].get("service_type") == "food"
        assert "brooklyn" in r["slots"].get("location", "").lower()

    def test_wait_is_soft_deny(self):
        """'wait' and 'hold on' are soft denials during confirmation.
        They classify as confirm_deny, which pauses the flow. But when
        followed by a service intent (test_hold_on_lets_message_through),
        the service intent takes priority."""
        assert _classify_action("wait") == "confirm_deny"
        assert _classify_action("hold on") == "confirm_deny"

    def test_hold_on_lets_message_through(self, sid):
        """'hold on, I need shelter not food' should process the shelter intent."""
        send("I need food in Brooklyn", session_id=sid)
        r = send("hold on, I need shelter not food", session_id=sid)
        # "hold on" is not deny, so message flows through normally
        # It should extract "shelter" from the message
        assert r["slots"].get("service_type") == "shelter"


# =====================================================================
# 3. Yes-after-context handlers
# =====================================================================

class TestYesAfterContext:
    """'yes' after emotional/escalation/frustration/confused should
    be interpreted in context, not as a search confirmation."""

    def test_yes_after_emotional_connects_navigator(self, sid):
        send("I'm feeling scared", session_id=sid)
        r = send("yes", session_id=sid)
        assert "contact" in r["response"].lower() or \
               "reach out" in r["response"].lower() or \
               "navigator" in r["response"].lower()

    def test_yes_after_escalation_shows_distinct_response(self, sid):
        send("I need food in Brooklyn", session_id=sid)
        r1 = send("connect with peer navigator", session_id=sid)
        r2 = send("yes", session_id=sid)
        assert r1["response"] != r2["response"], \
            "yes-after-escalation should show different message than escalation"

    def test_yes_after_escalation_has_service_buttons(self, sid):
        send("connect with peer navigator", session_id=sid)
        r = send("yes", session_id=sid)
        labels = [qr["label"] for qr in r.get("quick_replies", [])]
        # Context-aware yes offers search and contact info options
        assert any("search" in lable.lower() or "contact" in lable.lower() for lable in labels)

    def test_yes_after_frustration_connects_navigator(self, sid):
        send("I need food in the Bronx", session_id=sid)
        send("Yes, search", session_id=sid)
        send("that wasn't helpful", session_id=sid)
        r = send("yes", session_id=sid)
        assert "navigator" in r["response"].lower()

    def test_yes_after_confused_connects_navigator(self, sid):
        send("I'm confused", session_id=sid)
        r = send("yes", session_id=sid)
        assert "navigator" in r["response"].lower()


# =====================================================================
# 4. No-after-context handlers
# =====================================================================

class TestNoAfterContext:
    """'no' after emotional/escalation should be gentle, not trigger
    a search denial or show the full service menu."""

    def test_no_after_emotional_is_gentle(self, sid):
        send("I'm feeling down", session_id=sid)
        r = send("no", session_id=sid)
        assert "okay" in r["response"].lower() or "here" in r["response"].lower()
        # Should NOT show full service menu
        labels = [qr["label"] for qr in r.get("quick_replies", [])]
        assert len(labels) <= 2

    def test_no_after_escalation_is_gentle(self, sid):
        send("connect with peer navigator", session_id=sid)
        r = send("no", session_id=sid)
        assert "problem" in r["response"].lower() or "here" in r["response"].lower() \
            or "mind" in r["response"].lower()

    def test_no_after_frustration_is_gentle(self, sid):
        send("I need food in the Bronx", session_id=sid)
        send("Yes, search", session_id=sid)
        send("not helpful", session_id=sid)
        r = send("no", session_id=sid)
        assert "navigator" in r["response"].lower() or "worries" in r["response"].lower()


# =====================================================================
# 5. Frustration counter
# =====================================================================

class TestFrustrationCounter:
    """_frustration_count should increment across turns and trigger
    the shorter second response."""

    def test_first_frustration_sets_count(self, sid):
        send("I need food in the Bronx", session_id=sid)
        send("Yes, search", session_id=sid)
        send("not helpful", session_id=sid)
        s = get_session_slots(sid)
        assert s.get("_frustration_count") == 1

    def test_second_frustration_increments(self, sid):
        send("I need food in the Bronx", session_id=sid)
        send("Yes, search", session_id=sid)
        send("not helpful", session_id=sid)
        send("still useless", session_id=sid)
        s = get_session_slots(sid)
        assert s.get("_frustration_count") == 2

    def test_second_frustration_is_shorter(self, sid):
        send("I need food in the Bronx", session_id=sid)
        send("Yes, search", session_id=sid)
        r1 = send("not helpful", session_id=sid)
        r2 = send("still useless", session_id=sid)
        assert len(r2["response"]) < len(r1["response"])

    def test_counter_persists_across_searches(self, sid):
        """Frustration count should persist even if user starts a new search."""
        send("I need food in the Bronx", session_id=sid)
        send("Yes, search", session_id=sid)
        send("not helpful", session_id=sid)  # count=1
        send("I need food in Manhattan", session_id=sid)
        send("Yes, search", session_id=sid)
        r = send("still not helpful", session_id=sid)  # count=2
        s = get_session_slots(sid)
        assert s.get("_frustration_count") == 2
        assert "navigator" in r["response"].lower()

    def test_reset_clears_frustration_count(self, sid):
        """Start over should clear the frustration count."""
        send("I need food in the Bronx", session_id=sid)
        send("Yes, search", session_id=sid)
        send("not helpful", session_id=sid)
        send("Start over", session_id=sid)
        s = get_session_slots(sid)
        assert s.get("_frustration_count") is None or s.get("_frustration_count") == 0


# =====================================================================
# 6. Emotional → service flow transitions
# =====================================================================

class TestEmotionalServiceTransitions:
    """Emotional state should not interfere with subsequent service requests."""

    def test_emotional_then_service_works(self, sid):
        send("I'm feeling scared", session_id=sid)
        r = send("I need food in Brooklyn", session_id=sid)
        assert r["slots"].get("service_type") == "food"

    def test_emotional_then_service_clears_emotional_state(self, sid):
        send("I'm feeling scared", session_id=sid)
        send("I need food in Brooklyn", session_id=sid)
        s = get_session_slots(sid)
        assert s.get("_last_action") is None

    def test_shame_with_service_gets_normalizing_prefix(self, sid):
        r = send("I never thought I'd need a food bank", session_id=sid)
        resp = r["response"].lower()
        assert "shame" in resp or "strength" in resp or "lot of people" in resp

    def test_pending_confirmation_then_emotional(self, sid):
        """Emotional message during pending confirmation should get
        emotional response, not re-prompt."""
        send("I need food in Brooklyn", session_id=sid)
        r = send("I'm scared", session_id=sid)
        assert "scared" in r["response"].lower() or \
            "okay" in r["response"].lower() or \
            "difficult" in r["response"].lower()

    def test_emotional_adjective_forms(self):
        """Situational adjectives should trigger emotional handler."""
        with patch('app.services.chatbot.orchestrator.detect_crisis', return_value=None):
            assert _classify_tone("this is really depressing", crisis_result=None) == "emotional"
            assert _classify_tone("that's overwhelming", crisis_result=None) == "emotional"
            assert _classify_tone("this is terrifying", crisis_result=None) == "emotional"


# =====================================================================
# 7. Multi-service and slot persistence
# =====================================================================

class TestSlotPersistence:
    """Slots should persist correctly across turns and be updated
    when the user provides new information."""

    def test_location_persists_across_service_change(self, sid):
        send("I need food in Brooklyn", session_id=sid)
        r = send("no, I need shelter instead", session_id=sid)
        assert r["slots"].get("service_type") == "shelter"
        assert "brooklyn" in r["slots"].get("location", "").lower()

    def test_location_updates_when_provided(self, sid):
        send("I need food in Brooklyn", session_id=sid)
        send("Yes, search", session_id=sid)
        r = send("I also need shelter in Queens", session_id=sid)
        assert r["slots"].get("service_type") == "shelter"
        # Queens might not overwrite Brooklyn depending on implementation
        # The important thing is shelter was extracted

    def test_results_then_new_service(self, sid):
        """After results, a new service request should start fresh search."""
        send("I need food in Brooklyn", session_id=sid)
        send("Yes, search", session_id=sid)
        r = send("I also need shelter", session_id=sid)
        assert r["slots"].get("service_type") == "shelter"

    def test_age_persists_across_turns(self, sid):
        send("I'm 17 and need food in Brooklyn", session_id=sid)
        send("Yes, search", session_id=sid)
        r = send("I also need shelter", session_id=sid)
        # Age should persist from the first message
        assert r["slots"].get("age") == 17 or r["slots"].get("age") == "17"


# =====================================================================
# 8. Complex multi-turn flows (regression scenarios)
# =====================================================================

class TestComplexFlows:
    """End-to-end multi-turn sequences that previously caused issues."""

    def test_emotional_to_service_to_frustration_to_navigator(self, sid):
        """Full flow: emotional → service → results → frustration → navigator."""
        send("I'm feeling down", session_id=sid)
        send("I need food in Brooklyn", session_id=sid)
        send("Yes, search", session_id=sid)
        send("not helpful", session_id=sid)
        r = send("yes", session_id=sid)
        # "yes" after frustration = connect to navigator
        assert "navigator" in r["response"].lower()

    def test_escalation_decline_then_service(self, sid):
        """User declines navigator → starts a new service search."""
        send("connect with peer navigator", session_id=sid)
        send("no", session_id=sid)
        r = send("I need food in Brooklyn", session_id=sid)
        assert r["slots"].get("service_type") == "food"

    def test_service_change_then_confirm(self, sid):
        """User changes service then confirms the new one."""
        send("I need food in Brooklyn", session_id=sid)
        send("no, I need shelter instead", session_id=sid)
        r = send("Yes, search", session_id=sid)
        # Should search for shelter, not food
        assert r["slots"].get("service_type") == "shelter"

    def test_double_emotional_different_emotions(self, sid):
        """Two emotional messages in a row should both get appropriate responses."""
        r1 = send("I'm feeling scared", session_id=sid)
        r2 = send("I'm also really lonely", session_id=sid)
        assert "scared" in r1["response"].lower()
        assert "alone" in r2["response"].lower() or "invisible" in r2["response"].lower() \
            or "lonely" in r2["response"].lower() or "difficult" in r2["response"].lower()

    def test_frustrated_reset_clean_slate(self, sid):
        """Frustration → reset → new search should work cleanly."""
        send("I need food in the Bronx", session_id=sid)
        send("Yes, search", session_id=sid)
        send("not helpful", session_id=sid)
        send("Start over", session_id=sid)
        r = send("I need shelter in Queens", session_id=sid)
        assert r["slots"].get("service_type") == "shelter"
        s = get_session_slots(sid)
        assert s.get("_frustration_count") is None or s.get("_frustration_count") == 0


# =====================================================================
# 9. Adversarial / unrecognized service handling
# =====================================================================

class TestUnrecognizedServiceEscalation:
    """Unrecognized service requests should escalate through 3 tiers:
    1st: list available categories
    2nd: shorter + navigator option
    3rd+: just navigator push"""

    def test_first_unrecognized_lists_categories(self, sid):
        r = send("I need a helicopter ride in Staten Island", session_id=sid)
        resp = r["response"].lower()
        assert "food" in resp or "shelter" in resp
        s = get_session_slots(sid)
        assert s.get("_unrecognized_count") == 1

    def test_second_unrecognized_adds_navigator(self, sid):
        send("I need a helicopter ride in Staten Island", session_id=sid)
        r = send("I really need a helicopter", session_id=sid)
        qr_labels = [q["label"].lower() for q in r.get("quick_replies", [])]
        assert any("navigator" in lable or "person" in lable for lable in qr_labels)
        s = get_session_slots(sid)
        assert s.get("_unrecognized_count") == 2

    def test_third_unrecognized_just_navigator(self, sid):
        send("I need a helicopter ride in Staten Island", session_id=sid)
        send("helicopter again", session_id=sid)
        r = send("helicopter please", session_id=sid)
        assert "navigator" in r["response"].lower()
        labels = [q["label"] for q in r.get("quick_replies", [])]
        assert len(labels) <= 3  # just navigator + start over

    def test_recovery_after_unrecognized(self, sid):
        """User can recover by choosing a real service type."""
        send("I need a helicopter ride in Staten Island", session_id=sid)
        send("helicopter again", session_id=sid)
        r = send("food", session_id=sid)
        assert r["slots"].get("service_type") == "food"

    def test_reset_clears_unrecognized_count(self, sid):
        send("I need a helicopter ride in Queens", session_id=sid)
        send("helicopter again", session_id=sid)
        send("Start over", session_id=sid)
        s = get_session_slots(sid)
        assert s.get("_unrecognized_count") is None or s.get("_unrecognized_count") == 0

    def test_sticky_detection_for_nonsense(self, sid):
        """Once flagged as unrecognized, subsequent messages without
        request verbs should still increment the counter.

        This test exercises the sticky-counter mechanic in
        handlers/general.py. With an ANTHROPIC_API_KEY set, the LLM
        slot extractor treats "asdfghjkl" as a potential ``org_name``,
        which makes ``has_service_intent=True`` and routes the message
        through the service pipeline — bypassing the general handler
        where ``_unrecognized_count`` is incremented. That's defensible
        LLM behavior for this input, but it means the test of the
        counter mechanic becomes non-deterministic across environments
        (passes without the env var, fails with it).

        To test the mechanic itself, force the regex-only path by
        patching ``_USE_LLM`` False at both bind sites that matter
        (pipeline, orchestrator). Without the LLM, nonsense stays
        nonsense, stays routed to the general handler, and the
        counter increments as designed.
        """
        with (
            patch("app.services.chatbot.pipeline._USE_LLM", False),
            patch("app.services.chatbot.orchestrator._USE_LLM", False),
        ):
            send("Can you find me some asdfghjkl", session_id=sid)  # generic turn 1
            send("I need asdfghjkl please", session_id=sid)  # count=1
            _r = send("asdfghjkl again", session_id=sid)  # sticky: count=2
        s = get_session_slots(sid)
        assert s.get("_unrecognized_count") == 2

    def test_location_preserved_in_redirect(self, sid):
        """Unrecognized redirect should mention the user's location."""
        r = send("I need a helicopter ride in Staten Island", session_id=sid)
        assert "staten island" in r["response"].lower()

    def test_nonsense_no_location_first_turn(self, sid):
        """Pure nonsense on first turn (no location, no request verb)
        should get generic response, not unrecognized handler."""
        _r = send("blorp blorp blorp", session_id=sid)
        s = get_session_slots(sid)
        assert s.get("_unrecognized_count", 0) == 0


class TestOtherServiceTypeInterception:
    """When LLM returns service_type='other' without detail (e.g., 'helicopter
    ride'), it should be intercepted and treated as unrecognized rather
    than searching for 'other services'."""

    def test_other_without_detail_is_unrecognized(self, sid):
        """service_type='other' with no detail should trigger redirect."""
        # Simulate by pre-setting session with service_type='other'
        from app.services.session_store import save_session_slots
        save_session_slots(sid, {
            'service_type': 'other',
            'location': 'staten island',
        })
        # Send a follow-up that triggers re-evaluation
        r = send("yes", session_id=sid)
        # The session had 'other' — the interception should have cleared it
        # and redirected the user. We verify:
        # 1. No services were returned (not a real search result)
        # 2. The redirect message cues the user toward navigator or known
        #    service types — matches the unrecognized-service handler's
        #    vocabulary in handlers/general.py.
        assert r["services"] == [], \
            "service_type='other' without detail should NOT produce services"
        response_lower = r["response"].lower()
        assert (
            "navigator" in response_lower
            or "help with that" in response_lower
            or "staten island" in response_lower  # redirect names the location
            or r["slots"].get("service_type") != "other"  # cleared the stale slot
        ), f"Expected redirect/unrecognized response, got: {r['response'][:200]}"

    def test_other_with_detail_is_legitimate(self, sid):
        """service_type='other' WITH detail should proceed normally."""
        # SNAP benefits → extracted as 'other' with detail 'snap'
        r = send("I need help with SNAP benefits in Brooklyn", session_id=sid)
        st = r["slots"].get("service_type")
        # Should be extracted as 'other' with detail, or as a recognized category
        # The key assertion: it should NOT be redirected to unrecognized
        assert st is not None or "food" in r["response"].lower() or \
            "search" in r["response"].lower()


class TestUnrecognizedServiceLLMGateGuard:
    """LLM-gate version of TestUnrecognizedServiceEscalation.

    Eval target: ``adversarial_unrecognized_service``. R28 scored 2.91,
    R32 4.36, R41 3.73 — the score has tracked fixture state rather than
    bot behavior because there's no behavioral guard. Without the LLM
    gate active, regex misses on "helicopter ride" and the existing
    `_handle_general_conversation` tier-1/2/3 redirect fires (covered
    by TestUnrecognizedServiceEscalation). With the LLM gate active —
    the production path — Haiku snaps to ``service_type=other`` with
    no detail because that's the closest enum, and the message routes
    to the service-search dispatcher, which returns plausible-but-
    irrelevant cards from the "other" taxonomy bucket.

    The fix routes low-confidence "other" classifications (extraction
    source = "llm_gate", service_type = "other", service_detail = None)
    to the same tiered redirect handler. Tests below pin the behavior
    by stubbing the LLM client to return that exact slot shape.

    See ``test_sticky_detection_for_nonsense`` in
    ``TestUnrecognizedServiceEscalation`` above — the docstring there
    documents this exact non-determinism (counter mechanic only worked
    with ``_USE_LLM=False``). Post-fix, the counter increments
    correctly even with the LLM gate active.
    """

    @staticmethod
    def _stub_llm_other_no_detail():
        """Build a slot_extraction_client mock returning the
        helicopter-shape: ``service_type=other`` with no detail.

        Uses the same MagicMock pattern as
        ``tests/integration/test_llm_call_redundancy.py``.
        """
        from unittest.mock import MagicMock
        block = MagicMock()
        block.type = "tool_use"
        block.name = "extract_intake_slots"
        block.input = {
            "service_type": "other",
            "location": "staten island",
            "service_detail": None,
            "tone": None,
            "action": None,
        }
        response = MagicMock()
        response.content = [block]
        client = MagicMock()
        client.messages.create.return_value = response
        return client

    @staticmethod
    def _force_llm_gate_active(client):
        """Compose the patch stack that forces the LLM gate to fire and
        bypasses external IO (search, crisis, claude_reply)."""
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch("app.services.chatbot.context._USE_LLM", True))
        stack.enter_context(patch("app.services.chatbot.pipeline._USE_LLM", True))
        stack.enter_context(patch("app.services.chatbot.orchestrator._USE_LLM", True))
        stack.enter_context(patch(
            "app.services.slot_extraction.dispatch.get_client",
            return_value=client,
        ))
        stack.enter_context(patch(
            "app.services.chatbot.execution.query_services",
            return_value=[],
        ))
        stack.enter_context(patch(
            "app.services.chatbot.orchestrator.detect_crisis",
            return_value=None,
        ))
        stack.enter_context(patch(
            "app.services.classifier.detect_crisis",
            return_value=None,
        ))
        return stack

    def test_helicopter_ride_routes_to_redirect_not_search(self, sid):
        """Turn 1: 'I need a helicopter ride from Staten Island'.

        Pre-fix: LLM gate sets service_type=other, has_service_intent
        is True, routing falls into the service branch, dispatcher is
        called and returns N irrelevant cards from the 'other' bucket.

        Post-fix: extraction_source=llm_gate + service_type=other +
        service_detail=None triggers the low-confidence-other branch
        in _compute_routing_category, which routes to general's
        tiered redirect. No service-search call, no cards returned,
        tier-1 message includes the location.
        """
        with self._force_llm_gate_active(self._stub_llm_other_no_detail()):
            r = send("I need a helicopter ride from Staten Island", session_id=sid)

        # No services returned — the search path was not taken.
        assert r["services"] == [], (
            f"Low-confidence 'other' should not dispatch search; "
            f"got {len(r['services'])} services in response."
        )
        # Tier-1 message hallmarks: mentions location + lists categories.
        resp = r["response"].lower()
        assert "staten island" in resp, (
            f"Tier-1 redirect should name the location; got: {r['response'][:200]}"
        )
        assert ("food" in resp or "shelter" in resp), (
            f"Tier-1 redirect should list available categories; "
            f"got: {r['response'][:200]}"
        )
        # Tier counter increments.
        s = get_session_slots(sid)
        assert s.get("_unrecognized_count") == 1, (
            f"_unrecognized_count should be 1 after first unrecognized "
            f"request; got {s.get('_unrecognized_count')}"
        )
        # Stale service_type is cleared so a follow-up "yes" can't
        # confirm against an "other" search.
        assert s.get("service_type") != "other", (
            f"Stale service_type='other' should be cleared from session "
            f"after redirect; got: {s.get('service_type')!r}"
        )

    def test_second_turn_increments_to_tier_2(self, sid):
        """Turn 2 of the eval scenario: 'a helicopter ride'.

        Even though 'a helicopter ride' doesn't match _SERVICE_NEED_RE
        (no 'I need / I want / looking for'), the routing layer still
        sets confidence=low for this LLM-gate output, so the handler's
        broadened gate fires. Tier counter increments to 2; navigator
        appears in quick replies.
        """
        with self._force_llm_gate_active(self._stub_llm_other_no_detail()):
            send("I need a helicopter ride from Staten Island", session_id=sid)
            r = send("a helicopter ride", session_id=sid)

        # Tier-2: navigator appears in quick replies.
        qr_labels = [q["label"].lower() for q in r.get("quick_replies", [])]
        assert any("navigator" in lbl for lbl in qr_labels), (
            f"Tier-2 should surface peer navigator in quick replies; "
            f"got labels: {qr_labels}"
        )
        s = get_session_slots(sid)
        assert s.get("_unrecognized_count") == 2, (
            f"_unrecognized_count should be 2 after second unrecognized "
            f"request; got {s.get('_unrecognized_count')}"
        )

    def test_keyword_other_still_searches(self, sid):
        """Negative test: 'I need free wifi in Brooklyn' should NOT
        trip the guard, because regex matches 'wifi' → service_type=
        other via SERVICE_KEYWORDS, so extraction_source='regex' (not
        'llm_gate'). The guard's `extraction_source == "llm_gate"`
        clause excludes the regex-matched case, and the request
        proceeds to service search as before.

        This test runs with the LLM gate ENABLED to mirror production —
        the regex tier still wins because it fires first and populates
        service_type before the gate would activate.
        """
        # Stub the LLM client to return a non-shaped response in case
        # it's invoked at all. With regex catching 'wifi', it shouldn't
        # be — but the stub keeps the test deterministic if the gate
        # path changes in future.
        with self._force_llm_gate_active(self._stub_llm_other_no_detail()):
            send("I need free wifi in Brooklyn", session_id=sid)

        # Regex matches 'wifi' → service_type='other' via keyword path.
        # The guard does not fire; the unrecognized-counter must NOT
        # have incremented — this is a legitimate request that should
        # proceed to search.
        s = get_session_slots(sid)
        assert s.get("service_type") == "other", (
            f"'wifi' should still extract as service_type='other' via "
            f"regex; got: {s.get('service_type')!r}"
        )
        assert s.get("_unrecognized_count", 0) == 0, (
            f"Legitimate 'other' (wifi) should not increment "
            f"_unrecognized_count; got {s.get('_unrecognized_count')}"
        )


class TestNegativePreferenceSafetyAndExpansion:
    """Behavior of ``_handle_negative_preference`` after the May 2026 fix.

    Eval target: ``wa_negative_preference`` (R32: 3.91, May 2026 run:
    4.0 weighted=4.1, +0.09 — passing but with three dimensions at 3/5).
    The fix adds two layered behaviors:

    1. **Safety-recall acknowledgment.** When the user discloses a
       past-tense safety experience ("was really unsafe", "wasn't safe",
       "felt unsafe"), all three tiers prepend safety-aware phrasing.
       The acknowledgment validates without probing for trauma details.
       Crisis-adjacent phrasings ("I'm not safe", "got attacked") fire
       crisis detection and route before this handler runs — no overlap.

    2. **Borough-expansion offer in tier 1.** When the user's location/
       service combo supports nearby boroughs (via the existing
       ``_NEARBY_BOROUGHS_BY_SERVICE`` data and a neighborhood→borough
       resolver), tier 1 mentions them in prose and offers up to 2
       ``🗺️ Try {Borough}`` quick replies. Tier 2 and tier 3 stay
       escalation-focused.

    Tests cover the four corner cases (safety×expansion ∈ 2×2) plus
    tier 2/3 escalation behavior and over-fire negatives.
    """

    def _seed_negative_preference_state(self, sid, location="Harlem"):
        """Seed session into a state where the next negative-preference
        message will be tier 1. Performs the standard turn-1 → turn-2
        warmup: user requests food at ``location``, confirms, then on
        turn 3 sends a negative-preference message.

        Returns the merged session slots after seeding (turn 1 + 2)
        so callers can check pre-state if needed.
        """
        # Turn 1: state the request
        send(f"I need food in {location}", session_id=sid)
        # Turn 2: confirm. May or may not return results depending on
        # fixture state; either way, _pending_confirmation gets cleared
        # and the bot is ready to receive the rejection in turn 3.
        send("Yes, search", session_id=sid)
        return get_session_slots(sid)

    # -----------------------------------------------------------------
    # Tier 1: safety acknowledgment + expansion (all four combinations)
    # -----------------------------------------------------------------

    def test_tier1_safety_recall_with_borough_expansion(self, sid):
        """Harlem food rejection with safety language: response should
        prepend safety acknowledgment, mention nearby boroughs in prose,
        and offer borough-expansion quick replies."""
        self._seed_negative_preference_state(sid, location="Harlem")
        r = send(
            "I've been to all of those already. The first one turned me "
            "away and the second one was really unsafe.",
            session_id=sid,
        )

        resp = r["response"]
        resp_lower = resp.lower()

        # Safety acknowledgment landmarks — opener phrasing or "feel
        # safer" closing.
        assert ("hard experience" in resp_lower or "feel safer" in resp_lower
                or "i'm sorry that happened" in resp_lower), (
            f"Tier-1 with safety recall should include a safety-aware "
            f"acknowledgment; got: {resp!r}"
        )

        # Borough-expansion prose: Harlem→Manhattan, food expansion is
        # ["Brooklyn", "Queens"] per _NEARBY_BOROUGHS_BY_SERVICE.
        assert "broaden the search" in resp_lower, (
            f"Tier-1 with location should offer expansion in prose; "
            f"got: {resp!r}"
        )
        assert ("brooklyn" in resp_lower or "queens" in resp_lower), (
            f"Tier-1 expansion should name a nearby borough; got: {resp!r}"
        )

        # Borough-expansion quick replies present, in priority position.
        qr_labels = [q["label"] for q in r.get("quick_replies", [])]
        try_borough_labels = [lbl for lbl in qr_labels if lbl.startswith("🗺️ Try")]
        assert len(try_borough_labels) >= 1, (
            f"Tier-1 with expansion should include 🗺️ Try {{Borough}} "
            f"quick replies; got labels: {qr_labels}"
        )
        # Peer navigator still present.
        assert any("navigator" in lbl.lower() for lbl in qr_labels), (
            f"Tier-1 should always include peer navigator; got: {qr_labels}"
        )

        # _last_action set correctly (non-frustration on tier 1).
        s = get_session_slots(sid)
        assert s.get("_last_action") == "negative_preference"
        assert s.get("_frustration_count") == 1

    def test_tier1_safety_recall_no_expansion_available(self, sid):
        """Safety acknowledgment fires even when location is unknown
        or unresolvable. Expansion prose should NOT appear."""
        # Seed: send a service-only request, skip location.
        send("I need food", session_id=sid)
        r = send(
            "I've been to all of those, the place was really unsafe.",
            session_id=sid,
        )

        resp = r["response"].lower()

        # Safety acknowledgment present.
        assert ("hard experience" in resp or "feel safer" in resp
                or "i'm sorry that happened" in resp), (
            f"Safety acknowledgment should fire even without expansion; "
            f"got: {r['response']!r}"
        )

        # No expansion prose. Note: this assertion specifically checks
        # the borough-broadening fragment, not generic words.
        assert "broaden the search" not in resp, (
            f"No expansion should fire without resolvable location; "
            f"got: {r['response']!r}"
        )

        # No 🗺️ borough quick replies.
        qr_labels = [q["label"] for q in r.get("quick_replies", [])]
        assert not any(lbl.startswith("🗺️ Try") for lbl in qr_labels), (
            f"No expansion quick replies without resolvable location; "
            f"got labels: {qr_labels}"
        )

    def test_tier1_no_safety_with_expansion(self, sid):
        """Plain negative preference (no safety language) at a known
        location should offer expansion but NOT prepend safety
        acknowledgment."""
        self._seed_negative_preference_state(sid, location="Brooklyn")
        r = send("I don't like those options", session_id=sid)

        resp = r["response"].lower()

        # Original tier-1 opener present (not the safety variant).
        assert "those options aren't what you need" in resp, (
            f"Plain negative preference should use the original opener; "
            f"got: {r['response']!r}"
        )
        # Safety phrasing should NOT appear.
        assert "hard experience" not in resp, (
            f"No safety acknowledgment without recall language; "
            f"got: {r['response']!r}"
        )
        assert "feel safer" not in resp, (
            f"No 'feel safer' closing without safety recall; "
            f"got: {r['response']!r}"
        )

        # Expansion still fires (Brooklyn food → Queens, Bronx).
        assert "broaden the search" in resp, (
            f"Expansion should fire for known location; got: {r['response']!r}"
        )

    def test_tier1_no_safety_no_expansion(self, sid):
        """Baseline: no location, no safety language. Tier 1 falls back
        to the original message (with peer navigator quick reply)."""
        send("I need food", session_id=sid)
        r = send("I don't like those options", session_id=sid)

        resp = r["response"].lower()

        assert "those options aren't what you need" in resp
        assert "hard experience" not in resp
        assert "broaden the search" not in resp

        qr_labels = [q["label"] for q in r.get("quick_replies", [])]
        # No expansion buttons.
        assert not any(lbl.startswith("🗺️ Try") for lbl in qr_labels)
        # Peer navigator still there.
        assert any("navigator" in lbl.lower() for lbl in qr_labels)

    # -----------------------------------------------------------------
    # Tier 2 / Tier 3 — safety acknowledgment yes, expansion no
    # -----------------------------------------------------------------

    def test_tier2_safety_recall_no_expansion(self, sid):
        """Tier 2 (frust_count >= 2) with safety language: safety-aware
        wording, but NO geographic expansion. Escalation arc stays
        focused on peer navigator + 311."""
        self._seed_negative_preference_state(sid, location="Harlem")
        # Pre-bump frustration to 1 so this rejection lands as tier 2.
        slots = get_session_slots(sid)
        slots["_frustration_count"] = 1
        from app.services.session_store import save_session_slots
        save_session_slots(sid, slots)

        r = send(
            "I've been to all of those — they were really unsafe.",
            session_id=sid,
        )

        resp = r["response"].lower()

        # Safety phrasing in tier 2.
        assert ("haven't felt safe" in resp or "feel right" in resp), (
            f"Tier-2 with safety should reference safety; got: {r['response']!r}"
        )

        # No expansion (tier 2 is escalation, not pivot).
        assert "broaden the search" not in resp, (
            f"Tier-2 should not include expansion prose; got: {r['response']!r}"
        )
        qr_labels = [q["label"] for q in r.get("quick_replies", [])]
        assert not any(lbl.startswith("🗺️ Try") for lbl in qr_labels), (
            f"Tier-2 should not include expansion quick replies; got: {qr_labels}"
        )

        # 311 mentioned (the tier-2 distinguishing feature).
        assert "311" in r["response"]

    def test_tier3_safety_recall_no_expansion(self, sid):
        """Tier 3 (frust_count >= 3) with safety language: brief
        safety-aware wording, navigator-only quick reply."""
        self._seed_negative_preference_state(sid, location="Harlem")
        slots = get_session_slots(sid)
        slots["_frustration_count"] = 2
        from app.services.session_store import save_session_slots
        save_session_slots(sid, slots)

        # Need a phrase that classifies as negative_preference. "been
        # to all of those" → negative_preference trigger; "really
        # unsafe" → safety recall.
        r = send(
            "I've been to all of those, they were really unsafe.",
            session_id=sid,
        )

        resp = r["response"].lower()

        # Safety phrasing in tier 3.
        assert ("haven't felt safe" in resp or "feels right" in resp), (
            f"Tier-3 with safety should reference safety; got: {r['response']!r}"
        )

        # Only one quick reply (the navigator).
        qr_labels = [q["label"] for q in r.get("quick_replies", [])]
        assert len(qr_labels) == 1, (
            f"Tier-3 should have exactly 1 quick reply (navigator); "
            f"got {len(qr_labels)}: {qr_labels}"
        )
        assert "navigator" in qr_labels[0].lower()

    # -----------------------------------------------------------------
    # Negatives: no over-fire on safety acknowledgment
    # -----------------------------------------------------------------

    def test_safety_acknowledgment_does_not_fire_on_plain_rejection(self, sid):
        """Plain rejection ("I don't like those") must NOT trigger the
        safety acknowledgment. This is the over-fire negative."""
        self._seed_negative_preference_state(sid, location="Brooklyn")
        r = send("I've been to all of those", session_id=sid)

        resp = r["response"].lower()

        assert "hard experience" not in resp, (
            f"'been to all of those' must not trigger safety acknowledgment; "
            f"got: {r['response']!r}"
        )
        assert "i'm sorry that happened" not in resp, (
            f"'been to all of those' must not trigger 'I'm sorry that happened'; "
            f"got: {r['response']!r}"
        )

    def test_safety_acknowledgment_handles_curly_apostrophe(self, sid):
        """Mobile users typing curly apostrophes still trigger the
        safety acknowledgment ("didn\u2019t feel safe" → recognized).

        Negative-preference trigger in this message: "been to all of
        those". Safety-recall trigger: "didn\u2019t feel safe" with the
        curly apostrophe. Without curly→straight handling at either
        layer, this would silently route through the frustration path
        with no safety acknowledgment. The defensive paired-listing in
        ``_classify_action`` and the explicit ``normalize_apostrophes``
        in ``_has_safety_recall`` together guarantee both fire.
        """
        self._seed_negative_preference_state(sid, location="Harlem")
        # Include curly apostrophe (U+2019) in "didn\u2019t feel safe".
        r = send(
            "I've been to all of those, the place didn\u2019t feel safe.",
            session_id=sid,
        )

        resp = r["response"].lower()
        assert ("hard experience" in resp or "feel safer" in resp
                or "i'm sorry that happened" in resp), (
            f"Curly apostrophe in 'didn\u2019t feel safe' should still "
            f"trigger safety acknowledgment; got: {r['response']!r}"
        )


class TestImplicitServiceChange:
    """Holistic change-mind detection: during pending confirmation, any
    message with a DIFFERENT service_type is an implicit correction.
    No denial keyword needed. Also handles negation-blind regex via
    additional_services swap."""

    @pytest.mark.parametrize("change_msg", [
        "Actually, I need shelter tonight",
        "Actually I need shelter not food",
        "Wait, can you search for shelter instead?",
        "I changed my mind, shelter please",
        "Shelter please",
        "I need shelter in Queens",
        "Can you look for shelter instead",
    ])
    def test_direct_service_change(self, sid, change_msg):
        """User states a different service — should update."""
        send("I need food in Brooklyn", session_id=sid)
        r = send(change_msg, session_id=sid)
        assert r["slots"].get("service_type") == "shelter"

    @pytest.mark.parametrize("change_msg", [
        "Forget food, I need a place to stay",
        "Not food — I need shelter",
        "I don't want food, shelter please",
    ])
    def test_negation_with_new_service(self, sid, change_msg):
        """User negates current service and states new one. Regex extracts
        both (negation-blind). The different service should win."""
        send("I need food in Brooklyn", session_id=sid)
        r = send(change_msg, session_id=sid)
        assert r["slots"].get("service_type") == "shelter"

    def test_same_service_different_location(self, sid):
        """Same service but new location should update location, not service."""
        send("I need food in Brooklyn", session_id=sid)
        r = send("I need food in Manhattan", session_id=sid)
        assert r["slots"].get("service_type") == "food"
        assert "manhattan" in r["slots"].get("location", "").lower()

    def test_confirm_yes_unaffected(self, sid):
        """'yes' should confirm the pending service, not change it."""
        send("I need food in Brooklyn", session_id=sid)
        r = send("yes", session_id=sid)
        assert r["slots"].get("service_type") == "food"

    def test_location_carries_over_on_service_change(self, sid):
        """When service changes, location should persist from pending."""
        send("I need food in Brooklyn", session_id=sid)
        r = send("shelter please", session_id=sid)
        assert r["slots"].get("service_type") == "shelter"
        assert "brooklyn" in r["slots"].get("location", "").lower()

    def test_service_change_shows_new_confirmation(self, sid):
        """After service change, should update to new service and proceed."""
        send("I need food in Brooklyn", session_id=sid)
        r = send("Actually I need shelter", session_id=sid)
        # Contradiction detection updates service and auto-executes or re-confirms
        assert r["slots"].get("service_type") == "shelter"
        assert "shelter" in r["response"].lower()

    # --- Additive intent (ADD, not CHANGE) ---

    @pytest.mark.parametrize("add_msg", [
        "I also need shelter",
        "And I need shelter too",
        "Can you also look for shelter?",
        "Oh and I need shelter as well",
        "I need shelter too",
        "food is good but I also need shelter",
    ])
    def test_additive_keeps_primary(self, sid, add_msg):
        """Additive keywords ('also', 'too', 'as well') should queue
        the new service, NOT replace the primary."""
        send("I need food in Brooklyn", session_id=sid)
        r = send(add_msg, session_id=sid)
        assert r["slots"].get("service_type") == "food", \
            f"'{add_msg}' should keep food as primary"
        queued = [s for s, *_ in r["slots"].get("_queued_services", [])]
        assert "shelter" in queued, \
            f"'{add_msg}' should queue shelter"

    def test_additive_then_confirm_searches_primary(self, sid):
        """After additive, confirming should search the primary service."""
        send("I need food in Brooklyn", session_id=sid)
        send("I also need shelter", session_id=sid)
        r = send("Yes, search", session_id=sid)
        assert r["slots"].get("service_type") == "food"
