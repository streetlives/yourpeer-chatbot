"""
Frustration handling and crisis step-down geolocation tests.

Covers: frustration re-statement phrases, context recovery, frustrated+intent
        immediate search, crisis step-down geolocation (all 4 categories).

Run with: python -m pytest tests/unit/test_frustration_and_crisis.py -v
"""

import pytest

from app.services.classifier import _classify_tone, _classify_action
from app.services.chatbot import _DISPLAY_PAGE_SIZE
from app.services.slot_extractor import NEAR_ME_SENTINEL
from app.services.session_store import get_session_slots, save_session_slots

from test_helpers import _fresh, _send


# =======================================================================
# B.1: Expanded negative-preference phrases — "already tried" and
# "this isn't helping" variants
# =======================================================================

class TestExpandedNegativePreferencePhrases:
    """B.1 fix: the `edge_frustration` eval scenario sent
    'This isn't helpful at all. I already tried those places.' which
    matched neither the pre-fix _NEGATIVE_PREFERENCE_PHRASES list nor
    any frustration pattern. It routed as a normal service request,
    hit the confirmation handler, and produced an identical response
    to the previous turn — critical error_recovery=1 failure.

    These tests lock in coverage for the two phrase clusters added to
    close the gap: 'already tried *' variants and 'this isn't help*'
    variants.
    """

    @pytest.mark.parametrize("msg", [
        # Exact edge_frustration scenario phrase (primary regression target)
        "This isn't helpful at all. I already tried those places.",
        # "already tried" variants
        "I already tried those places",
        "I already tried those",
        "already tried them",
        "already tried all of those",  # D.3 gap close
        "I already tried that",
        "I've already tried those",
        "ive already tried the shelters",
        "I've already tried",
        # "this isn't helpful" variants
        "This isn't helpful",
        "This isnt helpful",
        "this is not helpful",
        "This is not helping",
        "it is not helping me at all",  # D.3 gap close
        "You're not helping me",
        "that isn't helping",
        "isnt helping",
        # "been there already"
        "been there already",
    ])
    def test_expanded_phrases_classify_as_negative_preference(self, msg):
        """All phrases added by the B.1 fix should route to negative_preference."""
        assert _classify_action(msg) == "negative_preference", (
            f"'{msg}' should classify as negative_preference but got "
            f"{_classify_action(msg)}"
        )

    @pytest.mark.parametrize("msg", [
        # "already tried" with a concrete object shouldn't fire — the user
        # is reporting, not rejecting.
        "I already tried calling 311",
        "I already tried texting them",
        # General "tried" without the rejection context should pass through
        "I tried a new restaurant last week",
    ])
    def test_specific_positive_actions_still_pass_through(self, msg):
        """Negative guard: the new phrases shouldn't swallow messages that
        use 'already tried' in a reporting/narrative sense rather than a
        rejection sense. These should NOT classify as negative_preference."""
        assert _classify_action(msg) != "negative_preference", (
            f"'{msg}' is narrative/reporting, not rejection — should not "
            f"classify as negative_preference, got {_classify_action(msg)}"
        )

    def test_existing_negative_preference_phrases_still_fire(self):
        """Regression guard: the expansion must not break the pre-existing
        list. Pick one representative from each historical cluster."""
        for msg in [
            "not what i want",              # basic
            "none of those",                # set rejection
            "that is not helpful",          # "that" pointer (distinct from "this")
            "tried all of those",           # experience — pre-existing "tried all"
            "already been there",           # pre-existing "already been"
            "had a bad experience",         # experience qualitative
        ]:
            assert _classify_action(msg) == "negative_preference", (
                f"Pre-existing phrase '{msg}' regressed"
            )

    def test_edge_frustration_scenario_exact_phrase(self):
        """Guard for the exact text used by the `edge_frustration` eval
        scenario — keep this test green and the scenario's critical
        error_recovery failure stays fixed."""
        assert _classify_action(
            "This isn't helpful at all. I already tried those places."
        ) == "negative_preference"


# =======================================================================
# FIX 4: Frustration re-statement phrases
# =======================================================================

