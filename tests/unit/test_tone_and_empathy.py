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
