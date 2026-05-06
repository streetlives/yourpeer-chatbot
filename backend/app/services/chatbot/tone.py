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

# Patterns that NEGATE the substance-use disclosure trigger even when a
# keyword from _SUBSTANCE_USE_DISCLOSURE_PHRASES matched. Each fires
# against the lowercased message; any match suppresses the trigger.
#
# Four classes of false positive observed in the bug-hunt audit:
#
# 1. Long-term recovery (the user is in stable recovery, NOT actively
#    seeking detox): "I'm 5 years sober", "10 months clean."
# 2. Third-party requests (someone OTHER than the user is the person
#    with the issue): "my son is addicted", "for my daughter."
# 3. Professional / informational lookup (clinician or staff member
#    looking up an address, not seeking treatment): "address of Mt
#    Sinai detox", "where do I refer patients."
# 4. Non-substance addictions (the keyword "addiction" / "addicted"
#    matched, but the addiction is to a non-substance behavior):
#    "gambling addiction", "shopping addiction", "porn addiction."
#
# False negatives from these patterns are acceptable in either
# direction — a missed disclosure falls through to the existing
# emotional / shame / baseline-warmth paths, which are still
# trauma-informed. A false POSITIVE adds an unsolicited SAMHSA
# helpline + medical-supervision note that's incorrect for the
# user's situation, which is the harm the bug-hunt flagged.
_SUBSTANCE_USE_EXCLUSIONS = (
    # Long-term recovery: explicit duration of sobriety / cleanness
    re.compile(
        r"\b\d+\s*(?:year|yr|month|day|week)s?\s*"
        r"(?:sober|clean|in recovery|of recovery)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:sober|clean)\s*(?:for|since)\s*\d+\s*"
        r"(?:year|yr|month|day|week)",
        re.I,
    ),
    # Third-party: another person is the disclosed user
    re.compile(
        r"\bmy\s+(?:son|daughter|husband|wife|partner|boyfriend|"
        r"girlfriend|spouse|brother|sister|mom|dad|mother|father|"
        r"kid|child|nephew|niece|cousin|friend|family\s*member|"
        r"loved\s*one|relative)\b",
        re.I,
    ),
    re.compile(
        r"\bfor\s+(?:my\s+)?(?:son|daughter|client|patient|friend|"
        r"relative|partner|spouse|kid|child|loved\s*one|"
        r"family\s*member|someone)\b",
        re.I,
    ),
    re.compile(r"\b(?:he|she|they)\s+(?:is|are|has|have|needs?)\b", re.I),
    # Professional / informational lookup
    re.compile(
        r"\b(?:address|phone(?:\s*number)?|location|directions?|"
        r"hours?|contact|website)\s+(?:of|for|to)\b",
        re.I,
    ),
    re.compile(r"\b(?:how|where)\s+do\s+i\s+refer\b", re.I),
    re.compile(r"\bfor\s+(?:my\s+)?(?:patient|client)s?\b", re.I),
    re.compile(r"\b(?:patient|client)\s+(?:lookup|info|information)\b", re.I),
    # Non-substance addictions: the keyword "addict(ion|ed)" matched,
    # but the addiction is named as something other than a substance.
    re.compile(
        r"\b(?:gambling|shopping|porn|food|phone|screen|sex|"
        r"video\s*game|gaming|internet|social\s*media|spending|"
        r"work|exercise)\s+(?:addict|addiction|addicted|problem)\b",
        re.I,
    ),
    re.compile(
        r"\baddict(?:ion|ed)\s+to\s+(?:gambling|shopping|porn|food|"
        r"phone|screen|sex|video\s*game|gaming|internet|social\s*media|"
        r"spending|work|exercise)\b",
        re.I,
    ),
)

# Alcohol / opiate disclosure is a SUBSET of substance-use disclosure
# that warrants more specific safety language. Alcohol withdrawal can
# be life-threatening (delirium tremens, seizures); opiate withdrawal
# carries elevated overdose risk on relapse because tolerance drops
# during abstinence. The medical-supervision note in the addendum is
# correct and important for these substances. For users disclosing
# OTHER substances (cocaine, meth, marijuana, benzos) or non-specific
# addiction, the alcohol/opiate-specific text is mistuned — those
# cases get the generic safety addendum instead.
#
# This is a regex (not a phrase tuple) because we want word-boundary
# matching: "opiate" should match in "opiate addiction" but not as an
# accidental substring of some unrelated word.
_ALCOHOL_OPIATE_PATTERN = re.compile(
    r"\b("
    r"alcohol|alcoholic|alcoholism|drinking|"
    r"opiate|opiates|opioid|opioids|"
    r"heroin|fentanyl|methadone|suboxone|oxy(?:codone|contin)?|"
    r"vicodin|percocet|hydrocodone|morphine"
    r")\b",
    re.I,
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
        and not any(p.search(msg_lower) for p in _SUBSTANCE_USE_EXCLUSIONS)
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
        # emotional_context starts with "substance_use_disclosure".
        #
        # Subtype split: alcohol/opiate disclosures get medical-
        # supervision language because withdrawal from those is
        # specifically dangerous. Other substance disclosures get
        # generic safety language without the alcohol/opiate-specific
        # claims (see execution.py for the addendum text branch).
        prefix = (
            "Reaching out for help with this is a real step forward. "
            "Let me find what's available. "
        )
        if _ALCOHOL_OPIATE_PATTERN.search(msg_lower):
            emotional_context = "substance_use_disclosure_alcohol_opiate"
        else:
            emotional_context = "substance_use_disclosure_other"
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
