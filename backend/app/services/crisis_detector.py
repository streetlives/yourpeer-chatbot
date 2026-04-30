"""
Crisis Detection Module

Detects crisis language in user messages and returns the appropriate
category and response. This runs BEFORE all other message classification
in the chatbot — a message containing crisis language always gets crisis
resources, never a slot-filling follow-up.

Categories (from the architecture spec §5.3):
    1. Suicide / self-harm
    2. Violence / harm to others
    3. Domestic violence / abuse
    4. Exploitation / trafficking
    5. Medical emergency
    6. General safety concern (unsafe at home, runaway, fleeing)

Detection strategy — two-stage:

    Stage 1: Regex pre-check (< 1ms, deterministic, auditable)
        Catches the most common explicit phrasings. If it fires,
        return immediately — no LLM call needed.

    Stage 2: LLM classification (1-3s, only runs when regex misses)
        Claude classifies the message against all six categories.
        Catches indirect, paraphrased, and culturally specific language
        that can't be enumerated in a keyword list.

        Fail-open policy: if the LLM call fails (timeout, API error,
        quota exceeded), the system returns a general safety response
        rather than falling through to slot-filling. This is intentional:
        the LLM is only invoked when a message was ambiguous enough that
        regex couldn't decide — that uncertainty itself is reason to
        err toward safety for this population.

Design decisions:
    - Regex runs unconditionally first — it's free and fast.
    - LLM is only called when regex returns None.
    - LLM uses a structured JSON response (not tool calling) to minimize
      tokens and latency. Max 150 tokens is sufficient for yes/no + category.
    - All crisis responses are warm, non-judgmental, and action-oriented.
    - Each category has its own response with category-specific resources.
"""

import os
import json
import re
import logging
import time
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# LLM CLIENT — uses shared Anthropic client from claude_client.py
# ---------------------------------------------------------------------------

from app.llm.claude_client import get_client, CRISIS_DETECTION_MODEL, _track_llm_call

# Whether LLM crisis detection is enabled (requires ANTHROPIC_API_KEY)
_USE_LLM_DETECTION = bool(os.getenv("ANTHROPIC_API_KEY"))

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CRISIS KEYWORD PATTERNS
# ---------------------------------------------------------------------------
# Each category has a list of phrases. Matching is case-insensitive and
# checks for substring containment. Phrases are ordered roughly by
# specificity — longer phrases first to avoid false positives.

_SUICIDE_SELF_HARM_PHRASES = [
    # Direct statements
    "kill myself", "want to die", "wanna die", "wish i was dead",
    "wish i were dead", "better off dead", "end my life",
    "end it all", "take my life", "don't want to live",
    "dont want to live", "no reason to live", "nothing to live for",
    "can't go on", "cant go on", "not worth living",
    # C-SSRS Level 1 — wish to be dead / not alive (P0 audit)
    "wish i was never born", "wish i wasn't alive", "wish i wasnt alive",
    "don't want to wake up", "dont want to wake up",
    "go to sleep and never wake up", "fall asleep and never wake up",
    # C-SSRS Level 2 — non-specific active thoughts (P0 audit)
    "thinking about ending it", "thinking about killing myself",
    "thoughts of suicide", "thinking about suicide",
    # C-SSRS Level 3 — method without plan (P0 audit)
    "thought about pills", "thought about jumping",
    # Self-harm
    "hurt myself", "cutting myself", "harming myself", "self harm",
    "self-harm", "hurting myself",
    # Suicidal ideation
    "suicidal", "suicide", "overdose", "jump off",
    "hang myself", "slit my",
    # Passive / indirect hopelessness (P8)
    # These phrases don't mention death explicitly but signal suicidal ideation.
    # Erring on the side of false positives is correct here — showing crisis
    # resources to someone feeling hopeless does no harm.
    "what's the point anymore", "whats the point anymore",
    "what is the point anymore", "no point anymore",
    "nothing helps anymore", "nothing ever helps",
    "i give up", "given up on everything", "given up on life",
    "i can't do this anymore", "i cant do this anymore",
    "there's no hope", "theres no hope", "no hope left",
    "nobody cares if i", "no one cares if i",
    "would be better without me", "better off without me",
    "i'm done fighting", "im done fighting",
    "can't keep going", "cant keep going",
    "so tired of living", "tired of living like this",
    # Joiner IPT — perceived burdensomeness (P0 audit)
    # Strong predictor of suicidal desire per Joiner (2005)
    "i'm a burden", "im a burden", "i'm just a burden",
    "everyone would be fine without me",
    "the world would be better without me",
    # Indirect pain/threshold phrases (P0 audit)
    "i can't take it anymore", "i cant take it anymore",
    "just want the pain to stop", "want the pain to stop",
    "life isn't worth it", "life isnt worth it",
    "no point in going on",
    "make it all stop", "wish it would all stop",
    "im done with everything",
    # Indirect ideation — passive desire to not exist (regex audit)
    "don't want to be here anymore", "dont want to be here anymore",
    "i don't want to be here", "i dont want to be here",
    "nobody would miss me", "no one would miss me",
    "want to disappear", "i just want to disappear",
    "life isn't worth living", "life isnt worth living",
    "wouldn't matter if i was gone", "wouldnt matter if i was gone",
]

