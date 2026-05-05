"""Tone-prefix computation for service-flow responses.

The single public entry point ``_compute_tone_prefix()`` determines what
opener (if any) to prepend to a service confirmation or follow-up based
on the user's detected emotional signals. Research-driven: see
``docs/CHATBOT_BEHAVIOR.md`` for the SAMHSA / Buber framing.
"""

import re

from app.services.responses import random_warmth_prefix
from app.utils.text_normalize import normalize_apostrophes


# Phrases indicating shame/vulnerability disclosure. When these co-occur with
# a service request, a normalizing prefix ("It takes real strength…") is
# prepended instead of a generic empathy line.
_SHAME_SIGNALS = (
    "embarrassed", "ashamed", "pathetic", "failure",
    "never thought i'd need", "never thought id need",
    "hard for me to say", "hard to say", "hard for me to ask",
    "hard to ask", "hard to admit",
    "difficult to ask", "difficult to say",
    "hate asking", "hate to ask", "hate having to ask",
    "humiliating", "degrading",
    "burden", "swallow my pride", "swallowed my pride",
    "first time asking", "never done this before",
    "never had to ask", "never asked for help",
    "can't believe i'm", "cant believe im",
    "can't afford to eat", "cant afford to eat",
    "can't even feed", "cant even feed",
    "don't want anyone to know", "dont want anyone to know",
)

# Medical urgency requires BOTH a depletion signal AND a medical keyword
# to fire, avoiding false positives on generic "ran out of" phrases.
_MEDICATION_DEPLETION = (
    "ran out of", "run out of", "running out of", "out of my",
    "don't have my", "dont have my", "lost my medication",
    "lost my medicine",
    "no more", "can't get my", "cant get my", "ran out of my",
)
_MEDICATION_WORDS = (
    "insulin", "medication", "medicine", "prescription",
    "inhaler", "epipen", "pills", "meds",
)

# Sensitive life-situation phrases that override casual tone (e.g. a user
# writing "just got out of jail, need a shower" should not get a cheerful
# baseline-warmth opener).
_SENSITIVE_CONTEXT_RE = re.compile(
    r"\b(foster care|aging out|aged out|fleeing|escaped|"
    r"just got out of jail|just got out of prison|domestic violence)\b", re.I,
)

# Substance-use disclosure: phrases users use to disclose alcohol or
# drug dependence. Triggers a warm acknowledgment + safety addendum
# when paired with a service-flow turn (the user is asking for detox /
# rehab / treatment, not in immediate crisis). The cluster_5 routing
# fix ensures these queries reach the substance-use treatment results;
# this layer adds the dignifying acknowledgment + medical-urgency note
# (alcohol withdrawal can be life-threatening; opiate withdrawal carries
# overdose risk on relapse — SAMHSA helpline 1-800-662-4357).
_SUBSTANCE_USE_DISCLOSURE_PHRASES = (
    "detox", "detoxification",
    "addiction", "addicted",
    "alcoholic", "alcoholism",
    "drinking too much", "drinking a lot", "been drinking",
    "struggle with drinking", "struggling with drinking",
    "using drugs", "use drugs", "using again",
    "opiate", "opiates", "opioid", "opioids",
    "heroin", "fentanyl",
    "dependent on", "depend on alcohol", "depend on drugs",
    "withdrawal",
    "get clean", "stay clean", "sober",
    "rehab", "recovery program",
    "substance abuse", "substance use",
)


def _compute_tone_prefix(
    message: str,
    response_tone: str | None,
    is_service_flow: bool,
    prior_emotional_context: str | None,
) -> tuple[str, str | None]:
    """Compute the opening phrase for a service-flow response based on tone.

    **Precedence (load-bearing):**

    1. Sensitive context (foster care, fleeing, just got out of jail) —
       overrides everything else with a specific acknowledgment. Must come
       last in the function so it can override an already-set prefix.
    2. Shame disclosure + service flow → normalizing prefix.
    3. Medical urgency (depletion + meds keyword) + service flow →
       "That sounds urgent — let me help you find care right away."
    4. Emotional / frustrated / confused / urgent tone + service flow →
       matching empathic prefix.
    5. Prior emotional context (set on a previous turn this session) →
       continuity prefix ("Still here with you.").
    6. Baseline warmth (routine service flow with no emotional signal) →
       randomly selected warm opener.

    Returns (prefix, emotional_context) where ``prefix`` is the string to
    prepend (empty string if none) and ``emotional_context`` is what the
    caller should save to ``merged["_emotional_context"]`` for use on the
    next turn (None = don't change).
    """
    # Normalize curly apostrophes (U+2019, etc.) from mobile autocorrect
    # before substring matching. Several entries in _SHAME_SIGNALS and
    # _MEDICATION_DEPLETION contain straight apostrophes; without this,
    # mobile users typing "I can't afford to eat" would silently miss the
    # shame normalization prefix.
    msg_lower = normalize_apostrophes(message.lower())
    is_shame = any(s in msg_lower for s in _SHAME_SIGNALS)
    is_medical_urgent = (
        is_service_flow
        and any(s in msg_lower for s in _MEDICATION_DEPLETION)
        and any(s in msg_lower for s in _MEDICATION_WORDS)
    )
    is_substance_use_disclosure = (
        is_service_flow
        and any(s in msg_lower for s in _SUBSTANCE_USE_DISCLOSURE_PHRASES)
    )

    prefix = ""
    emotional_context: str | None = None

    if is_shame and is_service_flow:
        prefix = (
            "It takes real strength to reach out — a lot of people use "
            "these services, and there's no shame in it. "
        )
        emotional_context = "shame"
    elif is_substance_use_disclosure:
        # Strengths-based acknowledgment for substance-use disclosure.
        # The downstream caller adds a safety addendum to the results
        # message (SAMHSA helpline + medical-supervision note) when
        # emotional_context == "substance_use_disclosure".
        prefix = (
            "Reaching out for help with this is a real step forward. "
            "Let me find what's available. "
        )
        emotional_context = "substance_use_disclosure"
    elif is_medical_urgent:
        prefix = "That sounds urgent — let me help you find care right away. "
        emotional_context = "medical_urgent"
    elif response_tone == "emotional" and is_service_flow:
        prefix = "I hear you, and I want to help. "
        emotional_context = "emotional"
    elif response_tone == "frustrated" and is_service_flow:
        prefix = "I understand this has been frustrating. Let me try something different. "
    elif response_tone == "confused" and is_service_flow:
        prefix = "No worries — let me help you with that. "
    elif response_tone == "urgent" and is_service_flow:
        prefix = "I can see this is urgent — let me find something right away. "

    # Continuity: prior emotional context from earlier in the session
    if not prefix and is_service_flow and prior_emotional_context:
        if prior_emotional_context == "shame":
            prefix = "Still here with you. "
        elif prior_emotional_context == "medical_urgent":
            prefix = "Let's get you to the right place. "
        else:
            prefix = "I'm still here with you. "

    # Baseline warmth: prevent "functional but flat" tone on routine turns
    if not prefix and is_service_flow:
        prefix = random_warmth_prefix()

    # Sensitive context OVERRIDES everything — applied last
    if _SENSITIVE_CONTEXT_RE.search(message):
        prefix = "I understand this is a difficult situation. Let me help. "
        emotional_context = "sensitive"

    return prefix, emotional_context
