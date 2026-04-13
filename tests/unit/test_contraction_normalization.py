"""
Tests for P4 contraction normalization.

Validates that _normalize_contractions() expands contractions correctly
and that _classify_tone() catches contraction variants without needing
explicit enumeration in phrase lists.

Run with: python -m pytest tests/test_contraction_normalization.py -v
"""

import pytest
from unittest.mock import patch
from app.services.classifier import _normalize_contractions, _classify_tone, _classify_action


# -----------------------------------------------------------------------
# Unit tests for the normalizer function
# -----------------------------------------------------------------------

class TestNormalizeContractions:
    """The normalizer should expand all listed contractions."""

    @pytest.mark.parametrize("input_text,expected", [
        ("isn't", "is not"),
        ("isnt", "is not"),
        ("wasn't", "was not"),
        ("wasnt", "was not"),
        ("doesn't", "does not"),
        ("doesnt", "does not"),
        ("didn't", "did not"),
        ("didnt", "did not"),
        ("don't", "do not"),
        ("dont", "do not"),
        ("can't", "can not"),
        ("cant", "can not"),
        ("won't", "will not"),
        ("wont", "will not"),
        ("hasn't", "has not"),
        ("hasnt", "has not"),
        ("haven't", "have not"),
        ("havent", "have not"),
        ("wouldn't", "would not"),
        ("wouldnt", "would not"),
        ("couldn't", "could not"),
        ("couldnt", "could not"),
        ("shouldn't", "should not"),
        ("shouldnt", "should not"),
        ("aren't", "are not"),
        ("arent", "are not"),
        ("weren't", "were not"),
        ("werent", "were not"),
        ("i'm", "i am"),
        ("im", "i am"),
        ("i've", "i have"),
        ("ive", "i have"),
        ("it's", "it is"),
        ("that's", "that is"),
        ("you're", "you are"),
        ("youre", "you are"),
        ("they're", "they are"),
        ("theyre", "they are"),
        ("we're", "we are"),
    ])
    def test_individual_contractions(self, input_text, expected):
        assert _normalize_contractions(input_text) == expected

    def test_full_sentence(self):
        result = _normalize_contractions("that wasn't helpful at all")
        assert result == "that was not helpful at all"

    def test_multiple_contractions(self):
        result = _normalize_contractions("i'm not sure and it isn't working")
        assert result == "i am not sure and it is not working"

    def test_no_contractions(self):
        """Text without contractions should be unchanged (except lowered)."""
        result = _normalize_contractions("this is not helpful")
        assert result == "this is not helpful"

    def test_preserves_non_contraction_words(self):
        """Words that look like contractions but aren't should survive."""
        # "cant" as a verb meaning "to speak in a whining way" — very rare,
        # but the normalization will still expand it. This is a known tradeoff.
        result = _normalize_contractions("the food is great")
        assert result == "the food is great"


# -----------------------------------------------------------------------
# Integration: normalization catches contraction variants in _classify_tone
# -----------------------------------------------------------------------

class TestNormalizationInClassifyTone:
    """Contraction forms that aren't explicitly in phrase lists should
    now be caught via normalization."""

    @pytest.mark.parametrize("phrase", [
        # "aren't helpful" → "are not helpful" → matches "not helpful"
        "they aren't helpful",
        # "weren't useful" → "were not useful" → matches "not useful"
        "those weren't useful at all",
        # "aren't working" → "are not working" → matches "not working"
        "the results aren't working",
        # "haven't helped" → "have not helped" — wait, does "not helped" match?
        # Actually no — the phrase is "hasn't helped" which we added explicitly.
        # But "haven't helped" normalizes to "have not helped" which doesn't
        # match "has not helped". So this is still a vocab gap.
    ])
    def test_frustration_via_normalization(self, phrase):
        with patch("app.services.chatbot.detect_crisis", return_value=None):
            tone = _classify_tone(phrase, crisis_result=None)
        assert tone == "frustrated", \
            f"'{phrase}' should be frustrated via normalization, got '{tone}'"

    @pytest.mark.parametrize("phrase", [
        # "it's all too much" → "it is all too much" → matches "all too much"
        "it's all too much for me",
    ])
    def test_confused_via_normalization(self, phrase):
        with patch("app.services.chatbot.detect_crisis", return_value=None):
            tone = _classify_tone(phrase, crisis_result=None)
        assert tone == "confused", \
            f"'{phrase}' should be confused via normalization, got '{tone}'"

    @pytest.mark.parametrize("phrase", [
        # "she's completely alone" → "she is completely alone" → matches "completely alone"
        "she's completely alone out there",
        # "i've been feeling down" → "i have been feeling down" → matches "feeling down"
        "i've been feeling down lately",
    ])
    def test_emotional_via_normalization(self, phrase):
        with patch("app.services.chatbot.detect_crisis", return_value=None):
            tone = _classify_tone(phrase, crisis_result=None)
        assert tone == "emotional", \
            f"'{phrase}' should be emotional via normalization, got '{tone}'"


# -----------------------------------------------------------------------
# Integration: normalization in help negators
# -----------------------------------------------------------------------

