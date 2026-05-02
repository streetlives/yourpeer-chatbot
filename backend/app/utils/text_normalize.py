"""
Text normalization utilities used across the chatbot service layer.

These helpers are deliberately small, deterministic, and side-effect-free
so they can be imported from any layer (handlers, detectors, the
crisis_detector module, etc.) without dependency cycles.
"""

# ---------------------------------------------------------------------------
# Apostrophe normalization
# ---------------------------------------------------------------------------
#
# Mobile users frequently send curly apostrophes (U+2019) where keyword
# lists and regexes expect straight (U+0027). Without normalization, a
# detector or matcher using straight-apostrophe literals will silently
# miss messages typed on iOS/Android with autocorrect.
#
# Codepoints normalized to U+0027:
#   U+2019  RIGHT SINGLE QUOTATION MARK   '   (most common autocorrect)
#   U+2018  LEFT SINGLE QUOTATION MARK    '   (some keyboards)
#   U+02BC  MODIFIER LETTER APOSTROPHE    ʼ   (rarer; some locales)
#   U+0060  GRAVE ACCENT                  `   (typed by accident)
#
# Callers should normalize the user's input ONCE near the entry point of
# their matching pass — typically right after ``text.lower()`` — rather
# than authoring multi-codepoint character classes inside every regex,
# which is harder to read and easier to forget when adding new phrases.
NON_STANDARD_APOSTROPHES = ("\u2019", "\u2018", "\u02bc", "\u0060")


def normalize_apostrophes(text: str | None) -> str | None:
    """Replace non-standard apostrophes with straight ASCII apostrophe.

    Returns the input unchanged when ``text`` is None or empty so callers
    can pass through and check truthiness afterward (None in, None out;
    "" in, "" out).

    Idempotent: calling on already-normalized text returns the same value.
    """
    if not text:
        return text
    for ch in NON_STANDARD_APOSTROPHES:
        if ch in text:
            text = text.replace(ch, "'")
    return text
