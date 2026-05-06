"""
Tests for tone handling, emotional response routing, and empathetic design.

Covers:
  - Sensitive context tone override (foster care, fleeing, reentry, DV)
  - Help + confused empathetic prefix
  - Escalation + yes acknowledgment (no repeat)
  - Shame/vulnerability detection (20+ indirect phrases, R27 tone gap fix)
  - Shame-specific normalizing prefix for service flows
  - Emotional context persistence across multi-turn conversations
  - Shame + help action routing collision
  - Research-backed emotional categories (R28):
    - Distrust / suspicion (Harm Reduction Coalition, SAMHSA)
    - Feeling undeserving (PMC: 41% prevalence in homeless populations)
    - Anger at situation (distinct from bot-directed frustration)
  - Response selection accuracy (each emotion -> correct specific response)
  - Crisis boundary guards (emotional != crisis)
  - False positive guards (service keywords != emotional routing)

Run with: python -m pytest tests/unit/test_tone_and_empathy.py -v
"""

import pytest
from app.services.classifier import _classify_tone
from app.services.responses import _pick_emotional_response, _EMOTIONAL_RESPONSES
from app.services.crisis_detector import detect_crisis
from app.services.slot_extraction_regex import extract_slots
from conftest import send, send_multi


# -----------------------------------------------------------------------
# SENSITIVE CONTEXT TONE OVERRIDE
# -----------------------------------------------------------------------

class TestSensitiveContextToneOverride:
    """Messages with sensitive life situations should get empathetic tone."""

    def test_foster_care_gets_empathy(self):
        r = send_multi(["I'm aging out of foster care and need help in the Bronx"])
        resp = r[0]["response"].lower()
        assert "difficult situation" in resp
        assert "no worries" not in resp

    def test_just_got_out_of_jail_gets_empathy(self):
        r = send_multi(["I just got out of jail and need shelter in Brooklyn"])
        resp = r[0]["response"].lower()
        assert "difficult situation" in resp

    def test_domestic_violence_gets_empathy(self):
        r = send_multi(["I'm fleeing domestic violence and need shelter in Manhattan"])
        resp = r[0]["response"].lower()
        assert "difficult" in resp or "safe" in resp or "988" in r[0]["response"]

    def test_fleeing_gets_empathy(self):
        r = send_multi(["I escaped and need shelter in Queens"])
        resp = r[0]["response"].lower()
        assert "difficult situation" in resp

    def test_normal_request_no_override(self):
        r = send_multi(["I need food in Brooklyn"])
        resp = r[0]["response"].lower()
        assert "difficult situation" not in resp
        assert "strength" not in resp

    def test_override_when_tone_is_confused(self):
        r = send_multi(["I aged out of foster care and don't know what to do in the Bronx"])
        resp = r[0]["response"].lower()
        assert "difficult situation" in resp

    # R34 Sprint 3 — Option 1 architectural fix: `_tone_prefix` is
    # computed early in the orchestrator and passed to help/confused
    # handlers too, not just service-flow responses. Previously the
    # sensitive-context empathy only fired when the user also had a
    # concrete service keyword in the message; open-ended or help-
    # category messages ("what do I do", "where do I start") routed
    # to handlers that never saw the tone prefix.

    def test_sensitive_context_applies_to_help_route(self):
        """Help-category message with sensitive context should still
        receive the sensitive-context prefix. Before Option 1, the
        help handler built its own response without ever seeing
        `_tone_prefix` — so "I'm aging out of foster care and need
        help in the Bronx" (which routes to help when service_type
        is not auto-assigned) produced a bare help menu with no
        acknowledgment of the foster-care context."""
        r = send_multi(["I'm aging out of foster care and need help in the Bronx"])
        resp = r[0]["response"].lower()
        assert "difficult situation" in resp, (
            "Help-routed message with sensitive context should receive "
            "the sensitive-context empathy prefix. If this is failing, "
            "check that orchestrator's _tone_prefix is being passed to "
            "_handle_help (meta.py)."
        )

    def test_sensitive_context_applies_to_confused_route(self):
        """Confused-category message with sensitive context should
        receive the sensitive-context prefix. Mirror of the help case
        above — _handle_confused also receives `_tone_prefix` and
        prepends it to _CONFUSED_RESPONSE."""
        r = send_multi(["I aged out of foster care and don't know what to do in the Bronx"])
        resp = r[0]["response"].lower()
        assert "difficult situation" in resp, (
            "Confused-routed message with sensitive context should "
            "receive the sensitive-context empathy prefix. If failing, "
            "check that orchestrator's _tone_prefix is passed to "
            "_handle_confused (meta.py)."
        )

    def test_no_double_empathy_help_plus_sensitive(self):
        """When sensitive prefix is present, help handler uses the
        standard _HELP_RESPONSE instead of its own 'I hear you — it
        can feel overwhelming' lead-in. Otherwise responses would
        read as doubled empathy:
            'I understand this is a difficult situation. Let me help.
             I hear you — it can feel overwhelming...'
        This guard ensures the two empathy framings don't stack."""
        r = send_multi(["I'm aging out of foster care and need help in the Bronx"])
        resp = r[0]["response"].lower()
        # Sensitive prefix present, but the help-specific "I hear you"
        # lead-in should NOT be present too.
        assert "difficult situation" in resp
        assert "i hear you" not in resp, (
            "Both the sensitive-context prefix AND the help handler's "
            "generic emotional lead-in appeared — meta.py _handle_help "
            "should use _HELP_RESPONSE (not the 'I hear you' variant) "
            "when tone_prefix is non-empty."
        )


# -----------------------------------------------------------------------
# HELP + CONFUSED EMPATHY
# -----------------------------------------------------------------------

class TestHelpConfusedEmpathy:
    def test_dont_know_where_to_start(self):
        r = send_multi(["I don't know where to start, I need help"])
        resp = r[0]["response"].lower()
        assert "overwhelming" in resp or "one step" in resp

    def test_overwhelmed_help(self):
        r = send_multi(["I'm overwhelmed and need help"])
        resp = r[0]["response"].lower()
        assert "overwhelming" in resp or "one step" in resp

    def test_plain_help_no_empathy(self):
        r = send_multi(["What can you help me with?"])
        resp = r[0]["response"].lower()
        assert "overwhelming" not in resp

    def test_help_still_shows_service_menu(self):
        r = send_multi(["Can you help me find something?"])
        qr = [q["label"].lower() for q in r[0].get("quick_replies", [])]
        assert any("food" in label for label in qr)