_VIOLENCE_PHRASES = [
    # Threats to others
    "kill someone", "hurt someone", "want to hurt",
    "going to hurt", "gonna hurt",
    "kill him", "kill her", "kill them",
    "hurt him", "hurt her", "hurt them",
    # Weapons
    "have a gun", "have a knife", "got a weapon",
    "going to shoot", "gonna shoot",
]

_DOMESTIC_VIOLENCE_PHRASES = [
    # Abuse descriptions
    "being abused", "he hits me", "she hits me", "they hit me",
    "partner hits me", "husband hits me", "wife hits me",
    "boyfriend hits me", "girlfriend hits me",
    "being beaten", "beats me", "abusive relationship",
    "abusive partner", "domestic violence", "domestic abuse",
    "afraid of my partner", "scared of my partner",
    "afraid to go home", "scared to go home",
    "he hurts me", "she hurts me", "they hurt me",
    "partner hurts me", "violent partner",
    "stalking me", "being stalked", "restraining order",
    "order of protection",
    # Fleeing / implicit DV
    "he's going to come back", "she's going to come back",
    "going to come back", "coming back soon",
    "he's going to hurt me", "she's going to hurt me",
    "going to hurt me",
    "threatened to hurt me", "threatened to kill me",
    "threatens to hurt me", "threatens to kill me",
    "he threatened me", "she threatened me",
    "said he would hurt me", "said she would hurt me",
    "said he'd hurt me", "said she'd hurt me",
    "said he would kill me", "said she would kill me",
    "need to leave before", "have to leave before",
    "kicked me out", "threw me out", "locked me out",
    # Partner control / coercive control (P1 audit)
    "he controls everything", "she controls everything",
    "controls my money", "takes my money",
    "won't let me leave the house", "wont let me leave the house",
    # Physical violence — strangulation / choking (regex audit)
    "chokes me", "choked me", "chokes me out",
    "strangles me", "strangled me", "strangling me",
    "grabs me by the neck", "grabs my neck",
    "put his hands on me", "puts his hands on me",
    "put her hands on me", "puts her hands on me",
    # Post-crisis / escaped DV (peer navigator scenarios)
    "escaped abuse", "escaped my abuser",
    "fleeing abuse", "fled abuse", "fled my abuser",
    "left my abuser", "ran from my abuser",
    "got away from my abuser",
]

# General safety concerns — not clearly DV or suicidal, but the person
# feels unsafe and needs immediate resources. Errs on the side of providing
# crisis resources rather than routine slot-filling.
_SAFETY_CONCERN_PHRASES = [
    "don't feel safe", "dont feel safe", "not safe here",
    "not safe where i am", "i'm not safe", "im not safe",
    "need to get out", "need to leave now", "have to get out",
    "can't stay here", "cant stay here",
    "in danger", "i'm in danger", "im in danger",
    "someone is threatening me", "being threatened",
    "someone is following me", "being followed",
    "need to get away",
    "afraid for my life", "fear for my life",
    "they're going to find me", "going to find me",
    "no safe place to go", "nowhere safe",
    # Generic kicked-out (could be anyone, not just youth)
    "kicked out of my home", "thrown out of my home", "kicked out at",
    # Fleeing without DV language (P1 audit)
    "hiding from someone",
    "someone looking for me", "someone is looking for me",
    "he's looking for me", "she's looking for me",
    "had to leave home fast", "left home suddenly",
]

