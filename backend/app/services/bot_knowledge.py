"""Bot Self-Knowledge Module

Single source of truth for what the bot can do, what it can't do,
and how it handles privacy. Used by both the LLM prompt builder
(for grounded answers) and the static fallback handler (for
keyword-matched answers).

Capabilities are sourced from actual code where possible — service
categories from slot_extraction_regex, PII types from pii_redactor, locations
from the known locations list. This prevents drift between what the
code does and what the bot tells users it does.
"""

import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LIVE CAPABILITIES — sourced from actual code
# ---------------------------------------------------------------------------

def _get_service_categories() -> dict:
    """Service categories from the slot extractor (live, not hardcoded)."""
    try:
        from app.services.slot_extraction_regex import SERVICE_KEYWORDS
        return {
            cat: keywords[:5]  # first 5 keywords as examples
            for cat, keywords in SERVICE_KEYWORDS.items()
        }
    except ImportError:
        return {}


def _get_pii_categories() -> list:
    """PII types the redactor detects (live, not hardcoded)."""
    try:
        from app.privacy.pii_redactor import _PLACEHOLDERS
        return list(_PLACEHOLDERS.keys())
    except ImportError:
        return []


def _get_location_count() -> int:
    """Number of known NYC locations (live)."""
    try:
        from app.services.slot_extraction_regex import _KNOWN_LOCATIONS
        return len(_KNOWN_LOCATIONS)
    except ImportError:
        return 0


def _get_zip_code_count() -> int:
    """Number of NYC zip codes mapped to neighborhoods (live)."""
    try:
        from app.services.slot_extraction_regex import _NYC_ZIP_TO_NEIGHBORHOOD
        return len(_NYC_ZIP_TO_NEIGHBORHOOD)
    except ImportError:
        return 0


def _get_borough_list() -> list:
    """NYC boroughs (static — these don't change)."""
    return ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"]


def _get_crisis_categories() -> list:
    """Crisis categories from crisis_detector (live, not hardcoded).

    Returns the list of category names that `detect_crisis` can emit.
    Used by `build_capability_context` so the LLM prompt stays in sync
    when categories are added (e.g., the April 2026 addition of
    youth_runaway, assault_victim, safety_concern).
    """
    try:
        from app.services.crisis_detector import _CRISIS_CATEGORIES
        # Shape: list of (name, phrases, response) tuples — extract just names
        names = []
        for entry in _CRISIS_CATEGORIES:
            if isinstance(entry, tuple) and entry:
                names.append(entry[0])
            elif isinstance(entry, str):
                names.append(entry)
        return names
    except ImportError:
        return []


# ---------------------------------------------------------------------------
# CAPABILITY TOPICS
# ---------------------------------------------------------------------------
# Each topic is a dict with:
#   - keywords: list of trigger words for static matching
#   - answer: user-facing response (for static fallback)
#   - summary: one-line summary (for LLM context)
#   - source: which code module implements this

