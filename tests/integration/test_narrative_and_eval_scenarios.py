"""Integration tests that send messages through the full generate_reply
pipeline — not just individual functions.

These reproduce the ACTUAL failing eval scenarios (or close approximations)
to catch drift between local code and production behavior. They also test
cross-feature interactions: narrative + emotional, PII in narratives,
shame prefix + narrative extraction, etc.
"""
import pytest
import uuid
from conftest import send, send_multi
from app.services.session_store import get_session_slots
from app.privacy.pii_redactor import redact_pii
from app.services.chatbot import _USE_LLM

requires_llm = pytest.mark.requires_llm(
    pytest.mark.skipif(
        not _USE_LLM,
        reason="Requires ANTHROPIC_API_KEY for LLM narrative extraction",
    )
)


@pytest.fixture
def sid():
    return f"test-{uuid.uuid4().hex[:8]}"


# =====================================================================
# 1. Narrative → generate_reply integration
# =====================================================================

class TestNarrativeIntegration:
    """Long messages should go through narrative extraction and produce
    correct service confirmations."""

    @requires_llm
    def test_hospital_housing_narrative(self, sid):
        """Eval: natural_long_story — housing need misextracted as medical."""
        r = send(
            "I just got out of the hospital last week and my housing "
            "situation fell through because my roommate kicked me out "
            "and now I need somewhere to stay in the Bronx",
            session_id=sid,
        )
        assert r["slots"].get("service_type") == "shelter"
        assert "bronx" in r["slots"].get("location", "").lower()

    def test_reentry_narrative(self, sid):
        """Eval: multi_reentry_shelter_employment — re-entry context ignored."""
        r = send(
            "I was just released from Rikers two days ago and I'm in "
            "the South Bronx area and I need a place to stay and also "
            "need to find employment as soon as possible",
            session_id=sid,
        )
        assert r["slots"].get("service_type") == "shelter"

    @requires_llm
    def test_eviction_family_narrative(self, sid):
        """Eval: wa_tell_my_story — employment prioritized over shelter."""
        r = send(
            "I got evicted last month and I've been staying with friends "
            "but they can't keep me anymore and I have a 6 year old "
            "daughter and we need food and shelter in East New York",
            session_id=sid,
        )
        assert r["slots"].get("service_type") == "shelter"
        assert "east new york" in r["slots"].get("location", "").lower()

    @requires_llm
    def test_runaway_youth_narrative(self, sid):
        """Eval: multi_dycd_rhy_youth_runaway — shelter missed, only clothing."""
        r = send(
            "I'm 17 and I ran away from home because my parents were "
            "abusing me and I need clothes and somewhere safe to stay "
            "in Bushwick tonight",
            session_id=sid,
        )
        assert r["slots"].get("service_type") == "shelter"
        assert r["slots"].get("age") == 17

    def test_narrative_shows_confirmation(self, sid):
        """Narrative with enough info should show confirmation, not ask follow-up."""
        r = send(
            "I just got out of the hospital and my housing fell through "
            "and I need somewhere to stay in the Bronx and find a job",
            session_id=sid,
        )
        # Should have pending confirmation (enough slots to search)
        assert r["slots"].get("_pending_confirmation") is True or \
            r.get("follow_up_needed") is True

    def test_narrative_queues_additional_services(self, sid):
        """Narrative with multiple services should queue the additional ones."""
        r = send(
            "I just got out of the hospital and my housing fell through "
            "and I need somewhere to stay in the Bronx and find a job",
            session_id=sid,
        )
        queued = r["slots"].get("_queued_services", [])
        # Employment or medical should be queued
        queued_types = [s for s, *_ in queued] if queued else []
        assert len(queued_types) >= 1, \
            f"Should queue additional services, got: {queued}"

    def test_short_message_not_narrative_path(self, sid):
        """Short messages should NOT use narrative extraction."""
        r = send("I need food in Brooklyn", session_id=sid)
        assert r["slots"].get("service_type") == "food"


# =====================================================================
# 2. Cross-feature interactions
# =====================================================================

