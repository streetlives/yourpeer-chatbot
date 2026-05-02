"""
Text normalization utilities used across the chatbot service layer.

These helpers are deliberately small, deterministic, and side-effect-free
so they can be imported from any layer (handlers, detectors, the
crisis_detector module, etc.) without dependency cycles.
"""

import re

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
#   U+02BC  MODIFIER LETTER APOSTROPHE    'A'   (rarer; some locales)
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


# ---------------------------------------------------------------------------
# Contraction normalization
# ---------------------------------------------------------------------------
#
# Expands common contractions to their full forms so phrase lists only
# need the expanded version (e.g., "not helpful") to match all contraction
# variants ("isn't helpful", "isnt helpful", "wasn't helpful", etc.).
#
# Applied to frustration, emotional, and confused matching in the
# classifier. NOT applied to crisis detection — crisis uses explicit
# enumeration for safety, so the prompt's literal phrase set is the
# source of truth for what counts as crisis language.

CONTRACTION_MAP = {
    # Negative contractions -> "not" form
    "isn't": "is not", "isnt": "is not",
    "wasn't": "was not", "wasnt": "was not",
    "aren't": "are not", "arent": "are not",
    "weren't": "were not", "werent": "were not",
    "doesn't": "does not", "doesnt": "does not",
    "didn't": "did not", "didnt": "did not",
    "don't": "do not", "dont": "do not",
    "can't": "can not", "cant": "can not",
    "won't": "will not", "wont": "will not",
    "hasn't": "has not", "hasnt": "has not",
    "haven't": "have not", "havent": "have not",
    "wouldn't": "would not", "wouldnt": "would not",
    "couldn't": "could not", "couldnt": "could not",
    "shouldn't": "should not", "shouldnt": "should not",
    # Pronoun contractions
    "i'm": "i am", "im": "i am",
    "i've": "i have", "ive": "i have",
    "i'll": "i will",
    "i'd": "i would",
    "it's": "it is",
    "that's": "that is",
    "there's": "there is",
    "what's": "what is",
    "you're": "you are", "youre": "you are",
    "they're": "they are", "theyre": "they are",
    "we're": "we are",
}

# Sort by length descending so longer contractions match first
# ("wouldn't" before "won't" to avoid partial replacement)
_CONTRACTION_PAIRS = sorted(CONTRACTION_MAP.items(), key=lambda x: -len(x[0]))


def normalize_contractions(text: str) -> str:
    """Expand contractions for consistent phrase matching.

    Lowercases the input as a side effect because the contraction map
    keys are lowercase. Callers that need case-preserving normalization
    should not use this; they probably want a different helper.

    Example:
        "that wasn't helpful" -> "that was not helpful"
        "I'm struggling"     -> "i am struggling"
        "doesnt work"        -> "does not work"
    """
    result = text.lower()
    for contraction, expansion in _CONTRACTION_PAIRS:
        result = result.replace(contraction, expansion)
    return result


# ---------------------------------------------------------------------------
# Intensifier stripping
# ---------------------------------------------------------------------------
#
# Removes common intensifier adverbs that break substring contiguity in
# phrase matching. "I'm really scared" -> "I'm scared" matches the
# canonical "i'm scared" phrase. The set is curated for the kinds of
# emotional/frustration intensifiers seen in homelessness-services
# conversations; new entries should be tested against the existing
# regression suite to make sure they don't strip semantically meaningful
# words.

INTENSIFIERS = {
    "really", "very", "so", "super", "extremely", "pretty", "quite",
    "totally", "absolutely", "incredibly", "truly", "deeply",
    "terribly", "horribly", "awfully", "genuinely", "particularly",
    "just", "kinda", "sorta",
}

_INTENSIFIER_RE = re.compile(
    r'\b(' + '|'.join(re.escape(w) for w in sorted(INTENSIFIERS, key=len, reverse=True)) + r')\b\s*',
    re.IGNORECASE,
)


def strip_intensifiers(text: str) -> str:
    """Remove intensifier adverbs and collapse repeated whitespace.

    Example:
        "I'm really scared"      -> "I'm scared"
        "I'm so incredibly down" -> "I'm down"
        "feeling pretty hopeless" -> "feeling hopeless"
    """
    result = _INTENSIFIER_RE.sub('', text)
    return re.sub(r'\s{2,}', ' ', result).strip()