TOPICS = {
    "services": {
        "keywords": [
            "what services", "what can you search", "what can you find",
            "what can you help with", "what can you help me with",
            "what kind", "what do you do", "what can you do",
            "capabilities", "what are you able to",
        ],
        "answer": (
            "I can search for free services across NYC's five boroughs: "
            "food (pantries, soup kitchens), shelter (emergency, transitional), "
            "clothing, showers & personal care, health care (medical, dental, "
            "vision), mental health (counseling, substance use), legal help "
            "(immigration, eviction), jobs & training, and other services "
            "like benefits (SNAP/EBT), IDs, and drop-in centers."
        ),
        "summary": "Searches 9 service categories across NYC's five boroughs",
        "source": "slot_extraction_regex.py → SERVICE_KEYWORDS",
    },

    "location_how": {
        "keywords": [
            "location", "gps", "geolocation", "find me", "where i am",
            "use my location", "how do you know",
        ],
        "answer": (
            "When you tap 'Use my location', I ask your browser for GPS "
            "coordinates to find services nearby. If that doesn't work, "
            "just tell me your neighborhood, borough, or zip code."
        ),
        "summary": "Uses browser geolocation or user-stated neighborhood/borough/zip code",
        "source": "chatbot/pipeline.py → _apply_session_geo",
    },

    "location_fail": {
        "keywords": [
            "why couldn't", "why couldnt", "why can't", "why cant",
            "why didn't", "why didnt", "location fail", "location not working",
            "couldn't get my location", "can't get my location",
            "didn't get my location", "not getting my location",
            "location wrong", "wrong location",
            "location isn't working", "location isnt working",
        ],
        "answer": (
            "Location access can fail for a few reasons: you may have "
            "denied the browser permission, your device might not support "
            "GPS, or the signal timed out (common indoors). You can always "
            "tell me your neighborhood, borough, or zip code instead."
        ),
        "summary": "Geolocation can fail (permission denied, GPS timeout, indoor signal)",
        "source": "responses.py → _build_bot_question_prompt",
    },

    "coverage": {
        "keywords": [
            "outside nyc", "outside new york", "other city", "other state",
            "new jersey", "nj", "connecticut", "ct", "long island",
            "westchester", "outside the city",
        ],
        "answer": (
            "I only search within New York City's five boroughs. For services "
            "outside NYC, you can call 211 — it's a free helpline that "
            "connects people to local resources anywhere in the US."
        ),
        "summary": "NYC only — suggests 211 for outside coverage",
        "source": "responses.py → _build_bot_question_prompt",
    },

    "privacy_general": {
        "keywords": [
            "private", "privacy", "data", "store", "save", "track",
            "safe to", "confidential", "secure", "trust",
            "what happens to my information", "what do you do with",
            "is this safe", "is this private", "is this confidential",
        ],
        "answer": (
            "Your conversations are private and anonymous. I don't store "
            "personal information or link conversations to any identity. "
            "I'm not connected to any government agency or service provider. "
            "If you share personal info by accident (phone number, name, "
            "SSN, email, address, date of birth, credit card, URL, or "
            "gender-identity terms), it's automatically detected and "
            "redacted before anything is saved. You can say 'start over' "
            "at any time to clear your session."
        ),
        "summary": "Anonymous, no PII stored, automatic redaction of 9 PII types",
        "source": "pii_redactor.py → _PLACEHOLDERS",
    },

    "privacy_ice": {
        "keywords": [
            "immigration", "deport", "undocumented", "immigrant",
            "ice", "migra", "papeles",
        ],
        "answer": (
            "I don't collect any identifying information — no name, no "
            "address, no immigration status. I'm not connected to any "
            "government agency, including ICE. Your conversation is "
            "anonymous and is not shared with anyone."
        ),
        "summary": "Not connected to ICE or any government agency",
        "source": "chatbot/handlers/meta.py → _static_bot_answer (ICE section)",
    },

    "privacy_police": {
        "keywords": [
            "police", "cop", "law enforcement", "report", "arrest",
        ],
        "answer": (
            "I don't share any information with law enforcement. I don't "
            "know who you are, and your conversation here is anonymous. "
            "The only exception is if you're in immediate danger — I'll "
            "share crisis hotline numbers, but that's your choice to call."
        ),
        "summary": "No information shared with law enforcement",
        "source": "chatbot/handlers/meta.py → _static_bot_answer (police section)",
    },

    "privacy_benefits": {
        "keywords": [
            "benefits", "case worker", "caseworker", "shelter see",
            "affect my", "will this affect", "impact my",
            "do they get my", "does the shelter know", "does the provider",
            "will they know", "can they see", "do they know i searched",
        ],
        "answer": (
            "Using this chat won't affect your benefits or case status. "
            "Shelters, case workers, and service providers can't see your "
            "conversation here. I just help you find services — I don't "
            "report anything to anyone."
        ),
        "summary": "No impact on benefits/case status, providers can't see chat",
        "source": "chatbot/handlers/meta.py → _static_bot_answer (benefits section)",
    },

    "privacy_visibility": {
        "keywords": [
            "who can see", "anyone see", "see what i", "see my",
            "recording", "record", "listening",
            "share", "sharing", "shared with", "tell anyone",
            "is this anonymous", "is this confidential",
            "who gets my", "who sees my",
        ],
        "answer": (
            "No one else can see your conversation. I don't record audio "
            "or share your messages with other people or organizations. "
            "If you're on a shared device, you can say 'start over' to "
            "clear the chat history."
        ),
        "summary": "No recording, no sharing, session clearable",
        "source": "chatbot/handlers/meta.py → _static_bot_answer (visibility section)",
    },

    "privacy_delete": {
        "keywords": [
            "delete", "clear", "erase", "remove my", "forget",
        ],
        "answer": (
            "Say 'start over' and your session will be cleared immediately. "
            "On this device, your chat history is stored temporarily in your "
            "browser and auto-expires after 30 minutes of inactivity."
        ),
        "summary": "'Start over' clears session, auto-expires after 30 min",
        "source": "chatbot/handlers/meta.py → _static_bot_answer (delete section)",
    },

    "privacy_identity": {
        "keywords": [
            "know my name", "know who i am", "anonymous", "identify",
        ],
        "answer": (
            "I don't know who you are. I don't ask for or store your name, "
            "phone number, or any personal details. If you share personal "
            "info by accident, it's automatically removed before anything "
            "is saved."
        ),
        "summary": "No identity tracking, PII auto-redacted",
        "source": "pii_redactor.py",
    },

    "how_it_works": {
        "keywords": [
            "how do you work", "how does this work", "how does it work",
            "how do you find", "where do you get",
        ],
        "answer": (
            "You tell me what you need and where you are, and I search a "
            "database of verified social services in NYC maintained by "
            "Streetlives (yourpeer.nyc). I'll show you matching services "
            "with addresses, hours, and phone numbers. The data is verified "
            "by community members and staff."
        ),
        "summary": "Searches Streetlives database of verified NYC services",
        "source": "chatbot/orchestrator.py → generate_reply",
    },

    "crisis_support": {
        "keywords": [
            "crisis", "emergency", "hotline", "988", "suicide",
            "danger", "help line", "helpline",
        ],
        "answer": (
            "If you're in crisis, I can connect you with these free, "
            "confidential resources:\n"
            "• 988 Suicide & Crisis Lifeline — call or text 988 (24/7)\n"
            "• Crisis Text Line — text HOME to 741741\n"
            "• NYC Domestic Violence Hotline — 1-800-621-4673\n"
            "• National DV Hotline — 1-800-799-7233\n"
            "• Trevor Project (LGBTQ+ youth) — 1-866-488-7386\n\n"
            "I can also connect you with a peer navigator — a real person "
            "who can help."
        ),
        "summary": "Crisis detection with hotline resources (988, DV, Trevor)",
        "source": "crisis_detector.py",
    },

    "peer_navigator": {
        "keywords": [
            "peer navigator", "talk to a person", "real person",
            "human", "speak to someone", "talk to someone",
        ],
        "answer": (
            "A peer navigator is a real person who has lived experience "
            "with the social services system and can help you one-on-one. "
            "You can reach the Streetlives team through yourpeer.nyc, "
            "or I can connect you right now. Just say 'talk to a person'."
        ),
        "summary": "Peer navigators available via yourpeer.nyc",
        "source": "responses.py → _ESCALATION_RESPONSE",
    },

    "limitations": {
        "keywords": [
            "limitation", "limitations", "can't do", "cant do", "unable",
            "don't know", "dont know", "not able", "what can't you",
            "what cant you",
        ],
        "answer": (
            "I'm an AI assistant, so there are things I can't do: I can't "
            "make appointments or reservations for you, I can't verify if "
            "a service is currently open or has capacity, and I can't "
            "provide medical, legal, or financial advice. My data may not "
            "always be current — always call ahead to confirm hours. "
            "For complex needs, a peer navigator can help you directly."
        ),
        "summary": "AI limitations: no appointments, no real-time availability, no advice",
        "source": "chatbot/ package",
    },

    "language": {
        "keywords": [
            "language", "spanish", "espanol", "español", "translate",
            "other language", "chinese", "french", "arabic",
            "hablas", "habla", "do you speak",
        ],
        "answer": (
            "I work primarily in English, with partial Spanish support: "
            "I recognize Spanish service requests (comida, refugio, albergue, "
            "tengo hambre) and respond with a bilingual acknowledgment, "
            "though the rest of the conversation — confirmations, results, "
            "buttons — is in English. For full Spanish support or other "
            "languages, a peer navigator may be able to assist — just say "
            "'talk to a person'. Service cards show which languages each "
            "provider speaks, so you can pick one that matches your needs."
        ),
        "summary": "English primary + partial Spanish detection/acknowledgment; other languages via peer navigator",
        "source": "chatbot/handlers/accessibility.py → _handle_spanish_detection",
    },
}