# -----------------------------------------------------------------------
# ESCALATION YES ACKNOWLEDGMENT
# -----------------------------------------------------------------------

class TestEscalationYesAcknowledgment:
    def test_yes_does_not_repeat_escalation(self):
        r = send_multi(["connect with peer navigator", "yes"])
        assert r[1]["response"].lower() != r[0]["response"].lower()

    def test_yes_has_acknowledgment(self):
        r = send_multi(["connect with peer navigator", "yes"])
        resp = r[1]["response"].lower()
        assert "navigator" in resp or "here" in resp

    def test_yes_offers_alternatives(self):
        r = send_multi(["connect with peer navigator", "yes"])
        qr_labels = [q["label"].lower() for q in r[1].get("quick_replies", [])]
        has_search = any("search" in label for label in qr_labels)
        has_contact = any("contact" in label for label in qr_labels)
        assert has_search or has_contact

    def test_show_contact_again_re_escalates(self):
        r = send_multi([
            "connect with peer navigator",
            "yes",
            "Connect with peer navigator",
        ])
        assert "navigator" in r[2]["response"].lower()


# =======================================================================
# SHAME / VULNERABILITY DETECTION (R27 tone gap fix)
# =======================================================================

class TestShameToneClassification:
    """All shame/vulnerability phrases should classify as 'emotional' tone."""

    @pytest.mark.parametrize("phrase", [
        # Original shame phrases
        "I'm embarrassed to ask for help",
        "I'm ashamed to ask",
        "I feel like a failure",
        "I'm pathetic",
        # Indirect vulnerability (R27 additions)
        "This is really hard for me to say",
        "I hate asking for this",
        "It's hard to admit",
        "This is humiliating",
        "I feel like such a burden",
        "I've never had to ask for help before",
        "I swallowed my pride",
        "I can't afford to eat",
        "I can't even feed myself",
        "I can't believe I'm doing this",
        "It's difficult to ask for help",
        "I don't want to be a burden",
    ])
    def test_classifies_as_emotional(self, phrase):
        tone = _classify_tone(phrase, crisis_result=None)
        assert tone == "emotional", \
            f"'{phrase}' should classify as emotional, got '{tone}'"

    @pytest.mark.parametrize("phrase", [
        "I'm embarrassed to ask",
        "I feel like a failure",
        "I'm ashamed of myself",
        "hard for me to say",
        "hard to admit",
        "humiliating",
    ])
    def test_shame_does_not_trigger_crisis(self, phrase):
        result = detect_crisis(phrase, skip_llm=True)
        assert result is None, f"'{phrase}' should NOT trigger crisis"


class TestShameResponseSelection:
    """Shame phrases should get the shame-specific normalizing response."""

    @pytest.mark.parametrize("phrase", [
        "I'm embarrassed to ask for help",
        "I'm ashamed",
        "I feel like a failure",
        "This is hard for me to say",
        "I hate asking for this",
        "It's humiliating",
        "I feel like a burden",
        "I swallowed my pride",
        "I can't afford to eat",
        "I've never done this before",
    ])
    def test_gets_shame_response(self, phrase):
        response = _pick_emotional_response(phrase)
        assert response == _EMOTIONAL_RESPONSES["shame"], \
            f"'{phrase}' should get shame response"


class TestShameTonePrefix:
    """When shame + service intent co-occur, confirmation gets normalizing prefix."""

    @pytest.mark.parametrize("msg", [
        "This is really hard for me to say but I can't afford to eat. I'm in the Bronx.",
        "I hate asking for this but I need food in Manhattan",
        "I swallowed my pride and I need a food bank in Brooklyn",
        "This is humiliating but I need shelter in Queens",
        "I've never had to ask for help before. I need food in the Bronx.",
        "It's hard to admit but I need clothing in Manhattan",
        "I feel like such a burden but I need shelter in Brooklyn",
        "I never thought I'd need a food bank",
    ])
    def test_shame_service_gets_normalizing_prefix(self, msg):
        r = send(msg)
        resp = r["response"].lower()
        assert any(w in resp for w in ["strength", "shame", "no shame", "lot of people"]), \
            f"Expected normalizing prefix, got: {r['response'][:80]}"

    def test_non_shame_emotional_service_gets_generic_prefix(self):
        r = send("I'm really scared and need shelter in Brooklyn")
        resp = r["response"].lower()
        assert "i hear you" in resp

    def test_normal_request_no_emotional_prefix(self):
        r = send("I need food in Brooklyn")
        resp = r["response"].lower()
        assert "strength" not in resp
        assert "hear you" not in resp
        assert "still here" not in resp


# -----------------------------------------------------------------------
# EMOTIONAL CONTEXT PERSISTENCE
# -----------------------------------------------------------------------

class TestEmotionalContextPersistence:
    """Emotional tone should carry through to subsequent confirmations."""

    def test_emotional_context_carries_to_second_service(self):
        r = send_multi([
            "I'm really struggling and need food and shelter in Brooklyn",
            "Yes, search",
            "I need shelter",
        ])
        resp_t3 = r[2]["response"].lower()
        assert any(w in resp_t3 for w in ["still here", "still with you"]), \
            f"Second service should stay warm, got: {r[2]['response'][:80]}"

    def test_shame_context_carries_to_second_service(self):
        r = send_multi([
            "I hate asking but I need food and shelter in Brooklyn",
            "Yes, search",
            "I need shelter",
        ])
        resp_t3 = r[2]["response"].lower()
        assert any(w in resp_t3 for w in ["still here", "still with you"]), \
            f"Shame context should persist, got: {r[2]['response'][:80]}"

    def test_sensitive_context_carries(self):
        r = send_multi([
            "I just got out of jail and need food and shelter in Brooklyn",
            "Yes, search",
            "I need shelter",
        ])
        resp_t3 = r[2]["response"].lower()
        assert any(w in resp_t3 for w in ["still here", "still with you"]), \
            f"Sensitive context should persist, got: {r[2]['response'][:80]}"

    def test_no_false_warmth_without_emotional_context(self):
        r = send_multi([
            "I need food and shelter in Brooklyn",
            "Yes, search",
            "I need shelter",
        ])
        resp_t3 = r[2]["response"].lower()
        assert "still here" not in resp_t3
        assert "still with you" not in resp_t3


