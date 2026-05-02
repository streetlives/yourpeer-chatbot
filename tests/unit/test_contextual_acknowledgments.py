"""Tests for ``chatbot.contextual_acknowledgments``.

Each acknowledgment helper is tested for:
* Positive: fires on the actual eval-scenario user-turn text.
* Negative: doesn't fire on transactional or unrelated messages.
* Boundary: behavior at the edge of the detection threshold.

The positive tests use the literal user-turn strings from the eval
suite (``tests/eval/eval_llm_judge.py``) so a future scenario edit that
breaks the trigger surfaces here.
"""

import pytest

from app.services.chatbot.contextual_acknowledgments import (
    _PERSONAL_STORY_MIN_WORDS,
    _combined_contextual_acknowledgments,
    _is_family_with_children_urgent_shelter,
    _is_personal_story,
    _is_rough_sleeper,
    _is_substance_use_shelter,
    _path_intake_acknowledgment,
    _personal_story_acknowledgment,
    _rough_sleeper_acknowledgment,
    _substance_use_shelter_acknowledgment,
)


# ---------------------------------------------------------------------------
# Eval scenario texts — pinned to keep tests in sync
# ---------------------------------------------------------------------------

# wa_tell_my_story turn 1
TELL_MY_STORY = (
    "Let me explain my situation. I'm 34, I lost my job three months "
    "ago and got evicted last week. I have a 6 year old daughter with "
    "me. We've been staying at my sister's in Brooklyn but she can't "
    "keep us anymore. I need to find us a shelter and also figure out "
    "how to get food stamps."
)

# wa_rough_sleeper_urgent turn 1
ROUGH_SLEEPER = (
    "I'm sleeping on the street tonight. I don't have anywhere to go "
    "and nobody is helping me."
)

# wa_substance_use_shelter turn 1
SUBSTANCE_USE = (
    "I need a shelter in Manhattan. I'm struggling with alcohol and "
    "I need a place that won't kick me out for that."
)

# multi_family_with_children_path turn 1
FAMILY_PATH_MSG = "I have two kids and we need somewhere to sleep tonight and food. We're in Harlem."

# peer_young_mom_multiple_needs turn 1
YOUNG_MOM_MSG = "19-year-old mom with a baby, need shelter, diapers, food, and basic healthcare right now."


# ===========================================================================
# Personal-story acknowledgment
# ===========================================================================