# ---------------------------------------------------------------------------
# QUERY FUNCTIONS
# ---------------------------------------------------------------------------

def answer_question(message: str) -> str | None:
    """Find the best matching topic for a bot question.

    Returns the user-facing answer string, or None if no topic matches.

    Priority order matters for multi-topic collisions:
    1. Specific privacy topics (ICE, police, benefits) — highest
    2. General privacy — catches broad privacy questions
    3. Location fail — before location how (more specific)
    4. Location how — general location questions
    5. Other topics — services, coverage, how_it_works, etc.

    When a message matches BOTH a privacy topic and location_how
    (e.g., "Is my location data private?"), privacy wins because
    the user's concern is about privacy, not how location works.
    """
    lower = message.lower()

    # Check specific privacy topics first (highest priority)
    for topic_id in [
        "privacy_ice", "privacy_police", "privacy_benefits",
        "privacy_visibility", "privacy_delete", "privacy_identity",
    ]:
        if _topic_matches(topic_id, lower):
            return TOPICS[topic_id]["answer"]

    # General privacy (before location — "is my location data private?" = privacy)
    if _topic_matches("privacy_general", lower):
        return TOPICS["privacy_general"]["answer"]

    # Location fail before location how (more specific)
    if _topic_matches("location_fail", lower):
        return TOPICS["location_fail"]["answer"]
    if _topic_matches("location_how", lower):
        return TOPICS["location_how"]["answer"]

    # Other topics — coverage before services (more specific)
    for topic_id in [
        "coverage", "services", "how_it_works", "crisis_support",
        "peer_navigator", "limitations", "language",
    ]:
        if _topic_matches(topic_id, lower):
            return TOPICS[topic_id]["answer"]

    return None