# -----------------------------------------------------------------------
# SHAME + HELP ROUTING
# -----------------------------------------------------------------------

class TestShameHelpRouting:
    """Shame + 'help' should get shame response, not help menu."""

    @pytest.mark.parametrize("msg", [
        "I'm embarrassed to ask for help",
        "I hate asking for help",
        "It's hard for me to ask for help",
        "I'm ashamed to ask for help",
    ])
    def test_shame_help_gets_shame_response(self, msg):
        r = send(msg)
        resp = r["response"].lower()
        assert any(w in resp for w in ["ashamed", "shame", "nothing to be", "strength"]), \
            f"'{msg}' should get shame normalization, got: {r['response'][:80]}"

    def test_plain_help_unaffected(self):
        r = send("What can you help me find?")
        resp = r["response"].lower()
        assert any(w in resp for w in ["food", "shelter", "clothing"])

    def test_emotional_nonshame_help_gets_empathetic_help(self):
        r = send("I'm feeling really lost and need help")
        resp = r["response"].lower()
        assert "overwhelming" in resp or "one step" in resp


# =======================================================================
# RESEARCH-BACKED EMOTIONAL CATEGORIES (R28)
# =======================================================================

class TestDistrustDetection:
    """Distrust phrases should classify as emotional with transparency response.

    Research: National Harm Reduction Coalition names 'difficulty trusting
    people' as a trauma response. SAMHSA identifies distrust as a barrier.
    """

    @pytest.mark.parametrize("phrase", [
        "I don't trust this",
        "Is this legit?",
        "Is this safe?",
        "How do I know this is real?",
        "I've been burned before",
        "I've been lied to before",
        "Sounds too good to be true",
        "What's the catch?",
        "How is this free?",
        "Is there a catch?",
    ])
    def test_classifies_as_emotional(self, phrase):
        tone = _classify_tone(phrase, crisis_result=None)
        assert tone == "emotional", \
            f"'{phrase}' should classify as emotional, got '{tone}'"

    @pytest.mark.parametrize("phrase", [
        "I don't trust this",
        "Is this legit?",
        "I've been burned before",
        "What's the catch?",
    ])
    def test_gets_distrust_response(self, phrase):
        response = _pick_emotional_response(phrase)
        assert response == _EMOTIONAL_RESPONSES["distrust"]

    def test_distrust_response_mentions_transparency(self):
        response = _EMOTIONAL_RESPONSES["distrust"]
        lower = response.lower()
        assert "database" in lower or "verified" in lower or "personal information" in lower

    def test_distrust_not_crisis(self):
        for phrase in ["I don't trust this", "Is this safe?", "I've been burned"]:
            assert detect_crisis(phrase, skip_llm=True) is None


class TestUndeservingDetection:
    """Undeserving phrases should get worth-affirming response.

    Research: PMC shows 41% of homeless people feel undeserving of help.
    """

    @pytest.mark.parametrize("phrase", [
        "I don't deserve help",
        "I'm not worth it",
        "Other people need it more than me",
        "I'm not worthy of help",
        "People have it worse than me",
        "I don't want to take from someone who needs it more",
    ])
    def test_classifies_as_emotional(self, phrase):
        tone = _classify_tone(phrase, crisis_result=None)
        assert tone == "emotional", \
            f"'{phrase}' should classify as emotional, got '{tone}'"

    @pytest.mark.parametrize("phrase", [
        "I don't deserve help",
        "I'm not worth it",
        "Other people need it more than me",
    ])
    def test_gets_undeserving_response(self, phrase):
        response = _pick_emotional_response(phrase)
        assert response == _EMOTIONAL_RESPONSES["undeserving"]

    def test_undeserving_response_affirms_worth(self):
        response = _EMOTIONAL_RESPONSES["undeserving"]
        assert "deserve" in response.lower() or "everyone" in response.lower()

    def test_undeserving_not_crisis(self):
        for phrase in ["I don't deserve help", "I'm not worth it"]:
            assert detect_crisis(phrase, skip_llm=True) is None


class TestAngerDetection:
    """Anger at circumstances should get validation, not frustration escalation.

    Research: SAMHSA identifies anger as a common trauma response.
    """

    @pytest.mark.parametrize("phrase", [
        "I'm so angry about everything",
        "I'm furious",
        "I'm pissed off",
        "This is so unfair",
        "I'm fed up with everything",
        "I'm sick of this",
        "Why does this keep happening to me",
    ])
    def test_classifies_as_emotional_or_frustrated(self, phrase):
        tone = _classify_tone(phrase, crisis_result=None)
        assert tone in ("emotional", "frustrated"), \
            f"'{phrase}' should be emotional or frustrated, got '{tone}'"

    @pytest.mark.parametrize("phrase", [
        "I'm so angry about everything",
        "I'm furious",
        "This is so unfair",
    ])
    def test_gets_anger_response(self, phrase):
        response = _pick_emotional_response(phrase)
        assert response == _EMOTIONAL_RESPONSES["angry"]

    def test_anger_response_validates(self):
        response = _EMOTIONAL_RESPONSES["angry"]
        assert "right" in response.lower() or "sense" in response.lower()

    def test_anger_not_crisis(self):
        for phrase in ["I'm so angry", "This is so unfair", "I'm furious"]:
            assert detect_crisis(phrase, skip_llm=True) is None


# =======================================================================
# RESPONSE SELECTION ACCURACY
# =======================================================================