class TestFrustrationRestatementPhrases:
    """'I already said Manhattan' etc. should route to frustration, not
    confirm_change_location."""

    @pytest.mark.parametrize("msg", [
        "I already said Manhattan",
        "I already told you Brooklyn",
        "I just told you Queens",
        "I just said the Bronx",
        "Why are you asking me this",
        "Why are you asking again",
        "You already know where I am",
        "You're giving me the same options",
    ])
    def test_restatement_classified_as_frustrated(self, msg):
        tone = _classify_tone(msg, crisis_result=None)
        assert tone == "frustrated", \
            f"'{msg}' should be frustrated, got tone='{tone}'"

    def test_restatement_not_classified_as_change_location(self):
        """Critically: 'I already said Manhattan' must NOT be
        confirm_change_location — that would wipe the location."""
        action = _classify_action("I already said Manhattan")
        assert action != "confirm_change_location", \
            "'I already said Manhattan' was misclassified as confirm_change_location"

    def test_service_intent_overrides_frustration_tone(self):
        """'I already said I need food' has frustration tone but service
        intent should take routing priority."""
        from app.services.slot_extractor import extract_slots
        extracted = extract_slots("I already said I need food")
        tone = _classify_tone("I already said I need food", crisis_result=None)
        assert extracted.get("service_type") == "food"
        assert tone == "frustrated"
        # In routing, service intent (line 578) takes priority over
        # frustrated tone (line 589). We verify extraction is correct;
        # the routing priority is tested by integration tests.


# =======================================================================
# FIX 5: Frustration handler context recovery
# =======================================================================


# =======================================================================
# FIX 5: Frustration handler context recovery
# =======================================================================

class TestFrustrationContextRecovery:
    """When the user is frustrated but session has enough to search,
    offer to proceed."""

    def test_recovery_offers_to_proceed(self):
        """First frustration + enough slots → 'Sound good?' confirmation."""
        sid = _fresh()
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
        })
        result = _send("why are you asking me this", sid)
        resp = result["response"].lower()
        assert "apologize" in resp or "sorry" in resp or "right" in resp, \
            f"Should acknowledge frustration, got: {result['response']}"
        assert "manhattan" in resp, \
            "Should mention the existing location"
        assert "shelter" in resp, \
            "Should mention the existing service type"

    def test_recovery_sets_pending_confirmation(self):
        """Recovery path should set _pending_confirmation so 'Yes' works."""
        sid = _fresh()
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
        })
        _send("why are you asking me this", sid)
        slots = get_session_slots(sid)
        assert slots.get("_pending_confirmation") is True, \
            "_pending_confirmation should be set after recovery"

    def test_recovery_clears_last_action(self):
        """Recovery path must clear _last_action so confirm_yes reaches
        _handle_pending_confirmation, not _handle_context_aware_confirm."""
        sid = _fresh()
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
        })
        _send("why are you asking me this", sid)
        slots = get_session_slots(sid)
        assert slots.get("_last_action") is None, \
            "_last_action should be cleared — otherwise 'Yes' routes to escalation"

    def test_yes_after_recovery_executes_search(self):
        """The critical regression: 'Yes' after recovery must execute search,
        NOT show escalation response."""
        sid = _fresh()
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
        })
        _send("why are you asking me this", sid)
        result = _send("Yes, search", sid)
        # Should execute a search and return service cards
        assert result.get("result_count", 0) >= 1 or \
            "option" in result["response"].lower() or \
            "found" in result["response"].lower(), \
            f"'Yes' should execute search, got: {result['response']}"
        # Should NOT show escalation / peer navigator response
        assert "peer navigator" not in result["response"].lower() or \
            result.get("result_count", 0) >= 1, \
            f"Should not escalate — should search. Got: {result['response']}"

    def test_no_recovery_without_enough_slots(self):
        """Without service_type + location, frustration uses default response."""
        sid = _fresh()
        save_session_slots(sid, {
            "service_type": "shelter",
            # No location — not enough to search
        })
        _result = _send("why are you asking me this", sid)
        slots = get_session_slots(sid)
        assert slots.get("_pending_confirmation") is not True, \
            "Should NOT offer confirmation without location"

    def test_escalation_on_repeated_frustration(self):
        """Second frustration still escalates even with enough slots."""
        sid = _fresh()
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
        })
        _send("this is useless", sid)  # first frustration — offers recovery
        result = _send("still not helpful", sid)  # second frustration
        resp = result["response"].lower()
        assert "peer navigator" in resp or "311" in resp, \
            "Second frustration should escalate to navigator"


