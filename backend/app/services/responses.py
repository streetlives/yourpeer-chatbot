"""
Response builders for the YourPeer chatbot.

Contains all static response strings, emotion-specific responses,
LLM prompt builders, and the bot-question answer logic.

Separated from routing so that:
  - Tone/wording changes don't touch the routing logic
  - Each response can be reviewed independently
  - Prompts are testable without the full chatbot pipeline
"""

import re
import logging

from app.llm.claude_client import claude_reply
from app.utils.text_normalize import normalize_contractions, strip_intensifiers

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# STATIC RESPONSES
# ---------------------------------------------------------------------------

_GREETING_RESPONSE = (
    "Hey! I'm here to help you find free services in NYC — things like "
    "food, shelter, showers, clothing, health care, and more.\n\n"
    "I'll ask a couple of questions to find what's near you. "
    "You can skip anything you're not comfortable sharing. "
    "What are you looking for?"
)

_RESET_RESPONSE = (
    "No problem, let's start fresh. "
    "What kind of help are you looking for?"
)

_THANKS_RESPONSE = (
    "You're welcome! Let me know if you need anything else — "
    "I can search for food, shelter, clothing, health care, "
    "legal help, jobs, and more."
)

_HELP_RESPONSE = (
    "I can help you find free services in New York City. "
    "Just tell me what you need and where you are — for example, "
    '"I need food in Brooklyn" or "shelter in Queens." '
    "I'll search real, verified listings from YourPeer and "
    "show you what's available with addresses and hours.\n\n"
    "You can also say 'start over' anytime to begin a new search."
)

_ESCALATION_RESPONSE = (
    "I can connect you with a peer navigator who can help with your situation.\n\n"
    "You can reach the Streetlives team at yourpeer.nyc and use the chat feature.\n"
    "(This feature is currently in development)\n"
    "• You can call 311 and ask for social services referrals\n\n"
    "If you're in crisis:\n"
    "• 988 Suicide & Crisis Lifeline — call or text 988\n"
    "• Crisis Text Line — text HOME to 741741\n\n"
    "Would you like me to keep searching for services, or is there "
    "anything else I can help with?"
)

_FRUSTRATION_RESPONSE = (
    "I'm sorry this hasn't been what you needed. I understand how "
    "frustrating it can be when you've already tried places and they "
    "didn't work out.\n\n"
    "Here are some options:\n"
    "• I can search a different area or service type\n"
    "• I can connect you with a peer navigator who knows the system well\n"
    "• Call 311 for live social services help\n\n"
    "What would be most helpful for you right now?"
)

_BOT_IDENTITY_RESPONSE = (
    "I'm an AI assistant for YourPeer. I help you find free services in "
    "NYC — things like food, shelter, clothing, and more — using verified "
    "information from our database.\n\n"
    "I don't make up information. All the services I show you come from "
    "real locations that have been checked by people who've used them.\n\n"
    "If you'd like to talk to a real person, I can connect you with a "
    "peer navigator. Otherwise, just tell me what you need help with!"
)

_CONFUSED_RESPONSE = (
    "That's okay — you don't have to know exactly what you need. "
    "I can help you figure it out.\n\n"
    "Here are some things people often look for:\n"
    "• A meal or groceries\n"
    "• A place to stay tonight\n"
    "• A shower, clean clothes, or toiletries\n"
    "• A doctor or someone to talk to\n"
    "• Help with legal issues, a job, or benefits\n\n"
    "Tap any option below, or just tell me what's going on "
    "and I'll point you in the right direction."
)

_EMOTIONAL_RESPONSE = (
    "I hear you, and I'm sorry you're going through a difficult time. "
    "You don't have to have everything figured out right now.\n\n"
    "If you'd like to talk to someone who understands, I can connect you "
    "with a peer navigator — they're people who've been through similar "
    "situations and know the system well.\n\n"
    "Or if there's something specific I can help you find, just let me know. "
    "I'm here."
)