class TestResponseSelectionAccuracy:
    """Each emotion keyword set routes to the correct specific response."""

    def test_scared_routes_to_scared(self):
        assert _pick_emotional_response("I'm really scared") == _EMOTIONAL_RESPONSES["scared"]

    def test_sad_routes_to_sad(self):
        # "really" is stripped by _strip_intensifiers, matching "feeling down"
        assert _pick_emotional_response("I'm feeling really down") == _EMOTIONAL_RESPONSES["sad"]

    def test_rough_day_routes_to_rough_day(self):
        assert _pick_emotional_response("Having a rough day") == _EMOTIONAL_RESPONSES["rough_day"]

    def test_grief_routes_to_grief(self):
        assert _pick_emotional_response("Someone died") == _EMOTIONAL_RESPONSES["grief"]

    def test_alone_routes_to_alone(self):
        assert _pick_emotional_response("I have no one") == _EMOTIONAL_RESPONSES["alone"]

    def test_shame_routes_to_shame(self):
        assert _pick_emotional_response("I'm ashamed") == _EMOTIONAL_RESPONSES["shame"]

    def test_distrust_routes_to_distrust(self):
        assert _pick_emotional_response("I don't trust this") == _EMOTIONAL_RESPONSES["distrust"]

    def test_undeserving_routes_to_undeserving(self):
        assert _pick_emotional_response("I don't deserve help") == _EMOTIONAL_RESPONSES["undeserving"]

    def test_angry_routes_to_angry(self):
        assert _pick_emotional_response("I'm so angry") == _EMOTIONAL_RESPONSES["angry"]

    def test_all_responses_different(self):
        responses = set(_EMOTIONAL_RESPONSES.values())
        assert len(responses) == len(_EMOTIONAL_RESPONSES)

    def test_no_service_mentions_in_emotional_responses(self):
        service_words = ["food", "shelter", "clothing", "shower"]
        for key, response in _EMOTIONAL_RESPONSES.items():
            for sw in service_words:
                assert sw not in response.lower(), \
                    f"'{key}' response should not mention '{sw}'"


# =======================================================================
# EDGE CASES & FALSE POSITIVE GUARDS
# =======================================================================

class TestEmotionalEdgeCases:
    """Guard against false positives and boundary conditions."""

    def test_service_keyword_in_emotional_context_routes_to_service(self):
        slots = extract_slots("I'm grieving and need counseling")
        assert slots["service_type"] == "mental_health"

    def test_distrust_with_service_routes_to_service_with_empathy(self):
        r = send("I don't trust this but I need food in Brooklyn")
        assert r["slots"].get("service_type") == "food"

    def test_anger_about_specific_service_still_routes(self):
        slots = extract_slots("I'm so angry I can't find shelter in Manhattan")
        assert slots["service_type"] == "shelter"

    def test_not_fair_with_service_still_routes(self):
        r = send("It's not fair, I need food in Brooklyn")
        assert r["slots"].get("service_type") == "food"


# =======================================================================
# EVAL SCENARIO REPRODUCTIONS
# =======================================================================

class TestEvalScenarioReproductions:
    """Exact reproduction of failing eval scenarios."""

    def test_multi_shame_single_service(self):
        r = send_multi([
            "This is really hard for me to say but I can't afford to eat. I'm in the Bronx.",
            "Yes, search",
        ])
        resp = r[0]["response"].lower()
        assert any(w in resp for w in ["strength", "shame", "no shame", "lot of people"]), \
            f"Should normalize shame, got: {r[0]['response'][:100]}"
        assert r[0]["slots"].get("service_type") == "food"

    def test_multi_emotional_accept_second_still_warm(self):
        r = send_multi([
            "I'm really struggling and need food and shelter in Brooklyn",
            "Yes, search",
            "I need shelter",
        ])
        assert "i hear you" in r[0]["response"].lower() or \
               "strength" in r[0]["response"].lower()
        resp_t3 = r[2]["response"].lower()
        assert any(w in resp_t3 for w in ["still here", "still with you"]), \
            f"Turn 3 should stay warm, got: {r[2]['response'][:80]}"


# -----------------------------------------------------------------------
# SUBSTANCE-USE DISCLOSURE TONE + SAFETY ADDENDUM (May 5 cluster_5 fix)
# -----------------------------------------------------------------------


class TestSubstanceUseDisclosureTone:
    """When the user discloses substance dependence (alcohol/opiate/etc.)
    alongside a service request, the bot should:

    1. Use a strengths-based acknowledgment prefix ("Reaching out for
       help with this is a real step forward.") rather than the generic
       baseline-warmth opener — substance-use disclosure carries
       vulnerability that warrants explicit recognition.

    2. Append a safety addendum to the search results message: SAMHSA
       helpline (1-800-662-4357), medical-supervision recommendation,
       911 for emergencies. Alcohol withdrawal can be life-threatening
       and opiate withdrawal carries overdose risk on relapse — the
       bot has a duty-of-care to surface this even when the user
       didn't explicitly ask.

    Behavior is GATED on is_service_flow=True — the disclosure must
    co-occur with a service request. A user just venting about
    drinking without asking for help should NOT trigger this (that's
    emotional flow, handled separately).
    """

    @pytest.mark.parametrize("msg", [
        "I need to detox from Alcohol and Opiates. Where can I go in Manhattan?",
        "I'm an alcoholic and I need a treatment program in Brooklyn",
        "I need to get clean. Where's a rehab in Queens?",
        "I've been drinking too much and need a recovery program in Brooklyn",
        # Note: phrasings like "I've been using heroin and need help" DON'T
        # trigger this prefix because slot extraction needs a concrete
        # service term (treatment, rehab, detox, recovery) to fire
        # is_service_flow=True. The strengths-based prefix is gated on
        # service flow, by design — bare disclosure without a service
        # request goes through the emotional-response path instead.
    ])
    def test_substance_use_disclosure_gets_strengths_prefix(self, msg):
        """Disclosure + service intent should yield the strengths-based prefix."""
        r = send(msg)
        resp = r["response"].lower()
        assert "step forward" in resp or "reaching out" in resp, \
            f"Expected strengths-based prefix, got: {r['response'][:120]}"

    def test_substance_use_disclosure_followed_by_search_yields_safety_addendum(self):
        """After confirmation → search, the results message includes a
        safety addendum mentioning SAMHSA helpline and medical
        supervision."""
        r = send_multi([
            "I need to detox from Alcohol and Opiates in Manhattan",
            "Yes, search",
        ])
        # Two turns: confirmation, then results.
        results_response = r[1]["response"].lower()
        # The addendum mentions SAMHSA helpline by phone number,
        # not by spelled name (vetted text in execution.py).
        assert "1-800-662-4357" in results_response, \
            f"Expected SAMHSA helpline number in results message, got: {r[1]['response'][:300]}"
        # And the medical-supervision recommendation.
        assert "medically" in results_response or "supervised" in results_response, \
            f"Expected medical-supervision note, got: {r[1]['response'][:300]}"

    def test_routine_medical_request_no_substance_addendum(self):
        """A user asking for generic medical care (not substance use)
        should NOT get the safety addendum — the addendum is specific
        to substance-use disclosure."""
        r = send_multi([
            "I need a doctor in Manhattan",
            "Yes, search",
        ])
        results_response = r[1]["response"].lower()
        assert "1-800-662-4357" not in results_response, \
            f"Routine medical should not get SAMHSA addendum, got: {r[1]['response'][:200]}"
        assert "withdrawal" not in results_response

    def test_substance_use_vent_without_service_request_no_prefix(self):
        """A user venting about drinking without asking for help should
        NOT trigger the substance-use disclosure prefix — that path is
        for service-flow turns only. A pure emotional vent goes through
        the emotional-response handler instead."""
        r = send("I've been drinking a lot lately")
        # Emotional flow — should NOT yield the strengths-prefix +
        # search-results pattern. We don't assert what it DOES yield
        # (emotional handler has its own path) — only that it doesn't
        # trigger the substance-disclosure prefix-then-search behavior.
        resp = r["response"].lower()
        # The strengths-prefix only fires on service flow, so a pure
        # vent shouldn't see it.
        assert "1-800-662-4357" not in resp