# Youth runaway — separated from general safety concerns because
# runaway youth need DIFFERENT resources (Runaway Safeline, Covenant House)
# not DV hotlines.
_YOUTH_RUNAWAY_PHRASES = [
    "ran away from home", "run away from home", "running away from home",
    "ran away last night", "ran away yesterday",
    "i'm a runaway", "im a runaway", "i am a runaway",
    "kicked out by my parents", "parents kicked me out",
    "family kicked me out", "thrown out by my family",
    "unsafe at home", "not safe at home", "home isn't safe", "home is not safe",
    "can't go home", "cant go home", "not safe to go home",
    "afraid to go home",
    # Youth/family violence (P1 audit)
    "my parents hurt me", "my family hurts me",
    "being hit at home",
]

# Assault / victim of violence — distinct from DV (partner violence) and
# from the "violence" category (threats TO hurt others). These are people
# who have BEEN hurt and need medical + victim services.
_ASSAULT_VICTIM_PHRASES = [
    "got beat up", "just got beat up", "got beaten up",
    "i was attacked", "i was assaulted", "i got assaulted",
    "someone attacked me", "someone beat me up",
    "was jumped", "got jumped", "i got jumped",
    "i was mugged", "got mugged", "got robbed and hurt",
    "was stabbed", "got stabbed", "got shot",
    "someone hurt me", "i was hurt by someone",
]

_TRAFFICKING_PHRASES = [
    # Labor trafficking
    "forced to work", "can't leave my job", "cant leave my job",
    "won't let me leave", "wont let me leave",
    "they took my passport", "took my documents",
    "locked in", "being held", "held against my will",
    "trapped", "can't escape", "cant escape",
    # Sex trafficking
    "forced into prostitution", "forced me into prostitution",
    "being trafficked", "being sex trafficked",
    "sex trafficking", "human trafficking",
    "making me do things", "pimped out",
    "they control me", "they won't let me go",
    "wont let me go", "prostitution",
]

_MEDICAL_EMERGENCY_PHRASES = [
    # Immediate danger
    "having a heart attack", "can't breathe", "cant breathe",
    "chest pain", "seizure", "stroke",
    "someone is dying", "bleeding out", "blood everywhere",
    "overdosing", "od'ing", "oding",
    "unconscious", "not breathing", "stopped breathing",
    "choking", "allergic reaction", "anaphylaxis",
    # Overdose / poisoning (regex audit)
    "took too many pills", "swallowed too many pills",
    "took a bunch of pills", "took all my pills",
    # Bleeding / injury (regex audit)
    "can't stop bleeding", "cant stop bleeding",
    "bleeding really bad", "bleeding a lot",
    # Explicit emergency (regex audit)
    "medical emergency", "having an emergency",
    "need an ambulance", "call an ambulance",
    "call 911",
]


# ---------------------------------------------------------------------------
# CRISIS RESPONSES
# ---------------------------------------------------------------------------
# Each response is warm, non-judgmental, and leads with the most
# relevant resource for that crisis type.

_SUICIDE_RESPONSE = (
    "I hear you, and I'm glad you reached out. What you're feeling matters, "
    "and there are people who want to help right now.\n\n"
    "Please reach out to one of these free, confidential resources:\n"
    "• 988 Suicide & Crisis Lifeline — call or text 988 (24/7)\n"
    "• Crisis Text Line — text HOME to 741741\n"
    "• Trevor Project (LGBTQ+ youth) — call 1-866-488-7386 or text START to 678-678\n\n"
    "You can also ask me to connect you with a peer navigator. "
    "You don't have to go through this alone."
)