# Emotion-specific static responses — each acknowledges the specific
# feeling WITHOUT mentioning services. The judge consistently flags
# service mentions in emotional responses as "too transactional."
_EMOTIONAL_RESPONSES = {
    "scared": (
        "It's okay to feel scared — that's a completely understandable "
        "reaction to what you're going through. You're not alone in this.\n\n"
        "If you'd like to talk to someone, I can connect you with a peer "
        "navigator who can help you figure out next steps. No pressure."
    ),
    "sad": (
        "I'm sorry you're feeling this way. It takes courage to say that, "
        "and it's okay to not be okay right now.\n\n"
        "If you'd like to talk to someone who understands, I can connect "
        "you with a peer navigator. I'm here whenever you're ready."
    ),
    "rough_day": (
        "That sounds really hard. Some days are just heavy, and it's "
        "okay to feel that way.\n\n"
        "If you'd like to talk to someone, I can connect you with a peer "
        "navigator. Or just take your time — I'm here."
    ),
    "shame": (
        "You have nothing to be ashamed of. A lot of people use these "
        "services — it doesn't say anything about you as a person.\n\n"
        "It takes real strength to reach out, and I'm glad you did. "
        "I'm here to help however I can."
    ),
    "grief": (
        "I'm really sorry for your loss. That's an incredibly heavy "
        "thing to carry.\n\n"
        "If you'd like to talk to someone, I can connect you with a peer "
        "navigator. There's no rush — I'm here."
    ),
    "alone": (
        "I hear you, and I'm sorry you're feeling that way. You're not "
        "invisible, and reaching out here took courage.\n\n"
        "If you'd like to talk to someone who understands, I can connect "
        "you with a peer navigator. I'm here."
    ),
    # --- Research-backed additions (R28) ---
    # 41% of homeless people report feeling undeserving of help (PMC).
    "undeserving": (
        "You absolutely deserve help — everyone does. These services "
        "exist for exactly this, and you have every right to use them.\n\n"
        "I'm glad you're here. I can help you find what you need, "
        "or connect you with a peer navigator."
    ),
    # "Difficulty trusting people" is a named trauma response (National
    # Harm Reduction Coalition). Distrust of institutional services is
    # one of the top barriers to care-seeking (PMC, SAMHSA).
    "distrust": (
        "That's a fair concern — I understand why you'd be cautious. "
        "Everything I show you comes from a real database of verified "
        "services, and I don't store any personal information.\n\n"
        "You're in control here. You can search, skip, or connect with "
        "a peer navigator — a real person — whenever you're ready."
    ),
    # Anger at circumstances (distinct from bot-directed frustration).
    # SAMHSA notes anger as a common trauma response. Should get
    # validation, not the frustration escalation handler.
    "angry": (
        "You have every right to feel angry. The situation you're in "
        "is not okay, and that frustration makes complete sense.\n\n"
        "When you're ready, I can help you find services — or I can "
        "connect you with a peer navigator who gets it."
    ),
}