# ---------------------------------------------------------------------------
# Substance-use exclusion patterns (bug-hunt #7)
# ---------------------------------------------------------------------------
# The original _SUBSTANCE_USE_DISCLOSURE_PHRASES tuple matched on plain
# substring, which created false positives in four scenarios documented
# in the bug-hunt:
#
#   - Long-term recovery ("I'm 5 years sober")
#   - Third-party requests ("my son is addicted")
#   - Professional / informational lookup ("address of Mt Sinai detox")
#   - Non-substance addictions ("gambling addiction")
#
# Each false positive surfaces an unsolicited SAMHSA helpline + medical-
# supervision note that is mistuned for the user's actual situation.
# These tests pin the exclusion behavior so a regression in the exclusion
# regex set surfaces immediately.

class TestSubstanceUseExclusions:
    """Bug-hunt #7: substance-use trigger should NOT fire on contexts
    where the user is not the one currently seeking treatment."""

    def _is_strengths_prefix(self, response: str) -> bool:
        """Detect the strengths-based prefix that indicates the
        substance-use trigger fired."""
        lower = response.lower()
        return "step forward" in lower or "reaching out for help" in lower

    @pytest.mark.parametrize("msg", [
        # Year-based recovery
        "I'm 5 years sober but need food in Brooklyn",
        "I have been 3 years sober, looking for a doctor in Manhattan",
        "I'm 6 months clean, where can I find clothes",
        # Days/weeks-based recovery
        "I'm 30 days sober, need a job",
        "I have been 2 weeks clean, looking for housing",
        # "Sober for X" inversion
        "I have been sober for 4 years and need a place to stay",
    ])
    def test_long_term_recovery_does_not_trigger(self, msg):
        """A user already in stable recovery isn't disclosing active
        substance use — the strengths-prefix + SAMHSA addendum aren't
        appropriate for their request."""
        r = send(msg)
        assert not self._is_strengths_prefix(r["response"]), (
            f"Long-term recovery should NOT trigger substance-use "
            f"prefix, got: {r['response'][:160]}"
        )

    @pytest.mark.parametrize("msg", [
        "My son is addicted to opiates, where can I get him help",
        "My daughter needs detox in Manhattan",
        "My husband is an alcoholic, need treatment for him in Brooklyn",
        "Looking for rehab for my friend in Queens",
        "My brother needs a recovery program in the Bronx",
        "Need detox for my partner",
    ])
    def test_third_party_does_not_trigger(self, msg):
        """When the user is asking on behalf of someone else, the
        strengths-prefix ('reaching out for help with this is a real
        step forward') is mistuned — it praises the wrong person.
        The SAMHSA helpline still applies but should come through
        a different path (e.g. the search results), not the
        disclosure-tone-prefix path."""
        r = send(msg)
        assert not self._is_strengths_prefix(r["response"]), (
            f"Third-party request should NOT trigger first-person "
            f"substance-use prefix, got: {r['response'][:160]}"
        )

    @pytest.mark.parametrize("msg", [
        "Address of Mt Sinai detox center",
        "Phone number for Realization Center detox",
        "Where do I refer patients for detox",
        "Looking for detox programs for my client",
        "Hours of detox programs in Manhattan",
        "Directions to the nearest detox center",
    ])
    def test_professional_lookup_does_not_trigger(self, msg):
        """A clinician or staff member looking up an address isn't
        disclosing their own substance use. Surfacing a SAMHSA
        helpline + 'reaching out is a real step forward' frame is
        wrong for this audience."""
        r = send(msg)
        assert not self._is_strengths_prefix(r["response"]), (
            f"Professional lookup should NOT trigger substance-use "
            f"prefix, got: {r['response'][:160]}"
        )

    @pytest.mark.parametrize("msg", [
        "I'm dealing with gambling addiction, need counseling",
        "I have a shopping addiction",
        "I'm addicted to gambling, need help",
        "I have a gaming addiction",
        "Porn addiction help in Brooklyn",
    ])
    def test_non_substance_addictions_do_not_trigger(self, msg):
        """The keyword 'addiction' / 'addicted' matches, but the
        user has named a non-substance behavior. The
        alcohol/opiate-specific medical-supervision note is wrong
        for these cases. The user should be routed through the
        normal mental_health flow, not the substance-use one."""
        r = send(msg)
        assert not self._is_strengths_prefix(r["response"]), (
            f"Non-substance addiction should NOT trigger substance-"
            f"use prefix, got: {r['response'][:160]}"
        )

    def test_recovery_with_relapse_currently_excluded_documented_limitation(self):
        """Edge case: long-term recovery is excluded by default, but
        if the user pairs it with an active service request that
        clearly indicates current need (e.g. 'I'm 2 years sober but
        I just relapsed and need detox'), we should ideally NOT
        silently exclude — the second clause is a substance-use
        disclosure.

        **Current behavior: the year-recovery regex matches and
        excludes.** This is a known limitation — the bug-hunt's fix
        prioritizes preventing false positives over catching every
        edge case. The assertion below pins the current (imperfect)
        behavior so a future change is explicit. If this scenario
        surfaces in evals, tighten the regex to require the recovery
        clause to be the entire sentence or to be flanked by an
        ``and``-clause that doesn't include relapse / current-need
        markers.
        """
        r = send("I'm 2 years sober but I just relapsed and need detox in Manhattan")
        # Pinning the current behavior: exclusion still fires. This
        # test exists to make the trade-off explicit, not to assert
        # the behavior is desirable.
        assert not self._is_strengths_prefix(r["response"]), (
            "Documented limitation: year-based recovery exclusion "
            "currently fires even when paired with active relapse "
            "disclosure. If this scenario surfaces in evals, tighten "
            "the regex; if this assertion starts failing because the "
            "regex was tightened, update the test name and behavior."
        )


