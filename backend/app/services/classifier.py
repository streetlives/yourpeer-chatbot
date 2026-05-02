"""
Message classifier for the YourPeer chatbot.

Classifies user messages into action intents and emotional tones
using keyword matching, contraction normalization, and intensifier
stripping. The LLM fallback is handled by the caller (chatbot.py).

Two orthogonal classification axes:
  - ACTION: what the user wants to DO (reset, confirm, escalate, etc.)
  - TONE:   how the user FEELS (crisis, emotional, frustrated, etc.)

The caller combines these to decide routing.
"""

import re
import logging

from app.services.crisis_detector import detect_crisis
from app.services.phrase_lists import (
    _RESET_PHRASES, _RESET_EXACT,
    _GREETING_PHRASES,
    _THANKS_PHRASES, _THANKS_EXACT,
    _HELP_PHRASES, _HELP_WORD_RE,
    _ESCALATION_PHRASES,
    _FRUSTRATION_PHRASES,
    _EMOTIONAL_PHRASES,
    _BOT_IDENTITY_PHRASES,
    _BOT_QUESTION_PHRASES,
    _CONFUSED_PHRASES,
    _URGENT_PHRASES,
    _CONFIRM_YES_EXACT, _CONFIRM_YES_STARTSWITH,
    _CONFIRM_CHANGE_SERVICE, _CONFIRM_CHANGE_LOCATION,
    _CONFIRM_DENY_EXACT, _CONFIRM_DENY_PHRASES, _CONFIRM_DENY_STARTSWITH,
)
from app.utils.text_normalize import (
    normalize_contractions as _normalize_contractions,
    strip_intensifiers as _strip_intensifiers,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SENTINEL
# ---------------------------------------------------------------------------
# Indicates the caller hasn't run detect_crisis yet.
_CRISIS_NOT_CHECKED = object()


# ---------------------------------------------------------------------------
# ACTION CLASSIFICATION
# ---------------------------------------------------------------------------

def _classify_action(text: str) -> str | None:
    """Classify a message's action intent (what the user wants to DO).

    Returns one of the action categories or None if no action detected:
        "reset", "bot_identity", "bot_question", "escalation",
        "confirm_change_service", "confirm_change_location",
        "confirm_yes", "confirm_deny", "greeting", "thanks", "help",
        "correction", "negative_preference"
    """
    lower = text.lower().strip()
    cleaned = re.sub(r"[^\w\s']", "", lower).strip()

    # Reset
    for phrase in _RESET_PHRASES:
        if phrase in cleaned:
            return "reset"
    for phrase in _RESET_EXACT:
        if cleaned == phrase:
            return "reset"

    # Correction — user tells us we misunderstood
    _CORRECTION_PHRASES = [
        "not what i meant", "not what i asked", "that's not what i",
        "thats not what i", "you misunderstood", "you got it wrong",
        "wrong thing", "i didn't ask for that", "i didnt ask for that",
        "i didn't mean", "i didnt mean", "that's wrong", "thats wrong",
        "try again", "no that's not right", "no thats not right",
        # C.2 escape-button variants (April 2026). When the topic-shift
        # disambiguation fires, it offers a "💬 I was asking something
        # else" quick reply. Clicking it routes the phrase here so the
        # correction handler clears pending and prompts for what the
        # user actually wanted. Typed variations are also caught.
        "i was asking something else", "i was asking about something else",
        "that wasn't my question", "that wasnt my question",
        "different question", "asking about something else",
    ]
    for phrase in _CORRECTION_PHRASES:
        if phrase in cleaned:
            return "correction"

    # Negative preference — user rejects all offered options
    _NEGATIVE_PREFERENCE_PHRASES = [
        "not what i want", "not what i need", "not what i am looking for",
        "i do not want any of those", "i dont want any of those",
        "i don't want any of those",
        "none of those", "none of these", "not interested in any",
        "that is not helpful", "those are not helpful",
        "those don't help", "those dont help",
        "none of them work", "none of those work",
        "i need something else", "something different",
        "those aren't what i need", "those arent what i need",
        # Experience-based rejection — user has tried the results
        "been to all of those", "been to all of them",
        "tried all of those", "tried them all", "tried all of them",
        "i don't like those", "i dont like those",
        "don't like those options", "dont like those options",
        "don't like any of those", "dont like any of those",
        "turned me away", "was really unsafe", "had a bad experience",
        "already been there", "i've been there", "ive been there",
        # B.1: "already tried" variants with "already" as temporal anchor.
        # Different from "tried all of those" above — these cover natural
        # phrasings where users frame rejection as prior experience.
        # "already tried those" is a prefix of "already tried those places"
        # via the `in cleaned` match, so the longer form is redundant but
        # kept for readability.
        "already tried those", "already tried them", "already tried all",
        "already tried those places", "already tried that",
        "i've already tried", "ive already tried",
        "been there already",
        # B.1: "This isn't helpful" — pointer "this" refers to the
        # CURRENT results/conversation, as distinct from "that is not
        # helpful" (above) which could refer to an earlier point. Both
        # exist because tense/specificity matter for detection.
        "this isn't helpful", "this isnt helpful",
        "this is not helpful", "this is not helping",
        "not helping me", "isn't helping", "isnt helping",
    ]
    for phrase in _NEGATIVE_PREFERENCE_PHRASES:
        if phrase in cleaned:
            return "negative_preference"

    # Bot identity
    for phrase in _BOT_IDENTITY_PHRASES:
        if phrase in cleaned:
            return "bot_identity"

    # Bot questions (including privacy)
    for phrase in _BOT_QUESTION_PHRASES:
        if phrase in cleaned:
            return "bot_question"

    # Escalation
    for phrase in _ESCALATION_PHRASES:
        if phrase in cleaned:
            return "escalation"

    # Confirmation actions
    #
    # Disambiguate "change to <borough>" / "switch to <borough>" BEFORE the
    # service-change phrase list runs. Without this guard, "change to
    # Brooklyn" matches "change to" in _CONFIRM_CHANGE_SERVICE and gets
    # classified as confirm_change_service — wiping the user's service
    # type instead of updating their location. Fixes
    # test_change_to_brooklyn_sets_location_directly and kin.
    _BOROUGH_CHANGE_RE = re.compile(
        r"\b(?:change|switch)\s+to\s+"
        r"(?:the\s+)?"
        r"(brooklyn|manhattan|queens|bronx|staten\s+island)\b",
        re.IGNORECASE,
    )
    if _BOROUGH_CHANGE_RE.search(cleaned):
        return "confirm_change_location"

    for phrase in _CONFIRM_CHANGE_SERVICE:
        if phrase in cleaned:
            return "confirm_change_service"
    for phrase in _CONFIRM_CHANGE_LOCATION:
        if phrase in cleaned:
            return "confirm_change_location"
    for phrase in _CONFIRM_YES_EXACT:
        if cleaned == phrase:
            return "confirm_yes"
    for phrase in _CONFIRM_YES_STARTSWITH:
        if cleaned.startswith(phrase) or cleaned == phrase:
            return "confirm_yes"
    for phrase in _CONFIRM_DENY_EXACT:
        if cleaned == phrase:
            return "confirm_deny"
    for phrase in _CONFIRM_DENY_STARTSWITH:
        if cleaned.startswith(phrase):
            return "confirm_deny"
    for phrase in _CONFIRM_DENY_PHRASES:
        if phrase in cleaned:
            return "confirm_deny"

    # Greeting (short messages only)
    if len(cleaned.split()) <= 3:
        for phrase in _GREETING_PHRASES:
            if cleaned == phrase or cleaned.startswith(phrase + " "):
                return "greeting"

    # Thanks (only if no continuation words)
    _has_continuation = any(w in cleaned for w in [
        "but", "however", "though", "need", "want", "also", "more",
    ])
    if not _has_continuation:
        for phrase in _THANKS_PHRASES:
            if phrase in cleaned:
                return "thanks"
        for phrase in _THANKS_EXACT:
            if cleaned == phrase:
                return "thanks"

    # Help — word-boundary check for "help" to avoid "helpful"/"unhelpful"
    if _HELP_WORD_RE.search(cleaned):
        # Exclude frustration phrases that contain "help" as a word
        _help_negators = ["not help", "did not help", "does not help",
                          "will not help", "never help", "can not help",
                          "was not help", "has not help"]
        normalized = _normalize_contractions(cleaned)
        if not any(neg in cleaned for neg in _help_negators) and \
           not any(neg in normalized for neg in _help_negators):
            return "help"
    for phrase in _HELP_PHRASES:
        if phrase in cleaned:
            return "help"

    return None


# ---------------------------------------------------------------------------
# TONE CLASSIFICATION
# ---------------------------------------------------------------------------

def _classify_tone(text: str, crisis_result=_CRISIS_NOT_CHECKED) -> str | None:
    """Classify a message's emotional tone (how the user FEELS).

    No service-word gating — always runs. The caller decides how to
    combine tone with service intent.

    Args:
        text: The user's message.
        crisis_result: Pre-computed result from detect_crisis().
            Pass the return value directly (a tuple for crisis, None for
            no crisis). When omitted, _classify_tone calls detect_crisis
            itself. This avoids a redundant Sonnet LLM call when the
            caller has already checked.

    Returns one of: "crisis", "emotional", "frustrated", "confused",
    "urgent", or None.

    Priority order matters — crisis > frustrated > emotional > confused > urgent.
    Urgent is lowest because it's about speed, not emotion. When a stronger
    tone is present ("I'm scared and need shelter tonight"), empathy matters
    more than acknowledging urgency (which the urgency slot already captures).
    """
    lower = text.lower().strip()
    cleaned = re.sub(r"[^\w\s']", "", lower).strip()

    # Normalized form expands contractions so phrase lists only need
    # the "not" form (e.g., "not helpful") to match "isn't helpful",
    # "wasnt helpful", etc.
    normalized = _normalize_contractions(cleaned)

    # Stripped forms remove intensifiers so phrase lists don't need
    # every intensifier×emotion combination.
    stripped = _strip_intensifiers(cleaned)
    stripped_normalized = _strip_intensifiers(normalized)

    # Crisis — highest priority (uses original text, NOT normalized)
    if crisis_result is _CRISIS_NOT_CHECKED:
        crisis_result = detect_crisis(text)
    if crisis_result is not None:
        return "crisis"

    # Frustration — check all variants
    for phrase in _FRUSTRATION_PHRASES:
        if phrase in cleaned or phrase in normalized or phrase in stripped or phrase in stripped_normalized:
            return "frustrated"

    # Emotional — check all variants
    for phrase in _EMOTIONAL_PHRASES:
        if phrase in cleaned or phrase in normalized or phrase in stripped or phrase in stripped_normalized:
            return "emotional"

    # Confused — check all variants
    for phrase in _CONFUSED_PHRASES:
        if phrase in cleaned or phrase in normalized or phrase in stripped or phrase in stripped_normalized:
            return "confused"

    # Urgent — time pressure or panic without a stronger emotional tone.
    for phrase in _URGENT_PHRASES:
        if phrase in cleaned:
            return "urgent"

    return None


# ---------------------------------------------------------------------------
# COMBINED CLASSIFIER — TEST CONVENIENCE WRAPPER
# ---------------------------------------------------------------------------

def _classify_message(text: str) -> str:
    """Classify a message into a single routing category.

    This is a test-convenience wrapper that combines ``_classify_action``,
    ``detect_crisis``, ``_classify_tone``, and ``extract_slots`` into a
    single category string. Production routing in ``generate_reply()``
    uses the split functions directly for nuanced handling (e.g., service
    intent + emotional tone interleaved with confirmation state); this
    wrapper exists so unit tests of the underlying classifiers can
    exercise the combined behavior with one call.

    The leading underscore signals "internal — not part of the
    chatbot's public API". Tests are the only consumer.
    """
    from app.services.slot_extractor import extract_slots

    lower = text.lower().strip()
    cleaned = re.sub(r"[^\w\s']", "", lower).strip()

    # Crisis always wins
    crisis_result = detect_crisis(text)
    if crisis_result is not None:
        return "crisis"

    # Action intent
    action = _classify_action(text)
    if action:
        return action

    # Frustration (before slot check — "not helpful" is never a service request)
    normalized = _normalize_contractions(cleaned)
    stripped = _strip_intensifiers(cleaned)
    stripped_normalized = _strip_intensifiers(normalized)
    for phrase in _FRUSTRATION_PHRASES:
        if phrase in cleaned or phrase in normalized or phrase in stripped or phrase in stripped_normalized:
            return "frustration"

    # Check slots — if service intent found, it wins over emotional/confused
    extracted = extract_slots(text)
    # Only check meaningful slot fields — metadata fields like _contradiction
    # (always False), no_requirements (always False), and _populations (always [])
    # are never None and would make has_slot True for every message.
    _SLOT_FIELDS = {
        "service_type", "location", "urgency", "age", "family_status",
        "_gender", "org_name", "service_detail",
    }
    has_slot = any(v is not None for k, v in extracted.items()
                   if k in _SLOT_FIELDS)
    if has_slot:
        return "service"

    # Emotional / confused / urgent (only when no service intent)
    tone = _classify_tone(text)
    if tone == "emotional":
        return "emotional"
    if tone == "confused":
        return "confused"

    return "general"