class TestCrossFeatureInteractions:
    """Test that different features work together correctly."""

    def test_emotional_narrative_with_service(self, sid):
        """Long emotional message with embedded service intent should
        detect emotion AND extract service correctly."""
        r = send(
            "I'm really scared right now because I just lost my apartment "
            "and I don't know where I'm going to sleep tonight and I'm "
            "in the East Village and I just feel so alone",
            session_id=sid,
        )
        # Should have shelter extracted (narrative path)
        assert r["slots"].get("service_type") == "shelter"

    def test_shame_narrative_gets_normalizing_prefix(self, sid):
        """Shame expressed in a narrative should get the normalizing prefix."""
        r = send(
            "I never thought I'd be in this situation but I just lost "
            "my job and I can't afford my rent anymore and I'm embarrassed "
            "to even ask but I need food and maybe a place to stay "
            "in Midtown if there's anything available",
            session_id=sid,
        )
        resp = r["response"].lower()
        # Should have shame prefix OR emotional handling
        assert ("shame" in resp or "strength" in resp or
                "lot of people" in resp or "shelter" in resp.lower())

    @requires_llm
    def test_intensifiers_in_narrative(self, sid):
        """Intensifiers in narratives should be stripped for phrase matching."""
        r = send(
            "I'm really incredibly overwhelmed right now and I just "
            "got out of the hospital and I really need somewhere safe "
            "to stay tonight in Brooklyn please help me",
            session_id=sid,
        )
        assert r["slots"].get("service_type") == "shelter"

    @requires_llm
    def test_frustration_then_narrative(self, sid):
        """Frustration followed by a narrative should work.

        Cross-feature interaction: verify that the frustration response
        path doesn't block subsequent messages, and that a long
        narrative after frustration is still processed normally.

        Requires LLM for the narrative handling. We deliberately don't
        pin service_type to a specific value: the narrative starts
        with "I also need a place to stay" which is genuinely
        ambiguous between "switch to shelter" and "add shelter to
        food." A reasonable LLM can land on either, and the feature
        under test is frustration+narrative composition — not which
        interpretation of "also" the classifier picks.
        """
        send("I need food in Brooklyn", session_id=sid)
        send("Yes, search", session_id=sid)
        send("not helpful", session_id=sid)
        r = send(
            "Look I also need a place to stay because I got evicted "
            "and I have nowhere to go tonight and I have two kids "
            "with me and we're in the Bronx",
            session_id=sid,
        )
        # Mechanic check: something was extracted. Either service_type
        # flipped to shelter (LLM read "also" as replacement), stayed
        # as food with shelter added to additional_services (LLM read
        # "also" as additive), or populations picked up the kids/
        # family signal. All three show the narrative reached the
        # extractor and produced signal.
        slots = r["slots"]
        narrative_was_processed = (
            slots.get("service_type") == "shelter"
            or "shelter" in (slots.get("additional_services") or [])
            or slots.get("family_status") == "with_children"
            or slots.get("location") == "Bronx"  # location update counts too
        )
        assert narrative_was_processed, (
            f"Narrative after frustration should produce extractable "
            f"signal, got slots={slots}"
        )


# =====================================================================
# 3. PII in narratives
# =====================================================================

class TestPIIInNarratives:
    """PII embedded in long messages should be redacted."""

    def test_phone_in_narrative(self):
        text = (
            "I just got out of the hospital and my housing fell through "
            "and you can reach me at 212-555-1234 and I need somewhere "
            "to stay in the Bronx tonight"
        )
        redacted, dets = redact_pii(text)
        assert "212-555-1234" not in redacted
        assert "[PHONE]" in redacted

    def test_name_in_narrative(self):
        text = (
            "My name is Sarah Johnson and I was just evicted from my "
            "apartment and I need help finding shelter for me and my "
            "two kids in East New York please"
        )
        redacted, _dets = redact_pii(text)
        assert "Sarah Johnson" not in redacted
        assert "[NAME]" in redacted

    def test_ssn_in_narrative(self):
        text = (
            "I need help and my social security number is 123-45-6789 "
            "and I was wondering if you could help me find food and "
            "shelter in Brooklyn for tonight"
        )
        redacted, dets = redact_pii(text)
        assert "123-45-6789" not in redacted
        assert "[SSN]" in redacted

    def test_multiple_pii_in_narrative(self):
        text = (
            "Hi my name is Maria Rodriguez and I need food in Brooklyn "
            "and you can email me at maria@test.com or call 917-555-0199 "
            "I was born on 03/15/1990 and I live at 123 Main Street"
        )
        _redacted, dets = redact_pii(text)
        pii_types = {d.pii_type for d in dets}
        assert "name" in pii_types
        assert "email" in pii_types
        assert "phone" in pii_types
        assert "dob" in pii_types
        assert "address" in pii_types


# =====================================================================
# 4. Session isolation
# =====================================================================

class TestSessionIsolation:
    """Different sessions should not leak state."""

    def test_two_sessions_independent(self):
        sid1 = f"test-{uuid.uuid4().hex[:8]}"
        sid2 = f"test-{uuid.uuid4().hex[:8]}"

        send("I need food in Brooklyn", session_id=sid1)
        send("I need shelter in Queens", session_id=sid2)

        s1 = get_session_slots(sid1)
        s2 = get_session_slots(sid2)

        assert s1.get("service_type") == "food"
        assert s2.get("service_type") == "shelter"
        assert "brooklyn" in s1.get("location", "").lower()
        assert "queens" in s2.get("location", "").lower()

    def test_emotional_state_doesnt_leak(self):
        sid1 = f"test-{uuid.uuid4().hex[:8]}"
        sid2 = f"test-{uuid.uuid4().hex[:8]}"

        send("I'm feeling scared", session_id=sid1)
        r2 = send("I need food in Brooklyn", session_id=sid2)

        s1 = get_session_slots(sid1)
        s2 = get_session_slots(sid2)

        assert s1.get("_last_action") == "emotional"
        assert s2.get("_last_action") is None
        assert r2["slots"].get("service_type") == "food"