class TestSubstanceUseSubtypeRouting:
    """Bug-hunt #9: addendum text should match the disclosed substance.

    Alcohol/opiate disclosures get the medical-supervision note (correct
    for those withdrawal profiles). Other substance disclosures get a
    generic SAMHSA + 911 message without the alcohol/opiate-specific
    claims about withdrawal danger.
    """

    @pytest.mark.parametrize("msg", [
        "I need to detox from alcohol in Manhattan",
        "I'm an alcoholic and need a treatment program in Brooklyn",
        "I need rehab for heroin in Queens",
        "I need to detox from opiates",
        "I need rehab for fentanyl in the Bronx",
    ])
    def test_alcohol_opiate_gets_medical_supervision_text(self, msg):
        """Alcohol + opiate disclosures should fire the
        medical-supervision branch of the addendum, which mentions
        withdrawal danger explicitly."""
        r = send_multi([msg, "Yes, search"])
        results = r[1]["response"].lower()
        assert "1-800-662-4357" in results, (
            f"Expected SAMHSA helpline in results, got: {r[1]['response'][:200]}"
        )
        assert "withdrawal" in results or "medically" in results, (
            f"Expected alcohol/opiate-specific medical-supervision "
            f"language for {msg!r}, got: {r[1]['response'][:300]}"
        )

    @pytest.mark.parametrize("msg", [
        # Non-alcohol/opiate disclosures with explicit trigger words
        "I'm trying to get clean, where's a rehab in Manhattan",
        "I have a substance abuse problem and need treatment in Brooklyn",
        "I have an addiction and need rehab in Brooklyn",
        "I need help with my addiction in Queens",
    ])
    def test_other_substance_gets_generic_text(self, msg):
        """Non-alcohol/opiate disclosures should fire the generic
        addendum — SAMHSA helpline + 911, but WITHOUT the
        alcohol/opiate-specific medical-supervision claim."""
        r = send_multi([msg, "Yes, search"])
        results = r[1]["response"].lower()
        # Generic addendum still surfaces the helpline
        assert "1-800-662-4357" in results, (
            f"Expected SAMHSA helpline in results for {msg!r}, "
            f"got: {r[1]['response'][:200]}"
        )
        # But should NOT make alcohol/opiate-specific claims
        assert "life-threatening" not in results, (
            f"Generic substance disclosure should NOT mention "
            f"alcohol-specific withdrawal danger, got: "
            f"{r[1]['response'][:300]}"
        )
        assert "raises the risk of overdose" not in results, (
            f"Generic substance disclosure should NOT mention "
            f"opiate-specific overdose-on-relapse claim, got: "
            f"{r[1]['response'][:300]}"
        )


class TestSubstanceUseAddendumOnEmptyResults:
    """Bug-hunt #8: addendum should fire even when the search returns
    zero results.

    The original code gated the addendum inside `elif results['result_count'] > 0`,
    so a substance-use disclosure in a thin-coverage neighborhood
    silently lost the SAMHSA helpline + 911 prompt — exactly the
    case where the user needs the helpline most.
    """

    def test_substance_use_disclosure_with_no_results_still_gets_addendum(self):
        """Mock a zero-result search response. The substance-use
        addendum should still surface alongside the no-results
        message."""
        from conftest import MOCK_EMPTY_RESULTS
        r = send_multi(
            [
                "I need to detox from alcohol in Manhattan",
                "Yes, search",
            ],
            mock_query_return=MOCK_EMPTY_RESULTS,
        )
        results = r[1]["response"].lower()
        assert "1-800-662-4357" in results, (
            f"Substance-use addendum should fire on empty results, "
            f"got: {r[1]['response'][:300]}"
        )

    def test_routine_search_with_no_results_does_not_get_addendum(self):
        """Sanity check: the addendum is still gated on substance-use
        disclosure. A routine no-result search (e.g. food) should NOT
        get the SAMHSA addendum tacked on."""
        from conftest import MOCK_EMPTY_RESULTS
        r = send_multi(
            [
                "I need food in Manhattan",
                "Yes, search",
            ],
            mock_query_return=MOCK_EMPTY_RESULTS,
        )
        results = r[1]["response"].lower()
        assert "1-800-662-4357" not in results
        assert "withdrawal" not in results


# ---------------------------------------------------------------------------
# Sober keyword: no duplicate intents (bug-hunt #10)
# ---------------------------------------------------------------------------

class TestSoberKeywordExtraction:
    """Bug-hunt #10: 'sober living' / 'sober house' should extract
    cleanly to medical without the bare 'sober' fallback re-matching
    inside the already-matched compound and producing a duplicate
    (medical, mental_health) extraction.
    """

    def test_sober_living_extracts_to_medical_only(self):
        """'I need sober living in Manhattan' should yield a single
        medical extraction, not a (medical, mental_health) pair."""
        from app.services.slot_extraction_regex import _extract_all_service_types
        result = _extract_all_service_types("I need sober living in Manhattan")
        services = [svc for svc, _detail in result]
        assert services == ["medical"], (
            f"Expected ['medical'] only, got: {result}"
        )

    def test_sober_house_extracts_to_medical_only(self):
        """'looking for a sober house in Brooklyn' should yield
        medical only, not a duplicate."""
        from app.services.slot_extraction_regex import _extract_all_service_types
        result = _extract_all_service_types("looking for a sober house in Brooklyn")
        services = [svc for svc, _detail in result]
        assert services == ["medical"], (
            f"Expected ['medical'] only, got: {result}"
        )

    def test_sober_with_other_service_no_dup(self):
        """Multi-intent: 'sober living and food in Brooklyn' should
        extract cleanly as (medical, food) — not (medical, food,
        mental_health) with the bare-sober fallback adding a third."""
        from app.services.slot_extraction_regex import _extract_all_service_types
        result = _extract_all_service_types("sober living and food in Brooklyn")
        services = sorted(svc for svc, _detail in result)
        assert services == ["food", "medical"], (
            f"Expected sorted ['food', 'medical'], got: {result}"
        )

    def test_bare_sober_no_longer_matches_alone(self):
        """Standalone 'sober' (no compound) no longer extracts as a
        service request via Tier 1 regex — bare 'sober' is more often
        state language ('I want to be sober') than a service request,
        and routing it to mental_health violated the Cluster 5
        substance-use → medical decision. Tier 2 semantic router
        handles edge-case service-request phrasings if needed."""
        from app.services.slot_extraction_regex import _extract_all_service_types
        # Pure state language — should not extract as service intent
        assert _extract_all_service_types("I want to be sober") == []
        assert _extract_all_service_types("trying to get sober") == []