_VIOLENCE_RESPONSE = (
    "If you or someone else is in immediate danger, please call 911.\n\n"
    "If you'd like to talk to someone:\n"
    "• 988 Suicide & Crisis Lifeline — call or text 988 (24/7)\n"
    "• Crisis Text Line — text HOME to 741741\n\n"
    "I can also connect you with a peer navigator who can help "
    "you find support services."
)

_DOMESTIC_VIOLENCE_RESPONSE = (
    "I'm sorry you're going through this. You deserve to be safe, "
    "and there is help available. I can connect you with a peer "
    "navigator if you would like.\n\n"
    "• National Domestic Violence Hotline — 1-800-799-7233 (24/7) "
    "or text START to 88788\n"
    "• NYC Domestic Violence Hotline — 1-800-621-4673 (24/7, multilingual)\n"
    "• Safe Horizon — 1-800-621-HOPE (4673)\n\n"
    "If you're in immediate danger, please call 911."
)

_TRAFFICKING_RESPONSE = (
    "What you're describing sounds very serious, and help is available. "
    "You are not in trouble.\n\n"
    "• National Human Trafficking Hotline — 1-888-373-7888 "
    "or text HELP to 233733 (BeFree)\n"
    "• If you're in immediate danger, call 911\n\n"
    "These services are confidential and available 24/7. "
    "I can also connect you with a peer navigator."
)

_MEDICAL_EMERGENCY_RESPONSE = (
    "If this is a medical emergency, please call 911 immediately.\n\n"
    "• Emergency: 911\n"
    "• Poison Control: 1-800-222-1222\n"
    "• NYC Health + Hospitals: call 311 for non-emergency medical help\n\n"
    "Once you're safe, I can help you find nearby clinics or health services."
)

_SAFETY_CONCERN_RESPONSE = (
    "Your safety comes first. If you're in immediate danger, please call 911.\n\n"
    "Here are resources that can help right now:\n"
    "• 988 Suicide & Crisis Lifeline — call or text 988 (24/7)\n"
    "• Crisis Text Line — text HOME to 741741\n"
    "• NYC 311 — call 311 for shelter intake and social services\n\n"
    "If you need shelter right away, I can help you find somewhere safe nearby. "
)

_YOUTH_RUNAWAY_RESPONSE = (
    "I hear you, and I want to help you stay safe. You're not alone in this.\n\n"
    "Here are people who can help right now — they won't judge you:\n"
    "• National Runaway Safeline — 1-800-786-2929 (24/7, confidential) "
    "or chat at 1800runaway.org\n"
    "• Covenant House (NYC) — 1-800-388-3888 (shelter + support for youth)\n"
    "• Crisis Text Line — text HOME to 741741\n"
    "• If you're in immediate danger, call 911\n\n"
    "I can also help you find shelter or a drop-in center nearby."
)

_ASSAULT_VICTIM_RESPONSE = (
    "I'm sorry that happened to you. Your safety matters, and help is available.\n\n"
    "If you need medical attention right now, please call 911.\n\n"
    "Other resources:\n"
    "• Safe Horizon Victim Services — 1-800-621-HOPE (4673) (24/7)\n"
    "• NYC Health + Hospitals — call 311 for non-emergency medical help\n"
    "• 988 Suicide & Crisis Lifeline — call or text 988\n\n"
    "I can also help you find medical care or other services nearby."
)


# ---------------------------------------------------------------------------
# DETECTION
# ---------------------------------------------------------------------------

# Map category → (phrases, response)
# Order matters: more specific categories checked first.
_CRISIS_CATEGORIES = [
    ("suicide_self_harm", _SUICIDE_SELF_HARM_PHRASES, _SUICIDE_RESPONSE),
    ("medical_emergency", _MEDICAL_EMERGENCY_PHRASES, _MEDICAL_EMERGENCY_RESPONSE),
    ("domestic_violence", _DOMESTIC_VIOLENCE_PHRASES, _DOMESTIC_VIOLENCE_RESPONSE),
    ("youth_runaway", _YOUTH_RUNAWAY_PHRASES, _YOUTH_RUNAWAY_RESPONSE),
    ("assault_victim", _ASSAULT_VICTIM_PHRASES, _ASSAULT_VICTIM_RESPONSE),
    ("safety_concern", _SAFETY_CONCERN_PHRASES, _SAFETY_CONCERN_RESPONSE),
    ("trafficking", _TRAFFICKING_PHRASES, _TRAFFICKING_RESPONSE),
    ("violence", _VIOLENCE_PHRASES, _VIOLENCE_RESPONSE),
]