def _topic_matches(topic_id: str, lower_message: str) -> bool:
    """Check if a message matches a topic's keywords."""
    import re
    topic = TOPICS[topic_id]
    for kw in topic["keywords"]:
        if len(kw) <= 3:
            # Short keywords need word boundaries to avoid false positives
            if re.search(r'\b' + re.escape(kw) + r'\b', lower_message):
                return True
        else:
            if kw in lower_message:
                return True
    return False


def build_capability_context() -> str:
    """Generate the 'Facts about yourself' section for the LLM prompt.

    Sources data from live code where possible, so the prompt stays
    accurate as features change.
    """
    service_cats = _get_service_categories()
    pii_types = _get_pii_categories()
    location_count = _get_location_count()
    zip_count = _get_zip_code_count()
    boroughs = _get_borough_list()

    # Format service categories from live data
    service_lines = []
    _SERVICE_LABELS = {
        "food": "Food: soup kitchens, food pantries, groceries",
        "shelter": "Shelter: emergency shelter, transitional housing, drop-in",
        "clothing": "Clothing: free clothing programs",
        "personal_care": "Personal care: showers, laundry, haircuts, hygiene",
        "medical": "Health care: medical, dental, vision, STD testing",
        "mental_health": "Mental health: counseling, therapy, substance use, AA/NA",
        "legal": "Legal help: immigration, eviction, asylum, legal aid",
        "employment": "Jobs: employment programs, job training, resume help",
        # "housing_assistance" retired April 15, 2026 — rental assistance,
        # Section 8, NYCHA, eviction prevention, etc. are now surfaced via
        # the 'other' category (see semantic_routes.py). The 'other' label
        # below was updated to mention housing help so users searching for
        # rental assistance still see it listed among supported categories.
        "other": "Other: benefits (SNAP/EBT/Medicaid), IDs, rental assistance, Section 8, NYCHA, drop-in centers, free phones",
    }
    for cat in service_cats:
        label = _SERVICE_LABELS.get(cat, cat)
        service_lines.append(f"  • {label}")

    # Format PII types from live data
    pii_line = ", ".join(pii_types) if pii_types else "phone numbers, names, SSNs, emails"

    lines = [
        f"- You search a database of verified social services in NYC's five "
        f"boroughs ({', '.join(boroughs)}), maintained by Streetlives (yourpeer.nyc)",
        "- You ONLY cover New York City. For services outside NYC, suggest calling 211",
        "- Service categories you can search:",
        *service_lines,
        f"- You know {location_count} NYC neighborhoods and {zip_count} NYC zip codes",
        "- Geolocation: you use the browser's GPS when the user taps 'Use my location'. "
        "Common reasons it can fail: browser permission denied, device doesn't support GPS, "
        "GPS timed out (indoors), or site not on HTTPS. If geolocation fails, ask for "
        "neighborhood or borough instead",
        "- If a user says 'I don't know', 'anywhere', or 'here' when asked for location, "
        "you offer geolocation and borough buttons — you don't treat it as confusion",
        "- Multiple services: you can handle requests like 'food and shelter in Brooklyn'. "
        "You prioritize by need — shelter and medical first, then food, then clothing, "
        "then employment and legal. You search the highest-need service first, show results, "
        "then offer to search for the next one",
        "- Long messages: you understand narrative descriptions like 'I just got out of "
        "Rikers and I don't have anywhere to sleep or anything to eat'. You extract all "
        "services mentioned, even when phrased as negations ('don't have', 'nowhere to')",
        "- Follow-up questions: after showing results, you can answer questions like "
        "'are any open now?', 'only the pantries', 'not the DHS ones', 'for families', "
        "or 'the one on 3rd street'. You filter the displayed results without a new "
        "database search",
        "- Co-located services: result cards show other services at the same location "
        "(e.g., 'Also here: Shower · Clothing')",
        "- Family composition: for shelter searches, you ask about family/children to "
        "find appropriate sub-category matches (youth, family, single adult)",
        "- Recently verified services appear first — you sort results by how recently "
        "they were verified by the Streetlives team",
        "- Crisis geolocation: after showing crisis resources (DV hotlines, runaway "
        "services), tapping 'Yes, search for shelter' uses the browser's GPS to find "
        "the nearest services immediately — no extra steps",
        "- Privacy protections:",
        "  • Not connected to any government agency, including ICE",
        "  • No information shared with law enforcement",
        "  • Shelters, case workers, and providers cannot see the conversation",
        "  • Using this chat will NOT affect benefits or case status",
        f"  • PII auto-redacted: {pii_line}",
        "  • 'Start over' clears the session immediately",
        "  • Chat history auto-expires after 30 minutes",
        "  • On shared/public devices, other users could see chat until it expires",
        "- You can connect users with a human peer navigator for support",
        "- You are an AI assistant, not a human",
        "- Your data comes from verified listings. Hours and availability may change — "
        "always call ahead to confirm",
        "- Crisis detection: you can detect " + _format_crisis_categories() + " "
        "and provide appropriate hotline resources. Specific hotline numbers "
        "(988 Lifeline, Crisis Text Line, domestic-violence lines, "
        "runaway-youth lines, trafficking lines, etc.) are shown to users "
        "via the crisis-response path — you do not recite them from this "
        "prompt",
        "- Emotional support: you acknowledge feelings (scared, sad, shame, grief, "
        "isolation) before offering services — you don't push services on someone "
        "who's expressing distress",
        "- Limitations: you cannot make appointments, verify real-time availability, "
        "or provide medical/legal/financial advice. You work primarily in English "
        "with partial Spanish support (bilingual acknowledgment + Spanish service "
        "keywords); other languages via peer navigator",
    ]

    return "Facts about yourself:\n" + "\n".join(lines)


def _format_crisis_categories() -> str:
    """Format the live crisis-category list as a human-readable phrase.

    Used by `build_capability_context` to keep the LLM prompt's crisis
    description in sync with `_CRISIS_CATEGORIES`. Maps internal category
    names (e.g. "suicide_self_harm") to user-facing phrasings.
    """
    # Friendly names for user-facing text. Keys MUST match
    # _CRISIS_CATEGORIES names in crisis_detector.py. When a new category
    # is added there, add its label here — `test_bot_knowledge_freshness`
    # asserts every live category has an entry in this mapping.
    _FRIENDLY = {
        "suicide_self_harm": "suicidal ideation",
        "domestic_violence": "domestic violence",
        "safety_concern": "unsafe situations",
        "trafficking": "trafficking",
        "medical_emergency": "medical emergencies",
        "violence": "threats of violence",
        "youth_runaway": "youth runaway / kicked out",
        "assault_victim": "assault / being attacked",
    }
    names = _get_crisis_categories()
    friendly = [_FRIENDLY.get(n, n.replace("_", " ")) for n in names]
    if not friendly:
        return "crisis situations"
    if len(friendly) == 1:
        return friendly[0]
    return ", ".join(friendly[:-1]) + ", and " + friendly[-1]