class TestPersonalStoryDetection:
    def test_fires_on_tell_my_story_scenario(self):
        """Eval-anchored positive case."""
        assert _is_personal_story(TELL_MY_STORY) is True

    def test_does_not_fire_on_short_request(self):
        assert _is_personal_story("I need food in Brooklyn") is False

    def test_does_not_fire_on_long_transactional_request(self):
        """Long but no personal-narrative markers — must not fire."""
        msg = (
            "I am looking for a food pantry near me in Brooklyn please. "
            "Specifically I would like a place that has fresh produce "
            "available and accepts walk-ins without an appointment. Could "
            "you also tell me if they have hours on Saturday and whether "
            "they require any documentation to be served. Thank you so "
            "much for your help with this question and I appreciate it."
        )
        assert len(msg.split()) >= 50  # confirm it's long enough to test the marker check
        assert _is_personal_story(msg) is False

    def test_does_not_fire_on_short_with_markers(self):
        """Single marker phrase but message too short — must not fire."""
        assert _is_personal_story("I lost my job. I need help.") is False

    def test_does_not_fire_on_long_with_one_marker(self):
        """Long enough but only one marker — must not fire (we require 2+)."""
        msg = (
            "I lost my job a while ago and now I am wondering if there "
            "are any food pantries in the Brooklyn area that I could "
            "visit this week, ideally somewhere accessible by subway "
            "and with weekend hours so I can plan around looking for "
            "work during the weekdays."
        )
        # only "i lost" should match
        assert _is_personal_story(msg) is False

    def test_does_not_fire_on_long_with_only_situation_keyword(self):
        """Bug 1 regression guard. Before the dedupe, a long message
        containing only 'Here's my situation' would double-count via
        substring overlap ('here's my situation' + 'my situation' = 2
        marker hits) and falsely trigger. The fix dropped the bare
        'my situation' marker so this no longer over-counts.
        """
        msg = (
            "Here's my situation: I'm asking about food pantries near "
            "the Brooklyn area please. Specifically I would like to "
            "know if any of them have hours on Saturday afternoons "
            "and whether they accept walk-in clients without an "
            "appointment scheduled in advance please. Thank you for "
            "any information you can share with me."
        )
        assert len(msg.split()) >= _PERSONAL_STORY_MIN_WORDS
        # Only "here's my situation" should match. One conceptual
        # signal = one marker hit = below threshold.
        assert _is_personal_story(msg) is False

    def test_does_not_fire_on_long_with_only_she_cant_keep(self):
        """Bug 1 regression guard for the second overlap pair.
        'she can't keep' previously matched both itself AND the
        substring 'he can't keep'. Dropping the latter ensures one
        conceptual signal counts as one marker."""
        msg = (
            "I am wondering about food pantries in Brooklyn. I have a "
            "friend who said she can't keep recommending the same "
            "place to me every week, so I figured I would ask for "
            "alternatives. Specifically I am looking for somewhere with "
            "weekend hours and ideally fresh produce available."
        )
        assert len(msg.split()) >= _PERSONAL_STORY_MIN_WORDS
        # Only "she can't keep" should match.
        assert _is_personal_story(msg) is False

    def test_fires_with_curly_apostrophes(self):
        """Bug 2 regression guard. Mobile users frequently send curly
        apostrophes (U+2019). The marker list uses straight ones, so
        without normalization the markers silently fail to match. The
        fix normalizes apostrophes at the start of each detector.
        """
        # Replace ALL straight apostrophes in TELL_MY_STORY with curly
        curly = TELL_MY_STORY.replace("'", "\u2019")
        # Confirm we actually have curly apostrophes in the test input
        assert "\u2019" in curly
        assert _is_personal_story(curly) is True

    def test_returns_false_on_empty(self):
        assert _is_personal_story("") is False
        assert _is_personal_story(None) is False


class TestPersonalStoryAcknowledgmentText:
    def test_fires_with_warmth_text(self):
        out = _personal_story_acknowledgment({}, TELL_MY_STORY)
        assert "Thank you" in out or "thank you" in out

    def test_ends_with_paragraph_break(self):
        """Same convention as _immigration_acknowledgment so the
        next prefix or response renders cleanly below."""
        out = _personal_story_acknowledgment({}, TELL_MY_STORY)
        assert out.endswith("\n\n")

    def test_empty_when_signal_absent(self):
        assert _personal_story_acknowledgment({}, "I need food in Brooklyn") == ""

    def test_does_not_slot_fill(self):
        """Acknowledgment should not pre-empt the bot's normal flow.
        Mutant kill: if someone tries to add 'where are you?' or
        'what specifically do you need' to the prefix, this fails.
        """
        out = _personal_story_acknowledgment({}, TELL_MY_STORY)
        assert "where" not in out.lower()
        assert "what specifically" not in out.lower()
        assert "?" not in out  # no questions in this prefix


# ===========================================================================
# PATH intake acknowledgment
# ===========================================================================