# Map LLM category name → response text
_LLM_CATEGORY_RESPONSES = {
    "suicide_self_harm": _SUICIDE_RESPONSE,
    "domestic_violence": _DOMESTIC_VIOLENCE_RESPONSE,
    "youth_runaway": _YOUTH_RUNAWAY_RESPONSE,
    "assault_victim": _ASSAULT_VICTIM_RESPONSE,
    "safety_concern": _SAFETY_CONCERN_RESPONSE,
    "trafficking": _TRAFFICKING_RESPONSE,
    "medical_emergency": _MEDICAL_EMERGENCY_RESPONSE,
    "violence": _VIOLENCE_RESPONSE,
}

# Used as the fail-open response when the LLM is invoked but unavailable.
# General enough to be appropriate for any crisis type.
_FAILOPEN_RESPONSE = _SAFETY_CONCERN_RESPONSE

_LLM_SYSTEM_PROMPT = """\
You are a crisis detection classifier for a social services chatbot serving \
vulnerable populations in New York City, including people experiencing \
homelessness, domestic violence, mental health crises, and trafficking.

Your only job is to determine whether a user message indicates a crisis \
situation requiring immediate safety resources.

Crisis categories:
- suicide_self_harm: suicidal ideation (direct or indirect), self-harm, \
hopelessness, passive ideation ("what's the point", "nothing matters anymore")
- domestic_violence: abuse by a partner or family member, threats, fleeing \
a dangerous home situation
- youth_runaway: a young person who ran away, was kicked out by family, \
is unsafe at home, or can't go home. NOT the same as domestic violence.
- assault_victim: someone who was physically attacked, beaten up, jumped, \
mugged, or assaulted. They are the VICTIM, not the attacker.
- safety_concern: feeling unsafe, being followed, in danger — general \
safety concerns that don't fit the more specific categories above
- trafficking: being controlled, unable to leave, documents taken
- medical_emergency: an immediate physical danger requiring 911 — heart \
attack, stroke, choking, severe injury, overdose, unconsciousness. \
NOT chronic medication needs ("ran out of insulin", "need a refill", \
"out of my prescription") unless paired with active emergency symptoms \
(collapsed, can't breathe, severe pain, confusion). Medication shortage \
without acute symptoms is a routine medical service request, not a crisis.
- violence: threats to harm others (the person is a potential aggressor)

Respond with ONLY a JSON object, no other text:
{"crisis": true, "category": "<category_name>"}
or
{"crisis": false}

Be sensitive to indirect language. Someone saying "I've been on the streets \
for months and nothing helps anymore" may be expressing passive suicidal \
ideation even without explicit language. When in doubt, err toward crisis=true.\
"""