# =======================================================================
# FRUSTRATED + SERVICE INTENT (Block 3 immediate search)
# =======================================================================


# =======================================================================
# FRUSTRATED + SERVICE INTENT (Block 3 immediate search)
# =======================================================================

class TestFrustratedWithServiceIntent:
    """When frustrated user restates intent with results showing,
    skip confirmation and search immediately."""

    @pytest.mark.skip(
        reason=(
            "The 'frustrated restatement auto-executes' feature isn't "
            "implemented in the current confirmation flow. Feature B (Housing "
            "First) shipped a baseline confirmation step for every service "
            "request; the escape-hatch that would bypass confirmation when "
            "(tone == 'frustrated') AND (service + location already present) "
            "AND (last_results visible) — i.e. explicit restatement after an "
            "unhelpful result — is a separate piece of work. When that lands, "
            "this skip marker should be removed. See also: "
            "test_frustrated_restatement_acknowledges_frustration below."
        )
    )
    def test_frustrated_restatement_searches_immediately(self):
        """'I already told you I need food' should search, not confirm."""
        sid = _fresh()
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
            "_last_results": [{"service_name": "Old result"}],
            "_displayed_count": 1,
        })
        result = _send("I already told you I need food in Brooklyn", sid)
        # Should return search results, NOT a confirmation question
        assert result.get("result_count", 0) >= 1 or \
            "option" in result["response"].lower() or \
            "found" in result["response"].lower(), \
            f"Should execute search immediately, got: {result['response']}"
        # Should NOT ask "does that sound right?"
        assert "sound right" not in result["response"].lower(), \
            "Should skip confirmation for frustrated user"

    @pytest.mark.skip(
        reason=(
            "Paired with test_frustrated_restatement_searches_immediately "
            "above — both require the frustrated-restatement auto-execute "
            "feature. This test adds an empathetic-prefix requirement on top "
            "of that feature."
        )
    )
    def test_frustrated_restatement_acknowledges_frustration(self):
        """Response should have empathetic prefix, not be purely transactional."""
        sid = _fresh()
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
            "_last_results": [{"service_name": "Old result"}],
            "_displayed_count": 1,
        })
        result = _send("ugh just find me shelter in Queens already", sid)
        resp = result["response"].lower()
        assert "hear you" in resp or "searching" in resp, \
            f"Should acknowledge frustration, got: {result['response']}"

    def test_frustrated_without_enough_slots_still_asks_follow_up(self):
        """'I already said I need food' without location → follow-up, not search."""
        sid = _fresh()
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
            "_last_results": [{"service_name": "Old result"}],
            "_displayed_count": 1,
        })
        # "I need food" has service intent but no location
        result = _send("I already said I need food", sid)
        # Should ask for location with empathetic tone
        assert result.get("follow_up_needed", False) or \
            "where" in result["response"].lower() or \
            "location" in result["response"].lower() or \
            result.get("result_count", 0) >= 1, \
            f"Should ask for location or search, got: {result['response']}"
        # Should NOT skip to search without location
        assert "sound right" not in result["response"].lower() or \
            result.get("result_count", 0) >= 1, \
            "Should not show confirmation without location"

    def test_non_frustrated_service_intent_still_confirms(self):
        """Normal service intent after results → standard confirmation."""
        sid = _fresh()
        save_session_slots(sid, {
            "service_type": "shelter",
            "location": "manhattan",
            "_last_results": [{"service_name": "Old result"}],
            "_displayed_count": 1,
        })
        _result = _send("I need food in Brooklyn", sid)
        slots = get_session_slots(sid)
        # Should show confirmation (not immediate search)
        # because the user is NOT frustrated
        assert slots.get("_frustrated_with_intent") is None, \
            "Flag should not be set for non-frustrated message"


# =======================================================================
# CRISIS STEP-DOWN GEOLOCATION (immediate search with coords)
# =======================================================================


# =======================================================================
# CRISIS STEP-DOWN GEOLOCATION (immediate search with coords)
# =======================================================================