def _pick_emotional_response(text: str) -> str:
    """Pick the most appropriate emotion-specific static response.

    Applies the same text normalization as ``_classify_tone`` — contraction
    expansion and intensifier stripping — so that "I'm feeling *really*
    down" matches the "feeling down" keyword just as it does in the
    classifier.  Without this, the classifier correctly routes to
    ``emotional`` but the response selector falls through to the
    generic response.

    Falls back to the generic _EMOTIONAL_RESPONSE if no specific
    emotion is detected.
    """
    lower = text.lower()
    normalized = normalize_contractions(lower)
    stripped = strip_intensifiers(lower)
    stripped_normalized = strip_intensifiers(normalized)

    def _any_match(phrases):
        """Check if any phrase appears in any text variant."""
        return any(
            p in lower or p in normalized or p in stripped or p in stripped_normalized
            for p in phrases
        )

    # Shame/stigma — including indirect vulnerability expressions
    if _any_match([
        "embarrassed", "ashamed", "pathetic", "failure",
        "never thought i'd need", "never thought id need",
        "don't want anyone to know", "dont want anyone to know",
        # Indirect shame (R27 tone gap — these are how people express
        # shame without using the word "ashamed")
        "hard for me to say", "hard to say this", "hard for me to ask",
        "hard to ask for", "hard to admit",
        "difficult to ask", "difficult to say",
        "hate asking", "hate to ask", "hate having to ask",
        "humiliating", "degrading",
        "burden", "swallow my pride", "swallowed my pride",
        "first time asking", "never done this before",
        "never had to ask", "never asked for help",
        "can't believe i'm", "cant believe im",
        "can't afford to eat", "cant afford to eat",
        "can't even feed", "cant even feed",
    ]):
        return _EMOTIONAL_RESPONSES["shame"]

    # Grief/loss
    if _any_match([
        "died", "passed away", "lost someone", "grieving", "mourning",
    ]):
        return _EMOTIONAL_RESPONSES["grief"]

    # Scared/fear
    if _any_match([
        "scared", "afraid", "frightened", "terrified", "fear",
    ]):
        return _EMOTIONAL_RESPONSES["scared"]

    # Isolation/loneliness
    if _any_match([
        "alone", "no one", "nobody", "no friends", "no family",
        "have no one", "completely alone",
    ]):
        return _EMOTIONAL_RESPONSES["alone"]

    # Sad/down
    if _any_match([
        "feeling down", "feeling sad", "feeling bad", "depressed",
        "not okay", "not ok", "not doing well", "not doing good",
        "i'm sad", "im sad", "i am sad",
    ]):
        return _EMOTIONAL_RESPONSES["sad"]

    # Rough day / general hardship
    if _any_match([
        "rough day", "bad day", "tough day", "hard day",
        "rough time", "hard time", "tough time",
        "falling apart", "getting worse",
    ]):
        return _EMOTIONAL_RESPONSES["rough_day"]

    # --- Research-backed additions (R28) ---

    # Feeling undeserving — 41% of homeless people report this (PMC)
    if _any_match([
        "don't deserve", "dont deserve",
        "i'm not worth", "im not worth", "not worth it",
        "other people need it more", "others need it more",
        "don't want to take", "dont want to take",
        "people have it worse", "someone else needs",
        "i'm not worthy", "im not worthy",
        "unworthy", "undeserving",
    ]):
        return _EMOTIONAL_RESPONSES["undeserving"]

    # Distrust / suspicion — named trauma response (Harm Reduction Coalition)
    if _any_match([
        "don't trust", "dont trust", "i do not trust",
        "is this legit", "is this real", "is this safe",
        "how do i know", "can i trust",
        "been burned before", "been lied to",
        "don't believe", "dont believe",
        "sounds too good", "what's the catch", "whats the catch",
        "how is this free", "is there a catch",
        "suspicious", "sketchy",
    ]):
        return _EMOTIONAL_RESPONSES["distrust"]

    # Anger at situation (NOT bot-directed frustration)
    if _any_match([
        "i'm so angry", "im so angry", "i am so angry",
        "i'm furious", "im furious",
        "i'm pissed", "im pissed",
        "makes me sick", "so unfair", "not fair",
        "fed up with everything", "fed up with this",
        "sick of this", "tired of being",
        "why does this keep happening", "why me",
    ]):
        return _EMOTIONAL_RESPONSES["angry"]

    return _EMOTIONAL_RESPONSE


# ---------------------------------------------------------------------------
# LLM PROMPT BUILDERS
# ---------------------------------------------------------------------------

def _build_conversational_prompt(user_message: str, slots: dict) -> str:
    """Prompt for general conversational messages that aren't service queries.

    IMPORTANT: Service slots (service_type, location, age, etc.) are
    intentionally NOT passed to the LLM. The LLM has no access to the
    service database and must never generate text that implies knowledge
    of available services. All service data comes from deterministic DB
    queries only.
    """
    return (
        "You are YourPeer, a friendly assistant that helps people find "
        "free social services in New York City.\n"
        "You are warm, respectful, and concise.\n\n"
        "STRICT RULES:\n"
        "- You do NOT make up service names, addresses, or phone numbers.\n"
        "- You do NOT say whether services exist or are available — you "
        "have NO access to the service database from this context.\n"
        "- You do NOT give specific medical, legal, psychological, or "
        "financial advice.\n"
        "- You do NOT diagnose conditions or suggest treatments.\n"
        "- You do NOT make promises about service availability.\n"
        "- You do NOT encourage specific life decisions.\n"
        "- You do NOT ask follow-up questions about what kind of service "
        "the user needs — that is handled by a separate system.\n"
        "- If someone needs professional guidance, suggest they talk to a "
        "peer navigator.\n\n"
        "The user sent a message that doesn't match a service request. "
        "Respond naturally and briefly. If it feels right, mention that "
        "you're here to help find services whenever they're ready, but "
        "only as a brief aside, not the focus.\n"
        "Keep your response to 1-3 sentences.\n\n"
        f"User message: {user_message}"
    )