class TestPathIntakeDetection:
    def _slots(self, **overrides):
        base = {
            "family_status": "with_children",
            "urgency": "high",
            "service_type": "shelter",
        }
        base.update(overrides)
        return base

    def test_fires_with_all_three_signals(self):
        assert _is_family_with_children_urgent_shelter(self._slots()) is True

    def test_no_fire_without_family_status(self):
        slots = self._slots()
        slots.pop("family_status")
        assert _is_family_with_children_urgent_shelter(slots) is False

    def test_no_fire_with_alone_family_status(self):
        """Mutant kill: equality check on 'with_children' specifically."""
        assert _is_family_with_children_urgent_shelter(
            self._slots(family_status="alone")
        ) is False

    def test_no_fire_with_with_family_status(self):
        """'with_family' (partner/spouse) is NOT 'with_children' — must not fire."""
        assert _is_family_with_children_urgent_shelter(
            self._slots(family_status="with_family")
        ) is False

    def test_no_fire_without_urgency(self):
        assert _is_family_with_children_urgent_shelter(
            self._slots(urgency=None)
        ) is False

    def test_no_fire_with_medium_urgency(self):
        """Mutant kill: equality check on 'high' specifically."""
        assert _is_family_with_children_urgent_shelter(
            self._slots(urgency="medium")
        ) is False

    def test_no_fire_with_food_service_type(self):
        assert _is_family_with_children_urgent_shelter(
            self._slots(service_type="food")
        ) is False

    def test_fires_when_shelter_is_queued_not_primary(self):
        """Multi-intent case: food primary, shelter queued — should still fire
        because the family DOES need shelter, even if it's queued."""
        slots = self._slots(
            service_type="food",
            additional_services=[("shelter", None, None)],
        )
        assert _is_family_with_children_urgent_shelter(slots) is True

    def test_fires_when_shelter_is_in_queued_services_key(self):
        """Same case as above but using the runtime _queued_services
        slot name (which the orchestrator uses post-merge)."""
        slots = self._slots(
            service_type="food",
            _queued_services=[("shelter", None, None)],
        )
        assert _is_family_with_children_urgent_shelter(slots) is True

    def test_no_fire_when_neither_primary_nor_queued_is_shelter(self):
        slots = self._slots(
            service_type="food",
            additional_services=[("clothing", None, None)],
        )
        assert _is_family_with_children_urgent_shelter(slots) is False


class TestPathIntakeAcknowledgmentText:
    def test_mentions_path_intake(self):
        slots = {"family_status": "with_children", "urgency": "high",
                 "service_type": "shelter"}
        out = _path_intake_acknowledgment(slots, "")
        assert "PATH" in out

    def test_mentions_bronx_address(self):
        """Surface the specific intake address so user has actionable info."""
        slots = {"family_status": "with_children", "urgency": "high",
                 "service_type": "shelter"}
        out = _path_intake_acknowledgment(slots, "")
        assert "Bronx" in out
        assert "151" in out  # 151 East 151st Street

    def test_mentions_311_connection_path(self):
        slots = {"family_status": "with_children", "urgency": "high",
                 "service_type": "shelter"}
        out = _path_intake_acknowledgment(slots, "")
        assert "311" in out

    def test_empty_when_signal_absent(self):
        assert _path_intake_acknowledgment({}, "") == ""


# ===========================================================================
# Rough sleeper acknowledgment
# ===========================================================================


class TestRoughSleeperDetection:
    def test_fires_on_eval_scenario(self):
        assert _is_rough_sleeper(ROUGH_SLEEPER) is True

    @pytest.mark.parametrize("msg", [
        "I'm sleeping on the street tonight",
        "I'm sleeping outside",
        "I'm a rough sleeper",
        "I have nowhere to go tonight",
        "I have nowhere to sleep right now",
        "homeless tonight",
        "I don't have anywhere to go",
        "I'm out on the street",
    ])
    def test_fires_on_distinctive_phrasings(self, msg):
        assert _is_rough_sleeper(msg) is True, f"Should fire on: {msg!r}"

    @pytest.mark.parametrize("msg", [
        "I need shelter in Brooklyn",
        "I'm looking for a place to sleep",  # ambiguous — could be preventive
        "I might need shelter soon",
        "I was on the street last year",  # past tense
        "what street are the shelters on?",
    ])
    def test_does_not_fire_on_non_distinctive(self, msg):
        assert _is_rough_sleeper(msg) is False, f"Should NOT fire on: {msg!r}"

    def test_fires_with_curly_apostrophes(self):
        """Bug 2 regression guard for rough-sleeper detector.

        ``i'?m sleeping on the street`` and similar patterns in the
        regex use a straight apostrophe. iOS/Android autocorrect
        produces curly (U+2019) by default, so without normalization
        a message like 'I'm sleeping on the street' (curly) would
        slip past the I'm-prefixed alternations.

        Note: this scenario also matches the unprefixed
        ``sleep(?:ing)? (?:on|in) the street`` pattern, so the
        test crafts a message that ONLY hits the apostrophe-bearing
        alternative to verify normalization specifically. We use
        ``i'm on the street`` which only matches via ``i'?m on the street``.
        """
        msg = "I\u2019m on the street and need help"  # curly apostrophe
        assert "\u2019" in msg
        assert _is_rough_sleeper(msg) is True

    def test_returns_false_on_empty(self):
        assert _is_rough_sleeper("") is False
        assert _is_rough_sleeper(None) is False