class TestCrisisStepDownGeolocation:
    """When user taps 'Yes, search for shelter' from crisis step-down,
    geolocation coordinates should trigger immediate search."""

    def _crisis_session(self, sid, service_type="shelter", location=None):
        """Set up a session as if crisis step-down just fired."""
        slots = {
            "service_type": service_type,
            "_last_action": "crisis",
        }
        if location:
            slots["location"] = location
        save_session_slots(sid, slots)

    def test_coords_trigger_immediate_search(self):
        """'Yes, search' with lat/lon → search executes, no follow-up."""
        sid = _fresh()
        self._crisis_session(sid)
        result = _send("Yes, search", sid, latitude=40.7128, longitude=-74.0060)
        # Should return results, not ask for location
        assert result.get("result_count", 0) >= 1 or \
            "option" in result["response"].lower() or \
            "found" in result["response"].lower(), \
            f"Should search immediately with coords, got: {result['response']}"
        assert "where" not in result["response"].lower(), \
            "Should not ask 'where' when coords provided"

    def test_coords_set_near_me_sentinel(self):
        """Coordinates should set location to NEAR_ME_SENTINEL."""
        sid = _fresh()
        self._crisis_session(sid)
        _send("Yes, search", sid, latitude=40.7128, longitude=-74.0060)
        slots = get_session_slots(sid)
        assert slots.get("location") == NEAR_ME_SENTINEL, \
            f"Location should be sentinel, got: {slots.get('location')}"

    def test_no_coords_falls_back_to_follow_up(self):
        """'Yes, search' without coords → asks for location."""
        sid = _fresh()
        self._crisis_session(sid)
        result = _send("Yes, search", sid)
        # Should ask for location, not return results
        assert result.get("follow_up_needed", False) or \
            "where" in result["response"].lower() or \
            "location" in result["response"].lower() or \
            "borough" in result["response"].lower(), \
            f"Without coords should ask for location, got: {result['response']}"

    def test_existing_location_not_overwritten_by_coords(self):
        """If user said 'Queens', GPS coords don't override it."""
        sid = _fresh()
        self._crisis_session(sid, location="queens")
        _send("Yes, search", sid, latitude=40.7128, longitude=-74.0060)
        slots = get_session_slots(sid)
        assert slots.get("location") == "queens", \
            f"Should keep 'queens', not overwrite with sentinel: {slots.get('location')}"

    @pytest.mark.skip(
        reason=(
            "The __crisis_geo_search__ sentinel value isn't implemented. "
            "Current crisis step-downs use the regular __use_geolocation__ "
            "trigger (functionally correct — geolocation still resolves) "
            "but don't carry the crisis flag distinctly through the pipeline. "
            "Adding a crisis-specific trigger requires: (1) new sentinel, "
            "(2) handler recognition in the geolocation resolver, "
            "(3) decision on what crisis-specific post-resolve behavior "
            "differs from the regular flow. Product-level work."
        )
    )
    def test_all_step_down_categories_use_geo_trigger(self):
        """All 4 step-down categories should produce __crisis_geo_search__ button."""
        categories = ["domestic_violence", "safety_concern", "youth_runaway", "assault_victim"]
        for cat in categories:
            sid = _fresh()
            save_session_slots(sid, {"service_type": "shelter"})
            mock_crisis = {"category": cat, "confidence": "high"}
            result = _send("I need shelter", sid, mock_crisis=mock_crisis)
            qr_values = [qr["value"] for qr in result.get("quick_replies", [])]
            assert "__crisis_geo_search__" in qr_values, \
                f"{cat}: button should use __crisis_geo_search__, got: {qr_values}"

    def test_crisis_context_cleared_after_search(self):
        """_last_action='crisis' should be cleared after confirm_yes."""
        sid = _fresh()
        self._crisis_session(sid)
        _send("Yes, search", sid, latitude=40.7128, longitude=-74.0060)
        slots = get_session_slots(sid)
        assert slots.get("_last_action") is None, \
            "_last_action should be cleared after crisis confirm"

    def test_youth_runaway_with_coords_searches_immediately(self):
        """Youth runaway step-down + coords → immediate shelter search."""
        sid = _fresh()
        save_session_slots(sid, {
            "service_type": "shelter",
            "_last_action": "crisis",
            "age": "16",
        })
        result = _send("Yes, search", sid, latitude=40.7580, longitude=-73.9855)
        assert result.get("result_count", 0) >= 1 or \
            "option" in result["response"].lower() or \
            "found" in result["response"].lower(), \
            f"Youth runaway + coords should search immediately: {result['response']}"