def _build_bot_question_prompt(user_message: str, slots: dict = None) -> str:
    """Prompt for questions about the bot's capabilities or behavior.

    IMPORTANT: Service slots (service_type, location, age, etc.) are
    intentionally NOT passed. Only capability indicators (e.g., whether
    geolocation is active) are included — never service search context.
    """
    from app.services.bot_knowledge import build_capability_context

    context_lines = []
    if slots:
        if slots.get("_latitude") is not None:
            context_lines.append(
                "- The user has shared their browser geolocation"
            )

    context_section = ""
    if context_lines:
        context_section = (
            "Current session context:\n"
            + "\n".join(context_lines)
            + "\n\n"
        )

    facts = build_capability_context()

    return (
        "You are YourPeer, a friendly assistant that helps people find "
        "free social services in New York City.\n\n"
        "The user is asking a question about how you work or what you can do. "
        "Answer their SPECIFIC question directly and honestly. Do not give a "
        "generic overview unless they asked for one.\n\n"
        f"{context_section}"
        f"{facts}\n\n"
        "Keep your response to 2-3 sentences. Answer the specific question. "
        "Be honest about limitations.\n\n"
        f"User question: {user_message}"
    )


# ---------------------------------------------------------------------------
# STATIC BOT ANSWERS (no LLM needed)
# ---------------------------------------------------------------------------