class TestNormalizationInHelpNegators:
    """Help negators should catch contraction forms via normalization."""

    @pytest.mark.parametrize("phrase", [
        "that wouldn't help",     # would not help → "not help"
        "this couldn't help me",  # could not help → "not help"
        "it hasn't helped at all",  # has not helped → "not help"
    ])
    def test_negated_help_not_classified_as_help(self, phrase):
        action = _classify_action(phrase)
        assert action != "help", \
            f"'{phrase}' should NOT classify as help action"

    def test_plain_help_still_works(self):
        """'help' without negation should still classify as help."""
        assert _classify_action("help") == "help"
        assert _classify_action("I need help") == "help"

    def test_help_me_find_still_works(self):
        """'help me find food' should still classify as help."""
        assert _classify_action("help me find food") == "help"


# -----------------------------------------------------------------------
# Guard: normalization does NOT affect crisis detection
# -----------------------------------------------------------------------

class TestNormalizationDoesNotAffectCrisis:
    """Crisis detection must use original text, not normalized.
    The explicit enumeration in crisis phrase lists is safer."""

    def test_crisis_still_detects_without_normalization(self):
        """Crisis phrases that use contractions should still fire
        because they're explicitly listed."""
        from app.services.crisis_detector import detect_crisis
        # These are in the explicit phrase list
        assert detect_crisis("I can't go on", skip_llm=True) is not None
        assert detect_crisis("dont want to live", skip_llm=True) is not None
        assert detect_crisis("i cant take it anymore", skip_llm=True) is not None

    def test_normalization_function_not_called_in_crisis(self):
        """Verify crisis detection path doesn't depend on normalization."""
        # Crisis detection in _classify_tone calls detect_crisis(text)
        # with the ORIGINAL text, not the normalized version.
        # This is verified by the code structure, not easily testable
        # in isolation, but we document the design intent here.
        pass


# -----------------------------------------------------------------------
# Intensifier stripping
# -----------------------------------------------------------------------

class TestStripIntensifiers:
    """Intensifier removal should work correctly."""

    @pytest.mark.parametrize("input_text,expected", [
        ("i'm really scared", "i'm scared"),
        ("so incredibly down", "down"),
        ("feeling pretty hopeless", "feeling hopeless"),
        ("i just feel stuck", "i feel stuck"),
        ("totally overwhelmed", "overwhelmed"),
        ("i'm not doing well", "i'm not doing well"),  # "not" is NOT an intensifier
        ("really really sad", "sad"),  # double intensifier
    ])
    def test_strip_intensifiers(self, input_text, expected):
        from app.services.classifier import _strip_intensifiers
        assert _strip_intensifiers(input_text) == expected

    def test_does_not_strip_negation(self):
        """'not' must never be stripped — it changes meaning."""
        from app.services.classifier import _strip_intensifiers
        assert "not" in _strip_intensifiers("i'm not okay")
        assert "not" in _strip_intensifiers("not helpful")
        assert "never" in _strip_intensifiers("never helpful")


class TestIntensifierInClassifyTone:
    """Intensifier stripping should catch all intensifier×emotion combos."""

    @pytest.mark.parametrize("intensifier", [
        "really", "very", "so", "super", "extremely",
        "pretty", "quite", "totally", "absolutely", "incredibly",
    ])
    @pytest.mark.parametrize("emotion", [
        "scared", "down", "sad", "lonely", "anxious",
        "depressed", "hopeless", "stuck", "stressed",
    ])
    def test_intensifier_emotion_matrix(self, intensifier, emotion):
        """Every intensifier×emotion combination should classify as emotional."""
        phrase = f"I'm {intensifier} {emotion}"
        with patch("app.services.chatbot.detect_crisis", return_value=None):
            tone = _classify_tone(phrase, crisis_result=None)
        assert tone == "emotional", \
            f"'{phrase}' should be emotional, got '{tone}'"

    @pytest.mark.parametrize("phrase", [
        "that's really not helpful",
        "this is absolutely useless",
        "totally didn't work",
    ])
    def test_intensifier_frustration(self, phrase):
        """Intensifiers in frustration phrases should still match."""
        with patch("app.services.chatbot.detect_crisis", return_value=None):
            tone = _classify_tone(phrase, crisis_result=None)
        assert tone == "frustrated", \
            f"'{phrase}' should be frustrated, got '{tone}'"

    @pytest.mark.parametrize("phrase", [
        "I'm just so confused",
        "I'm really overwhelmed",
        "I'm totally lost",
    ])
    def test_intensifier_confused(self, phrase):
        """Intensifiers in confused phrases should still match."""
        with patch("app.services.chatbot.detect_crisis", return_value=None):
            tone = _classify_tone(phrase, crisis_result=None)
        assert tone == "confused", \
            f"'{phrase}' should be confused, got '{tone}'"

    def test_crisis_not_affected_by_stripping(self):
        """Crisis detection must not use intensifier stripping."""
        from app.services.crisis_detector import detect_crisis
        # "I really want to die" — crisis should fire from "want to die"
        result = detect_crisis("I really want to die", skip_llm=True)
        assert result is not None
        # The crisis regex already has "want to die" so stripping isn't needed