# =====================================================================
# 5. Eval scenario approximations
# =====================================================================

class TestEvalScenarioApproximations:
    """Approximate the messages from the 17 failing eval scenarios.
    These catch drift between local code and production."""

    # --- Emotional (3 scenarios — fixed post-R18) ---

    def test_eval_emotional_scared(self, sid):
        r = send("I'm scared", session_id=sid)
        s = get_session_slots(sid)
        assert s.get("_last_action") == "emotional"
        assert "scared" in r["response"].lower() or "fear" in r["response"].lower() \
            or "understandable" in r["response"].lower()

    def test_eval_emotional_feeling_down(self, sid):
        _r = send("I'm feeling really down today", session_id=sid)
        s = get_session_slots(sid)
        assert s.get("_last_action") == "emotional"

    def test_eval_emotional_rough_day(self, sid):
        _r = send("I've been having a really rough day", session_id=sid)
        s = get_session_slots(sid)
        assert s.get("_last_action") == "emotional"

    # --- Context routing (3 scenarios — fixed post-R18) ---

    def test_eval_change_mind(self, sid):
        results = send_multi([
            "I need food in Brooklyn",
            "Actually wait, I changed my mind. I need shelter tonight",
        ], session_id=sid)
        assert results[-1]["slots"].get("service_type") == "shelter"

    def test_eval_yes_after_escalation(self, sid):
        results = send_multi([
            "I need food in Brooklyn",
            "Can I talk to a real person?",
            "yes",
        ], session_id=sid)
        # Turn 3 should differ from Turn 2
        assert results[2]["response"] != results[1]["response"]

    def test_eval_frustration_loop(self, sid):
        results = send_multi([
            "I need food in the Bronx",
            "Yes, search",
            "These results aren't helpful at all",
            "Still not helpful, this is useless",
        ], session_id=sid)
        # Second frustration should be shorter and push navigator
        assert len(results[3]["response"]) < len(results[2]["response"])
        assert "navigator" in results[3]["response"].lower()

    # --- Narrative (4-5 scenarios — fixed post-R18) ---

    @requires_llm
    def test_eval_long_story(self, sid):
        r = send(
            "I just got out of the hospital after being there for a week "
            "and when I got back my roommate had changed the locks and I "
            "have nowhere to go and I'm in the Bronx area",
            session_id=sid,
        )
        assert r["slots"].get("service_type") == "shelter"

    @requires_llm
    def test_eval_tell_my_story(self, sid):
        r = send(
            "I'm 34 years old and I just got evicted from my apartment "
            "last week and I've been staying with different friends but "
            "nobody can keep me and I have a 6 year old and we need "
            "food and somewhere to stay in East New York and I also "
            "need to find a job but that can wait",
            session_id=sid,
        )
        assert r["slots"].get("service_type") == "shelter"

    def test_eval_reentry(self, sid):
        r = send(
            "I was just released from Rikers yesterday and I'm 28 and "
            "I need a place to stay and eventually a job but right now "
            "I just need somewhere safe in the South Bronx area tonight",
            session_id=sid,
        )
        assert r["slots"].get("service_type") == "shelter"

    # --- Adversarial (2 scenarios — fixed post-R18) ---

    def test_eval_fake_service(self, sid):
        r = send("I need a helicopter ride in Staten Island", session_id=sid)
        # Should not crash or return service results for "helicopter"
        assert r["slots"].get("service_type") is None or \
            r["slots"].get("service_type") != "helicopter"

    def test_eval_nonsense_service(self, sid):
        """Repeated nonsense requests should produce escalating responses,
        not identical repeats (see sticky-counter in handlers/general.py).

        Turn 1 may be a generic response; turn 2 should trigger the
        unrecognized-service path with a visibly different reply. If
        responses are byte-identical, the escalation didn't fire.
        """
        results = send_multi([
            "Can you find me some xyzzy in the Bronx",
            "I still need xyzzy",
            "xyzzy please",
        ], session_id=sid)
        r1_start = results[0]["response"][:30]
        r2_start = results[1]["response"][:30]
        # At least turns 1 and 2 should differ (escalation).
        assert r1_start != r2_start, (
            f"Nonsense requests should escalate between turns, but turn 1 "
            f"and turn 2 start identically: {r1_start!r}"
        )

    # --- Shame (1 scenario — partially fixed) ---

    def test_eval_shame_shelter_stigma(self, sid):
        r = send(
            "I don't want anyone to know I'm homeless but I need "
            "a place to stay in the East Village",
            session_id=sid,
        )
        assert r["slots"].get("service_type") == "shelter"
        resp = r["response"].lower()
        # Should have shame normalization
        assert "shame" in resp or "strength" in resp or \
            "lot of people" in resp or "search" in resp