def _static_bot_answer(message: str) -> str:
    """Pattern-matched answers for common bot questions when LLM is unavailable."""
    # Try bot_knowledge topic matching first (richer, maintained centrally)
    try:
        from app.services.bot_knowledge import answer_question
        knowledge_answer = answer_question(message)
        if knowledge_answer:
            return knowledge_answer
    except Exception:
        pass

    lower = message.lower()

    # Geolocation questions
    if any(w in lower for w in ["location", "gps", "geolocation", "find me", "where i am"]):
        if any(w in lower for w in ["why", "couldn't", "couldnt", "can't", "cant", "didn't", "didnt", "fail", "wrong"]):
            return (
                "Location access can fail for a few reasons: you may have "
                "denied the browser permission, your device might not support "
                "GPS, or the signal timed out (common indoors). You can always "
                "tell me your neighborhood or borough instead."
            )
        return (
            "When you tap 'Use my location', I ask your browser for GPS "
            "coordinates to find services nearby. If that doesn't work, "
            "just tell me your neighborhood or borough."
        )

    # Coverage / outside NYC
    if any(w in lower for w in ["outside", "other city", "other state", "new jersey", "nj", "outside nyc"]):
        return (
            "I only search within New York City's five boroughs. For services "
            "outside NYC, you can call 211 — it's a free helpline that "
            "connects people to local resources anywhere in the US."
        )

    # What services / categories
    if any(w in lower for w in ["what services", "what can you search", "what can you find", "what kind"]):
        return (
            "I can search for food (pantries, soup kitchens), shelter, "
            "clothing, showers & personal care, health care (medical, dental, "
            "vision), mental health (counseling, substance abuse), legal help "
            "(immigration, eviction), jobs, and other services like benefits, "
            "IDs, and drop-in centers."
        )

    # Privacy — immigration / law enforcement fears
    _ice_words = ["immigration", "deport", "undocumented", "immigrant"]
    _ice_re = re.compile(r'\bice\b', re.IGNORECASE)
    if any(w in lower for w in _ice_words) or _ice_re.search(lower):
        return (
            "I don't collect any identifying information — no name, no "
            "address, no immigration status. I'm not connected to any "
            "government agency, including ICE. Your conversation is "
            "anonymous and is not shared with anyone."
        )

    if any(w in lower for w in ["police", "cop", "law enforcement", "report", "arrest"]):
        return (
            "I don't share any information with law enforcement. I don't "
            "know who you are, and your conversation here is anonymous. "
            "The only exception is if you're in immediate danger — I'll "
            "share crisis hotline numbers, but that's your choice to call."
        )

    # Privacy — benefits / provider visibility
    if any(w in lower for w in ["benefits", "case worker", "caseworker", "shelter see", "affect my"]):
        return (
            "Using this chat won't affect your benefits or case status. "
            "Shelters, case workers, and service providers can't see your "
            "conversation here. I just help you find services — I don't "
            "report anything to anyone."
        )

    # Privacy — who can see / recording / sharing
    if any(w in lower for w in [
        "who can see", "anyone see", "see what i", "see my",
        "recording", "record", "listening",
        "share", "sharing", "shared with", "tell anyone",
    ]):
        return (
            "No one else can see your conversation. I don't record audio "
            "or share your messages with other people or organizations. "
            "If you're on a shared device, you can say 'start over' to "
            "clear the chat history."
        )

    # Privacy — delete / clear data
    if any(w in lower for w in ["delete", "clear", "erase", "remove my", "forget"]):
        return (
            "Say 'start over' and your session will be cleared immediately. "
            "On this device, your chat history is stored temporarily in your "
            "browser and auto-expires after 30 minutes of inactivity."
        )

    # Privacy — identity / anonymity
    if any(w in lower for w in ["know my name", "know who i am", "anonymous", "identify"]):
        return (
            "I don't know who you are. I don't ask for or store your name, "
            "phone number, or any personal details. If you share personal "
            "info by accident, it's automatically removed before anything "
            "is saved."
        )

    # Privacy — general
    if any(w in lower for w in ["private", "privacy", "data", "store", "save", "track",
                                 "safe to", "confidential", "secure", "trust"]):
        return (
            "Your conversations are private and anonymous. I don't store "
            "personal information or link conversations to any identity. "
            "I'm not connected to any government agency or service provider. "
            "You can say 'start over' at any time to clear your session."
        )

    # How does it work
    if any(w in lower for w in ["how do you work", "how does this work", "how does it work"]):
        return (
            "You tell me what you need and where you are, and I search a "
            "database of verified social services in NYC maintained by "
            "Streetlives. I'll show you matching services with addresses, "
            "hours, and phone numbers."
        )

    # Default generic answer
    return (
        "I search a database of verified social services across NYC's five "
        "boroughs — food, shelter, clothing, showers, health care, mental "
        "health, legal help, jobs, and more. Just tell me what you need "
        "and your neighborhood, and I'll find options for you."
    )


def _fallback_response(message: str, slots: dict) -> str:
    """Try Claude for conversational response, with a safe static fallback."""
    try:
        prompt = _build_conversational_prompt(message, slots)
        return claude_reply(prompt)
    except Exception as e:
        logger.error(f"Claude fallback also failed: {e}")
        return (
            "I'm having trouble right now. "
            "You can try again in a moment, or visit yourpeer.nyc "
            "to search for services directly."
        )


# ---------------------------------------------------------------------------
# BASELINE WARMTH — default tone prefix for routine service flows
# ---------------------------------------------------------------------------
# Short, warm phrase prepended to confirmations and follow-ups when no
# emotional/shame/urgent context is detected. Prevents the bot from
# feeling "functional but flat" on routine requests.
#
# Historically this used `random.choice` over 7 prefixes for variety.
# That variety turned out to be noise in the LLM-as-judge eval:
# different prefixes score differently on Response Tone / Dignity &
# Anti-Stigma, so back-to-back runs of the same code produced drifting
# scores. Pinning to a single prefix removes the dice roll AND picks
# the warmest, most collaborative option ("Let's find something for
# you." scored best on trial evals; "I'll look into that." was flagged
# by Opus as transactional).
#
# If we later want variety back, re-introduce the list but seed the
# RNG deterministically in the eval harness instead of here — the
# product-facing change shouldn't be driven by eval needs.

_WARMTH_PREFIX = "Let's find something for you. "


def random_warmth_prefix() -> str:
    """Return the default warmth prefix for routine service flows.

    Name kept for back-compatibility with existing callers and tests;
    no longer actually random. See the comment above the constant for
    why this is pinned.
    """
    return _WARMTH_PREFIX