class TestRoughSleeperAcknowledgmentText:
    def test_mentions_311(self):
        out = _rough_sleeper_acknowledgment({}, ROUGH_SLEEPER)
        assert "311" in out

    def test_mentions_home_stat(self):
        """HOME-STAT is the specific mobile outreach team — surface
        the name so the user can ask for it by name when calling 311."""
        out = _rough_sleeper_acknowledgment({}, ROUGH_SLEEPER)
        assert "HOME-STAT" in out

    def test_mentions_shelter_text_line(self):
        """'Text SHELTER to 67283' is an alternative to phone."""
        out = _rough_sleeper_acknowledgment({}, ROUGH_SLEEPER)
        assert "67283" in out
        assert "SHELTER" in out

    def test_mentions_safe_haven(self):
        out = _rough_sleeper_acknowledgment({}, ROUGH_SLEEPER)
        assert "Safe Haven" in out

    def test_empty_when_signal_absent(self):
        assert _rough_sleeper_acknowledgment({}, "I need food in Brooklyn") == ""


# ===========================================================================
# Substance-use shelter acknowledgment
# ===========================================================================


class TestSubstanceUseShelterDetection:
    def test_fires_on_eval_scenario(self):
        assert _is_substance_use_shelter(
            {"service_type": "shelter"}, SUBSTANCE_USE
        ) is True

    def test_fires_on_disclosure_with_substance_noun(self):
        assert _is_substance_use_shelter(
            {"service_type": "shelter"},
            "I have trouble with drugs"
        ) is True

    def test_fires_on_harm_reduction_phrasing_alone(self):
        """User who already knows the framing — 'won't kick me out' — even
        without disclosing the substance, should trigger."""
        assert _is_substance_use_shelter(
            {"service_type": "shelter"},
            "I need a low-barrier shelter"
        ) is True

    def test_does_not_fire_when_service_type_is_not_shelter(self):
        """Same disclosure but asking for food — should NOT fire."""
        assert _is_substance_use_shelter(
            {"service_type": "food"}, SUBSTANCE_USE
        ) is False

    def test_fires_when_shelter_is_queued(self):
        """Multi-intent: food primary, shelter queued — should still fire
        because the substance-use concern applies to the shelter search."""
        assert _is_substance_use_shelter(
            {
                "service_type": "food",
                "_queued_services": [("shelter", None, None)],
            },
            SUBSTANCE_USE,
        ) is True

    def test_does_not_fire_on_disclosure_without_substance_noun(self):
        """Avoid false positive: 'struggling with rent', 'in recovery from
        surgery' — disclosure phrasing without a substance noun."""
        assert _is_substance_use_shelter(
            {"service_type": "shelter"},
            "I'm struggling with rent and need a shelter"
        ) is False

    def test_does_not_fire_on_substance_noun_without_disclosure(self):
        """Avoid false positive: 'I drink water'. Substance noun alone,
        without disclosure phrasing or harm-reduction language, does not
        fire."""
        assert _is_substance_use_shelter(
            {"service_type": "shelter"},
            "I drink water and need a shelter"
        ) is False

    def test_fires_with_curly_apostrophes(self):
        """Bug 2 regression guard. ``won'?t kick (?:me|us) out`` pattern
        uses a straight apostrophe. Curly-apostrophe input from mobile
        users would otherwise miss the harm-reduction path."""
        msg = "I need a shelter that won\u2019t kick me out"
        assert "\u2019" in msg
        assert _is_substance_use_shelter(
            {"service_type": "shelter"}, msg
        ) is True

    def test_returns_false_on_empty(self):
        assert _is_substance_use_shelter({"service_type": "shelter"}, "") is False
        assert _is_substance_use_shelter({"service_type": "shelter"}, None) is False