def _detect_crisis_llm(text: str) -> Optional[Tuple[str, str]]:
    """
    Use Claude to classify a message that regex didn't catch.

    Returns (category, response_text) if crisis detected, None otherwise.

    Fail-open: if the LLM call fails for any reason, returns the general
    safety concern response rather than None. This is intentional — the
    LLM is only invoked for messages ambiguous enough that regex couldn't
    decide, so we resolve that uncertainty toward safety.
    """
    try:
        _track_llm_call("crisis_detection")
        client = get_client()

        t0 = time.perf_counter()
        response = client.messages.create(
            model=CRISIS_DETECTION_MODEL,  # Sonnet 4.6 — nuance matters for safety
            max_tokens=60,                 # {"crisis": true, "category": "..."} is ~15 tokens
            system=_LLM_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
        latency = round((time.perf_counter() - t0) * 1000)

        from app.services.audit_log import record_llm_call
        record_llm_call(
            task="crisis_detection", model=CRISIS_DETECTION_MODEL,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency, success=True,
        )

        raw = response.content[0].text.strip()

        # Strip markdown code fences if present
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()

        parsed = json.loads(raw)

        if not parsed.get("crisis"):
            logger.info("LLM crisis check: no crisis detected")
            return None

        category = parsed.get("category", "safety_concern")
        response_text = _LLM_CATEGORY_RESPONSES.get(category, _FAILOPEN_RESPONSE)

        logger.warning(
            f"LLM crisis detected: category='{category}' "
            f"for message: '{text[:80]}...'"
        )
        return (category, response_text)

    except Exception as e:
        # Fail-open: LLM unavailable or returned unparseable output.
        # We were already in the ambiguous path (regex missed) — resolve
        # toward safety rather than falling through to slot-filling.
        logger.error(
            f"LLM crisis detection failed ({type(e).__name__}: {e}) — "
            f"failing open with safety_concern response"
        )
        return ("safety_concern", _FAILOPEN_RESPONSE)


def detect_crisis(text: str, skip_llm: bool = False) -> Optional[Tuple[str, str]]:
    """
    Check if a message contains crisis language. Two-stage detection:

    Stage 1 — Regex (< 1ms, always runs):
        Catches common explicit phrasings. Returns immediately on match.

    Stage 2 — LLM (1-3s, only when regex misses):
        Catches indirect, paraphrased, and culturally specific language.
        Fails open: if the LLM is unavailable, returns a general safety
        response rather than None, because the LLM is only invoked for
        ambiguous messages where uncertainty should resolve toward safety.

    Returns:
        (crisis_category, response_text) if crisis detected
        None if no crisis detected

    Categories: "suicide_self_harm", "medical_emergency",
                "domestic_violence", "safety_concern", "trafficking", "violence"
    """
    lower = text.lower()

    # --- Stage 1: Regex pre-check ---
    for category, phrases, response in _CRISIS_CATEGORIES:
        for phrase in phrases:
            if phrase in lower:
                logger.warning(
                    f"Crisis detected: category='{category}', "
                    f"matched phrase='{phrase}'"
                )
                return (category, response)

    # --- Stage 2: LLM classification (only if regex missed) ---
    # Guard: skip LLM for messages that match known sub-crisis emotional
    # phrases. The LLM prompt says "when in doubt, err toward crisis=true"
    # which is correct for genuinely ambiguous safety situations, but over-
    # escalates clearly emotional-but-not-crisis messages like "I'm feeling
    # scared" or "I'm struggling". These are handled by the emotional tone
    # handler in chatbot.py, which offers peer navigator support.
    _SUB_CRISIS_EMOTIONAL = [
        "feeling down", "feeling really down", "feeling sad",
        "feeling bad", "feeling depressed", "so depressed",
        "feeling scared", "feeling really scared", "im scared", "i'm scared",
        "feeling anxious", "so anxious", "feeling lonely", "so lonely",
        "feeling hopeless", "feeling lost", "feeling stuck",
        "not doing well", "not doing good", "not doing ok",
        "im not okay", "i'm not okay", "i'm not ok", "im not ok",
        "having a hard time", "having a rough time", "having a tough time",
        "rough day", "bad day", "tough day", "hard day",
        "stressed out", "so stressed", "really stressed",
        "i'm struggling", "im struggling",
        "tired of everything", "exhausted",
        "nobody cares", "no one cares",
    ]
    if any(phrase in lower for phrase in _SUB_CRISIS_EMOTIONAL):
        logger.info(
            "Skipping LLM crisis check — message matches sub-crisis "
            "emotional phrase (handled by emotional tone handler)"
        )
        return None

    if _USE_LLM_DETECTION and not skip_llm:
        return _detect_crisis_llm(text)

    if skip_llm:
        logger.debug(
            "Skipping LLM crisis check — message classified as safe short action"
        )

    return None


def is_crisis(text: str) -> bool:
    """Quick check: does this message contain crisis language?"""
    return detect_crisis(text) is not None