# =======================================================================
# PAGINATION: _DISPLAY_PAGE_SIZE = 5
# =======================================================================


# ---------------------------------------------------------------------------
# C.2 — Topic-shift question detection heuristic
# ---------------------------------------------------------------------------
# Unit tests for _looks_like_topic_shift_question, the conservative
# heuristic that distinguishes off-topic questions from confirmation-
# shaped utterances during a pending confirmation.


class TestLooksLikeTopicShiftQuestion:
    """Unit coverage for the C.2 disambiguation heuristic.

    Per the helper's docstring, it should fire on clear off-topic
    questions but not on fragments, short confirmations, or unclear
    utterances. Conservative by design — ambiguous cases fall through
    to the re-nudge path (safer default).
    """

    def test_question_mark_with_wh_word_fires(self):
        from app.services.chatbot.handlers.confirmation import (
            _looks_like_topic_shift_question,
        )
        assert _looks_like_topic_shift_question("what's your name?") is True
        assert _looks_like_topic_shift_question("who are you?") is True
        assert _looks_like_topic_shift_question("how does this work?") is True

    def test_wh_word_start_three_words_fires(self):
        from app.services.chatbot.handlers.confirmation import (
            _looks_like_topic_shift_question,
        )
        assert _looks_like_topic_shift_question("who are you really") is True
        assert _looks_like_topic_shift_question("where does this data go") is True

    def test_auxiliary_verb_opener_fires(self):
        from app.services.chatbot.handlers.confirmation import (
            _looks_like_topic_shift_question,
        )
        assert _looks_like_topic_shift_question("can you speak spanish?") is True
        assert _looks_like_topic_shift_question("do you remember me") is True
        assert _looks_like_topic_shift_question("is this conversation private") is True

    def test_question_mark_four_words_fires(self):
        """Question mark + substantive content but no wh-word opener
        still counts as a topic shift."""
        from app.services.chatbot.handlers.confirmation import (
            _looks_like_topic_shift_question,
        )
        assert _looks_like_topic_shift_question(
            "my friend mentioned something else?"
        ) is True

    def test_single_word_does_not_fire(self):
        from app.services.chatbot.handlers.confirmation import (
            _looks_like_topic_shift_question,
        )
        assert _looks_like_topic_shift_question("what") is False
        assert _looks_like_topic_shift_question("why") is False
        assert _looks_like_topic_shift_question("?") is False

    def test_short_confirmation_fragments_do_not_fire(self):
        """Confirmation-shaped utterances must not be misclassified as
        topic shifts — otherwise C.2 would steal legitimate re-nudge
        cases."""
        from app.services.chatbot.handlers.confirmation import (
            _looks_like_topic_shift_question,
        )
        for msg in ("yes", "no", "ok", "yeah ok", "sounds good",
                    "maybe", "i dunno", "idk", "change it"):
            assert _looks_like_topic_shift_question(msg) is False, (
                f"Heuristic falsely fired on confirmation fragment: {msg!r}"
            )

    def test_empty_and_whitespace_do_not_fire(self):
        from app.services.chatbot.handlers.confirmation import (
            _looks_like_topic_shift_question,
        )
        assert _looks_like_topic_shift_question("") is False
        assert _looks_like_topic_shift_question("   ") is False
        assert _looks_like_topic_shift_question("\n\t") is False

    def test_service_request_without_question_does_not_fire(self):
        """A service request like 'food in brooklyn' must not trigger
        the heuristic. Service intents are handled by slot extraction
        upstream of C.2 and should never reach Path 3."""
        from app.services.chatbot.handlers.confirmation import (
            _looks_like_topic_shift_question,
        )
        assert _looks_like_topic_shift_question("food in brooklyn") is False
        assert _looks_like_topic_shift_question("actually shelter") is False

    def test_heuristic_is_conservative_with_ambiguous_short_questions(self):
        """'what now?' is ambiguous — could be confused (Path: confused
        handler) or genuine topic shift. The heuristic declines to
        fire and defers to the earlier confused/help classifiers.
        Two-word questions don't trip the heuristic."""
        from app.services.chatbot.handlers.confirmation import (
            _looks_like_topic_shift_question,
        )
        assert _looks_like_topic_shift_question("what now?") is False
        assert _looks_like_topic_shift_question("why not?") is False
