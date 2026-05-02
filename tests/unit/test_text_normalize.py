"""
Tests for backend/app/utils/text_normalize.py.

These are direct unit tests of the shared helpers. The original module-
local copies in `crisis_detector.py`, `contextual_acknowledgments.py`,
and `classifier.py` each had their own unit tests; those test files now
serve as integration tests of the helpers in their respective contexts.
This file pins each helper's contract independently.
"""

from app.utils.text_normalize import (
    CONTRACTION_MAP,
    INTENSIFIERS,
    NON_STANDARD_APOSTROPHES,
    normalize_apostrophes,
    normalize_contractions,
    strip_intensifiers,
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


# -----------------------------------------------------------------------
# normalize_contractions
# -----------------------------------------------------------------------

def test_normalize_negative_contractions_with_apostrophe():
    """Apostrophe forms expand to 'X not' shape."""
    assert normalize_contractions("that wasn't helpful") == "that was not helpful"
    assert normalize_contractions("I don't know") == "i do not know"
    assert normalize_contractions("can't help") == "can not help"


def test_normalize_negative_contractions_without_apostrophe():
    """Apostropheless typing variants ('isnt', 'wasnt') also expand."""
    assert normalize_contractions("that wasnt helpful") == "that was not helpful"
    assert normalize_contractions("doesnt work") == "does not work"
    assert normalize_contractions("cant help") == "can not help"


def test_normalize_pronoun_contractions():
    """Pronoun contractions expand to 'I am', 'I have', 'it is', etc."""
    assert normalize_contractions("I'm struggling") == "i am struggling"
    assert normalize_contractions("im struggling") == "i am struggling"
    assert normalize_contractions("it's broken") == "it is broken"
    assert normalize_contractions("you're welcome") == "you are welcome"


def test_normalize_lowercases_input():
    """The function lowercases as a side effect — documented behavior."""
    assert normalize_contractions("I'M SCARED") == "i am scared"


def test_normalize_longer_contractions_match_first():
    """'wouldn't' must match before 'won't' — sort-by-length-desc enforces this."""
    # "wouldn't" → "would not"; if "won't" matched first, we'd get
    # "would" + remainder, then "won't" never gets to match because
    # the apostrophe is already consumed.
    assert normalize_contractions("I wouldn't go") == "i would not go"
    # Same shape verifies the ordering is intentional, not coincidence.
    assert normalize_contractions("I shouldn't go") == "i should not go"


def test_normalize_no_contractions_passes_through():
    """Plain text with no contractions: just lowercased."""
    assert normalize_contractions("plain text") == "plain text"
    assert normalize_contractions("Hello World") == "hello world"


def test_normalize_empty_string():
    """Empty string returns empty string."""
    assert normalize_contractions("") == ""


def test_contraction_map_contents():
    """Spot-check the map exposes the documented entries."""
    assert CONTRACTION_MAP["isn't"] == "is not"
    assert CONTRACTION_MAP["i'm"] == "i am"
    assert CONTRACTION_MAP["wouldn't"] == "would not"
    # No-apostrophe variants must coexist with apostrophe variants
    assert CONTRACTION_MAP["isnt"] == CONTRACTION_MAP["isn't"]
    assert CONTRACTION_MAP["im"] == CONTRACTION_MAP["i'm"]


# -----------------------------------------------------------------------
# strip_intensifiers
# -----------------------------------------------------------------------

def test_strip_single_intensifier():
    """A single intensifier disappears with its trailing whitespace."""
    assert strip_intensifiers("I'm really scared") == "I'm scared"
    assert strip_intensifiers("feeling pretty hopeless") == "feeling hopeless"


def test_strip_multiple_intensifiers():
    """Adjacent intensifiers all strip; whitespace collapses."""
    assert strip_intensifiers("I'm so incredibly down") == "I'm down"


def test_strip_at_word_boundary_only():
    """'really' inside a word should NOT strip — \\b boundary protects it.

    Mutant kill: dropping the \\b would corrupt words containing
    intensifier substrings ("really" → strip the prefix of "reallybad").
    """
    # 'reallybad' is not a real word but tests the boundary behavior.
    assert strip_intensifiers("reallybad") == "reallybad"
    # 'just' is in the set; 'justice' contains it but shouldn't strip.
    assert strip_intensifiers("seeking justice") == "seeking justice"


def test_strip_case_insensitive():
    """Intensifiers in any case strip — re.IGNORECASE."""
    assert strip_intensifiers("I'm REALLY scared") == "I'm scared"
    assert strip_intensifiers("Very tired") == "tired"


def test_strip_collapses_internal_whitespace():
    """Stripped intensifier leaves no double-spaces in the output."""
    # Without collapse, "I'm  really  scared" → "I'm  scared" (2 spaces).
    result = strip_intensifiers("I'm  really  scared")
    assert "  " not in result


def test_strip_no_intensifiers_passes_through():
    """Plain text with no intensifiers is returned trimmed."""
    assert strip_intensifiers("plain text") == "plain text"
    assert strip_intensifiers("nothing to strip here") == "nothing to strip here"


def test_strip_returns_stripped_result():
    """Trailing/leading whitespace from stripping is removed."""
    # Leading intensifier leaves a leading space → must trim.
    assert strip_intensifiers("really hungry") == "hungry"
    # Trailing intensifier leaves trailing junk → must trim.
    assert strip_intensifiers("hungry really") == "hungry"


def test_intensifiers_set_contents():
    """Spot-check the exposed set — entries the rubric depends on."""
    assert "really" in INTENSIFIERS
    assert "very" in INTENSIFIERS
    assert "so" in INTENSIFIERS
    assert "incredibly" in INTENSIFIERS
    # Not an intensifier — would over-strip.
    assert "good" not in INTENSIFIERS
    assert "bad" not in INTENSIFIERS


def test_strip_then_normalize_compose():
    """Composing strip_intensifiers with normalize_contractions is a
    common pattern in the classifier — ensure they compose without
    surprise."""
    msg = "I'm really not okay"
    expanded = normalize_contractions(msg)  # "i am really not okay"
    stripped = strip_intensifiers(expanded)  # "i am not okay"
    assert stripped == "i am not okay"