class TestSubstanceUseShelterAcknowledgmentText:
    def test_mentions_low_barrier_or_harm_reduction(self):
        out = _substance_use_shelter_acknowledgment(
            {"service_type": "shelter"}, SUBSTANCE_USE
        )
        # Either phrasing — but at least one must appear so user
        # knows what category of shelter to ask about
        assert "low-barrier" in out.lower() or "harm reduction" in out.lower()

    def test_mentions_samhsa_helpline(self):
        out = _substance_use_shelter_acknowledgment(
            {"service_type": "shelter"}, SUBSTANCE_USE
        )
        assert "SAMHSA" in out
        assert "1-800-662-4357" in out

    def test_empty_when_signal_absent(self):
        assert _substance_use_shelter_acknowledgment(
            {"service_type": "shelter"}, "I just need shelter"
        ) == ""


# ===========================================================================
# Combined entry point
# ===========================================================================


class TestCombinedEntryPoint:
    def test_empty_when_nothing_fires(self):
        assert _combined_contextual_acknowledgments({}, "I need food") == ""

    def test_single_acknowledgment_fires_alone(self):
        out = _combined_contextual_acknowledgments({}, ROUGH_SLEEPER)
        assert "311" in out
        # No PATH content (family signal not present)
        assert "PATH" not in out
        # No substance-use content
        assert "SAMHSA" not in out
        # No personal-story content (rough_sleeper is too short)
        assert "Thank you for telling me" not in out

    def test_personal_story_comes_first_when_multiple_fire(self):
        """When personal_story AND another acknowledgment both fire,
        the warmth prefix must come first.

        wa_tell_my_story has a 6-year-old daughter, urgent shelter
        need (evicted last week, sister can't keep them), so PATH
        could potentially fire — but only if family_status,
        urgency, and service_type slots are populated. Test with
        slots set explicitly.
        """
        slots = {
            "family_status": "with_children",
            "urgency": "high",
            "service_type": "shelter",
        }
        out = _combined_contextual_acknowledgments(slots, TELL_MY_STORY)
        # Both prefixes should be present
        assert "Thank you" in out
        assert "PATH" in out
        # Order check: personal-story warmth before PATH
        assert out.index("Thank you") < out.index("PATH")

    def test_path_comes_before_rough_sleeper_when_both_fire(self):
        """Family with children, sleeping outside tonight — both fire.
        PATH first because it's the more directive resource."""
        slots = {
            "family_status": "with_children",
            "urgency": "high",
            "service_type": "shelter",
        }
        msg = "I have my kids and we are sleeping on the street tonight."
        out = _combined_contextual_acknowledgments(slots, msg)
        assert "PATH" in out
        assert "311" in out
        assert out.index("PATH") < out.index("311")

    def test_each_acknowledgment_ends_with_double_newline(self):
        """Each prefix must end with \\n\\n — concatenation safe."""
        slots = {
            "family_status": "with_children",
            "urgency": "high",
            "service_type": "shelter",
        }
        out = _combined_contextual_acknowledgments(slots, TELL_MY_STORY)
        # Should have at least 2 separators (one per acknowledgment that fired)
        assert out.count("\n\n") >= 2