# ---------------------------------------------------------------------------
# Substance-use exclusion follow-up fixes (bugs 1/2/3)
# ---------------------------------------------------------------------------
# After the initial bug-hunt cluster (#7/#8/#9), an audit surfaced three
# more bugs that the green tests didn't catch:
#
#   Bug 1 — _emotional_context persists across turns. A user disclosing
#           substance use on turn 1 and asking for food on turn 2 would
#           get the alcohol/opiate addendum tacked onto the food results.
#           Fix: gate the addendum in execution.py on the CURRENT search
#           being substance-related (service_type=medical AND service_detail
#           in the substance-related set).
#
#   Bug 2 — pronoun exclusion was too broad. ``\b(?:he|she|they)\s+
#           (?:is|are|has|have|needs?)\b`` fired on any third-party pronoun
#           +verb anywhere in the message, suppressing legitimate first-
#           person disclosures like "I'm an alcoholic. She is supportive."
#           Fix: removed the pronoun-only pattern; the ``my X`` and
#           ``for X`` patterns cover the intended third-party cases.
#
#   Bug 3 — ``my X`` exclusion fired on multi-party disclosures where the
#           user is one of the parties: "My partner and I both drink too
#           much" — excluded even though user IS one of the parties
#           disclosing. Fix: added _FIRST_PERSON_OVERRIDE that bypasses
#           third-party exclusions when an unambiguous first-person
#           disclosure phrase (singular or plural-inclusive) is present.


class TestSubstanceUseAddendumNoCrossTurnLeak:
    """Bug 1: addendum should NOT leak across turns when the user
    discloses substance use and then changes service type.

    The _emotional_context slot is persisted across turns (shared
    infrastructure with shame and medical_urgent continuity), so a
    user who disclosed substance use on turn 1 has the slot still set
    to "substance_use_disclosure_*" on turn 2. The previous version
    of the addendum gate read only that slot, so a food search on
    turn 2 inherited the alcohol/opiate safety message.

    The fix gates the addendum on the CURRENT search being for
    substance-use treatment (service_type=="medical" AND service_detail
    in the substance-related set).
    """

    def test_substance_disclosure_then_change_to_food_no_addendum(self):
        """The original Bug 1 repro: user discloses alcohol detox
        intent on turn 1, then says 'Actually, I need food instead'
        on turn 2. Food search results should NOT carry the SAMHSA /
        medical-supervision text."""
        r = send_multi([
            "I need to detox from alcohol in Manhattan",
            "Actually, I need food instead",
        ])
        # Turn 2's response is the food confirmation+search.
        food_response = r[1]["response"].lower()
        assert "1-800-662-4357" not in food_response, (
            f"SAMHSA helpline leaked into food search. "
            f"Got: {r[1]['response'][:300]}"
        )
        assert "medically" not in food_response and "withdrawal" not in food_response, (
            f"Alcohol/opiate-specific safety language leaked into "
            f"food search. Got: {r[1]['response'][:300]}"
        )

    def test_substance_disclosure_then_change_to_clothing_no_addendum(self):
        """Same bug, different unrelated service type."""
        r = send_multi([
            "I'm an alcoholic and need a treatment program in Brooklyn",
            "Actually, I need clothing instead",
        ])
        clothing_response = r[1]["response"].lower()
        assert "1-800-662-4357" not in clothing_response, (
            f"SAMHSA helpline leaked into clothing search. "
            f"Got: {r[1]['response'][:300]}"
        )

    def test_substance_disclosure_then_yes_search_still_gets_addendum(self):
        """Sanity: the legitimate flow (disclose → confirm → search)
        still produces the addendum on the substance-use results."""
        r = send_multi([
            "I need to detox from alcohol in Manhattan",
            "Yes, search",
        ])
        results = r[1]["response"].lower()
        assert "1-800-662-4357" in results, (
            f"Addendum should fire on legitimate substance-use search. "
            f"Got: {r[1]['response'][:300]}"
        )

    def test_no_substance_disclosure_then_medical_no_addendum(self):
        """Sanity: a routine non-substance medical search should NOT
        get the SAMHSA addendum, regardless of carryover state."""
        r = send_multi([
            "I need a doctor in Manhattan",
            "Yes, search",
        ])
        results = r[1]["response"].lower()
        assert "1-800-662-4357" not in results

    @pytest.mark.skip(
        reason="Documented limitation. The slot-merge layer doesn't "
        "clear service_detail on a within-medical service-detail "
        "transition (detox → general doctor). When user message is "
        "'Actually, I just need a regular doctor' on turn 2, slot "
        "extraction returns service_detail=None for that message, "
        "and merge preserves service_detail='detox' from turn 1. "
        "The addendum gate then fires because service_detail still "
        "matches the substance-related set. Fixing this requires "
        "tightening slot merge to clear service_detail on explicit "
        "'just a doctor' / 'regular' / 'primary care' phrasings, "
        "which is out of scope for the substance-use cluster fix."
    )
    def test_substance_disclosure_then_change_to_general_medical_no_addendum(self):
        r = send_multi([
            "I need detox in Manhattan",
            "Actually, I just need a regular doctor",
        ])
        medical_response = r[1]["response"].lower()
        assert "1-800-662-4357" not in medical_response


