"""
Tests for backend/app/utils/text_normalize.py.

These are direct unit tests of the shared helper. The original module-
local copies in `crisis_detector.py` and `contextual_acknowledgments.py`
each had their own unit tests; those test files now serve as integration
tests of the helper in their respective contexts. This file pins the
helper's contract independently.
"""

from app.utils.text_normalize import (
    NON_STANDARD_APOSTROPHES,
    normalize_apostrophes,
)


# -----------------------------------------------------------------------
# normalize_apostrophes
# -----------------------------------------------------------------------

def test_normalize_right_single_quote():
    """U+2019 (mobile autocorrect default) → U+0027."""
    assert normalize_apostrophes("he\u2019s") == "he's"
    assert normalize_apostrophes("don\u2019t") == "don't"
    assert normalize_apostrophes("I\u2019m") == "I'm"


def test_normalize_left_single_quote():
    """U+2018 (some keyboards) → U+0027."""
    assert normalize_apostrophes("he\u2018s") == "he's"


def test_normalize_modifier_letter_apostrophe():
    """U+02BC (rare, some locales) → U+0027."""
    assert normalize_apostrophes("he\u02bcs") == "he's"


def test_normalize_grave_accent():
    """U+0060 (typed by accident) → U+0027."""
    assert normalize_apostrophes("he`s") == "he's"


def test_normalize_mixed_codepoints_in_one_string():
    """Multiple distinct non-standard apostrophes in one string all map."""
    s = "he\u2019s and she\u2018s"
    assert normalize_apostrophes(s) == "he's and she's"


def test_already_normalized_text_passes_through_unchanged():
    """No non-standard codepoints → identity transform."""
    assert normalize_apostrophes("he's") == "he's"
    assert normalize_apostrophes("plain text") == "plain text"


def test_idempotent():
    """Calling on already-normalized text returns the same result."""
    text = "he\u2019s and she\u2018s"
    once = normalize_apostrophes(text)
    twice = normalize_apostrophes(once)
    assert once == twice == "he's and she's"


def test_empty_string_passes_through():
    """Empty string returns empty string (callers may rely on truthiness)."""
    assert normalize_apostrophes("") == ""


def test_none_passes_through():
    """None input returns None (callers may rely on this for chaining)."""
    assert normalize_apostrophes(None) is None


def test_non_apostrophe_characters_untouched():
    """Curly DOUBLE quotes (U+201C, U+201D) and other punctuation are not normalized.

    Only the apostrophe-like codepoints in NON_STANDARD_APOSTROPHES get
    transformed. Curly double quotes ("smart quotes") are deliberately not
    in scope — they don't appear in any of the existing matching lists, and
    normalizing them would expand surface area unnecessarily.
    """
    assert normalize_apostrophes('he\u201Cs"') == 'he\u201Cs"'
    assert normalize_apostrophes("hello — world") == "hello — world"
    assert normalize_apostrophes("3.14") == "3.14"


def test_constant_contents():
    """The exposed constant contains exactly the four documented codepoints."""
    assert NON_STANDARD_APOSTROPHES == ("\u2019", "\u2018", "\u02bc", "\u0060")


def test_normalize_long_realistic_message():
    """A realistic crisis-style sentence with multiple curly apostrophes."""
    msg = (
        "He\u2019s going to come back tonight and I don\u2019t feel safe — "
        "they\u2019re going to find me here."
    )
    expected = (
        "He's going to come back tonight and I don't feel safe — "
        "they're going to find me here."
    )
    assert normalize_apostrophes(msg) == expected