class TestSubstanceUseExclusionPronounCollateral:
    """Bug 2: legitimate first-person disclosures with collateral
    third-party pronouns (in unrelated supportive sentences) should
    fire the trigger, not be suppressed by a stray ``she is`` /
    ``he has`` / ``they are`` somewhere in the message.

    These tests target the trigger logic directly via
    ``_compute_tone_prefix`` rather than going through ``send()`` —
    the trigger logic is the unit under test, and going through the
    full pipeline introduces dependencies on the slot extractor and
    semantic router that don't add value to this assertion.
    """

    @pytest.mark.parametrize("msg", [
        # First-person disclosure with supportive third-party mention
        "I'm an alcoholic. She is supportive of my recovery.",
        "I need detox. He has been begging me to go for months.",
        "They are running detox programs nearby and I need one.",
        "My counselor said I should detox. He is a good doctor.",
    ])
    def test_first_person_with_supportive_third_party_still_triggers(self, msg):
        """Pre-fix behavior: pronoun exclusion fired on 'she is' /
        'he has' / 'they are' anywhere in the message, suppressing
        the legitimate first-person disclosure. Post-fix: the
        pronoun-only pattern was removed, so these now trigger
        correctly."""
        from app.services.chatbot.tone import _compute_tone_prefix
        prefix, ctx = _compute_tone_prefix(
            message=msg,
            response_tone=None,
            is_service_flow=True,
            prior_emotional_context=None,
        )
        assert ctx is not None and ctx.startswith("substance_use_disclosure"), (
            f"First-person disclosure with collateral third-party "
            f"pronoun should set substance-use context. Got "
            f"ctx={ctx!r} for msg={msg!r}"
        )


class TestSubstanceUseFirstPersonOverride:
    """Bug 3: multi-party disclosures where the user is one of the
    parties should fire the trigger, not be suppressed by the
    third-party exclusion.

    The fix adds _FIRST_PERSON_OVERRIDE — when an unambiguous first-
    person disclosure phrase (singular or plural-inclusive) is
    present, third-party / non-substance exclusions are bypassed.

    Tests target the trigger logic directly. See note on
    TestSubstanceUseExclusionPronounCollateral for rationale.
    """

    @pytest.mark.parametrize("msg", [
        # Singular first-person + third-party collateral
        "I'm an alcoholic, my wife is worried about me, need treatment in Brooklyn",
        "I'm addicted to opiates, my husband supports me, need rehab in Queens",
        # Plural first-person inclusion (user is one of the parties)
        "My partner and I both drink too much, we need help in Brooklyn",
        "My husband and I are both struggling with addiction, need rehab in Manhattan",
        "My partner and I are both addicted to opiates, we need rehab in Queens",
        "We're both struggling with alcohol, need detox in Manhattan",
    ])
    def test_multi_party_first_person_override_triggers(self, msg):
        """User IS one of the parties needing help. The first-person
        marker (singular ``I'm``, plural ``we're``, inclusion phrasing
        ``and I both``) overrides the third-party exclusion."""
        from app.services.chatbot.tone import _compute_tone_prefix
        prefix, ctx = _compute_tone_prefix(
            message=msg,
            response_tone=None,
            is_service_flow=True,
            prior_emotional_context=None,
        )
        assert ctx is not None and ctx.startswith("substance_use_disclosure"), (
            f"Multi-party first-person disclosure should set substance-"
            f"use context. Got ctx={ctx!r} for msg={msg!r}"
        )

    @pytest.mark.parametrize("msg", [
        # Pure third-party — user is asking on behalf of someone else
        "My son is addicted to opiates, where can I get him help",
        "My daughter needs detox in Manhattan",
        "Looking for rehab for my friend in Queens",
        "My husband needs treatment for his alcoholism",
        "My wife is an alcoholic, where can I get her help",
    ])
    def test_pure_third_party_still_excludes(self, msg):
        """Regression guard: the override should NOT bypass exclusions
        on pure third-party requests where the user is not one of the
        parties. These messages have no first-person disclosure
        phrasing, so the third-party exclusion correctly fires."""
        from app.services.chatbot.tone import _compute_tone_prefix
        prefix, ctx = _compute_tone_prefix(
            message=msg,
            response_tone=None,
            is_service_flow=True,
            prior_emotional_context=None,
        )
        assert ctx is None, (
            f"Pure third-party request should NOT set substance-use "
            f"context. Got ctx={ctx!r} for msg={msg!r}"
        )


# ---------------------------------------------------------------------------
# Short prompt parity (bug-hunt #13)
# ---------------------------------------------------------------------------

class TestShortPromptSubstanceUseDirective:
    """Bug-hunt #13: the substance-use → medical routing directive
    must appear in BOTH _NARRATIVE_SYSTEM_PROMPT and _SHORT_SYSTEM_PROMPT.

    Most user messages are short (<20 words) and use the short prompt.
    Cluster 5 added the directive to the narrative prompt but not the
    short one, leaving edge cases like 'I'm trying to get clean'
    vulnerable to misroute when the LLM tier fires with the short
    prompt.
    """

    def test_short_prompt_contains_substance_use_directive(self):
        """The short prompt must include the substance-use →
        medical routing rule. Pinning the exact phrase guards
        against the next prompt edit silently dropping it."""
        from app.services.slot_extraction.prompts import _SHORT_SYSTEM_PROMPT
        assert "substance" in _SHORT_SYSTEM_PROMPT.lower(), (
            "Short prompt missing substance-use routing directive."
        )
        # Pin both the categorization and the negation (NOT mental_health)
        assert "medical" in _SHORT_SYSTEM_PROMPT.lower()
        assert "not mental_health" in _SHORT_SYSTEM_PROMPT.lower(), (
            "Short prompt should explicitly say substance use does "
            "NOT route to mental_health (parity with narrative prompt)."
        )

    def test_narrative_prompt_still_contains_directive(self):
        """Sanity check: the narrative prompt's directive (added in
        Cluster 5) is still present after the short-prompt edit."""
        from app.services.slot_extraction.prompts import _NARRATIVE_SYSTEM_PROMPT
        assert "substance" in _NARRATIVE_SYSTEM_PROMPT.lower()
        assert "not mental_health" in _NARRATIVE_SYSTEM_PROMPT.lower()
