"""
Post-Results Question Handler — answers follow-up questions about
services that were just displayed, using ONLY the data on the cards.

Design principles:
    - NO LLM generation: every answer is assembled from stored card data.
    - If we don't have the data, say so and offer alternatives (peer
      navigator, call the service directly).
    - Pattern-matched question classification: regex, not LLM.
    - Returns structured responses the chatbot can render.
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# QUESTION CLASSIFICATION
# ---------------------------------------------------------------------------

# Patterns that signal a question about the displayed results.
# Each tuple: (compiled_regex, intent_type, optional_extras)

_ORDINAL_MAP = {
    "first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2,
    "fourth": 3, "4th": 3, "fifth": 4, "5th": 4, "sixth": 5, "6th": 5,
    "seventh": 6, "7th": 6, "eighth": 7, "8th": 7, "ninth": 8, "9th": 8,
    "tenth": 9, "10th": 9, "last": -1,
}

_FILTER_OPEN_RE = re.compile(
    r"\b(open now|open today|which.*open|are.*open|any.*open|"
    r"who.*open|still open|currently open)\b", re.I
)
_FILTER_FREE_RE = re.compile(
    r"\b(free|no cost|no fee|don.t cost|doesn.t cost|cost anything|"
    r"which.*free|are.*free|any.*free)\b", re.I
)
_ASK_HOURS_RE = re.compile(
    r"\b(hours|when.*open|what time|schedule|close|closing)\b", re.I
)

# Gap 15: specific day-of-week detection
# Maps day names to ISO day-of-week numbers (Monday=1, Sunday=7)
# matching PostgreSQL EXTRACT(ISODOW ...) used in holiday_schedules.
_DAY_NAMES = {
    "monday": 1, "mon": 1,
    "tuesday": 2, "tue": 2, "tues": 2,
    "wednesday": 3, "wed": 3,
    "thursday": 4, "thu": 4, "thurs": 4, "thur": 4,
    "friday": 5, "fri": 5,
    "saturday": 6, "sat": 6,
    "sunday": 7, "sun": 7,
}
_DAY_PATTERN = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|tues|wed|thu|thurs?|fri|sat|sun)\b", re.I
)
_WEEKEND_RE = re.compile(r"\b(weekend|weekends)\b", re.I)
_ASK_ADDRESS_RE = re.compile(
    r"\b(address|where|location|directions|how.*get there|"
    r"how.*far|located)\b", re.I
)
_ASK_PHONE_RE = re.compile(
    r"\b(phone|call|number|contact|reach)\b", re.I
)
_ASK_WEBSITE_RE = re.compile(
    r"\b(website|web site|url|online|link|site)\b", re.I
)
_SPECIFIC_INDEX_RE = re.compile(
    r"(?:\b(?:the\s+)?(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|last|"
    r"1st|2nd|3rd|4th|5th|6th|7th|8th|9th|10th)\s*(?:one|result|option|service|place)?"
    r"|(?:^|\s)#(\d{1,2})\b"
    r"|\bnumber\s*(\d{1,2})\b"
    r"|\boption\s*(\d{1,2})\b)", re.I
)
_SPECIFIC_MORE_RE = re.compile(
    r"\b(tell me (?:more|about)|more (?:about|info|details|on)|"
    r"what about|details (?:on|about|for)|info (?:on|about))\b", re.I
)

# General result-reference signals — the user is talking about the results
_RESULT_REFERENCE_RE = re.compile(
    r"\b(them|they|these|those|the results|any of them|"
    r"which one|the services|the options|the places)\b", re.I
)


# ---------------------------------------------------------------------------
# SUB-CATEGORY FILTERING ENGINE (Phase 1)
# ---------------------------------------------------------------------------
# All filtering operates on DB-sourced card data. The LLM is never used
# to generate, rank, or select services. It is used in Tier 2 to EXTRACT
# search keywords from the user's natural language when regex-based
# extraction fails — but the actual matching is always deterministic.

# --- Taxonomy aliases: user-facing terms → canonical DB taxonomy names ---
# Built from PROD query (70 taxonomies). Case-insensitive matching.
_TAXONOMY_FILTER_ALIASES = {
    # Shelter sub-types
    "intake": "Intake", "intakes": "Intake",
    "families": "Families", "family": "Families",
    "single adult": "Single Adult", "singles": "Single Adult",
    "single adults": "Single Adult",
    "youth": "Youth", "young adult": "Youth",
    "senior": "Senior", "seniors": "Senior",
    "safe haven": "Safe Haven",
    "warming center": "Warming Center",
    "drop-in": "Drop-in Center", "drop in": "Drop-in Center",
    "drop-in center": "Drop-in Center",
    "assessment": "Assessment",
    "veterans": "Veterans", "vet": "Veterans", "vets": "Veterans",
    "lgbtq": "LGBTQ Young Adult", "lgbtq+": "LGBTQ Young Adult",
    "transitional": "Transitional Independent Living (TIL)",
    "til": "Transitional Independent Living (TIL)",
    "supportive housing": "Supportive Housing",
    "residential recovery": "Residential Recovery",
    # Food sub-types
    "soup kitchen": "Soup Kitchen", "soup kitchens": "Soup Kitchen",
    "food pantry": "Food Pantry", "food pantries": "Food Pantry",
    "pantry": "Food Pantry", "pantries": "Food Pantry",
    "mobile pantry": "Mobile Pantry",
    "brown bag": "Brown Bag",
    "food benefits": "Food Benefits",
    "snap": "Food Benefits", "wic": "Food Benefits",
    "farmers market": "Farmer's Markets",
    # Personal care sub-types
    "shower": "Shower", "showers": "Shower",
    "laundry": "Laundry",
    "haircut": "Haircut", "haircuts": "Haircut",
    "toiletries": "Toiletries",
    "restrooms": "Restrooms", "restroom": "Restrooms",
    # Clothing sub-types
    "clothing pantry": "Clothing Pantry",
    "interview clothing": "Interview-Ready Clothing",
    "coat drive": "Coat Drive",
    "thrift shop": "Thrift Shop",
    # Health sub-types
    "substance use": "Substance Use Treatment",
    "detox": "Substance Use Treatment",
    "mental health": "Mental Health",
    "counseling": "Mental Health",
    "support groups": "Support Groups",
    # Other sub-types
    "education": "Education",
    "employment": "Employment",
    "legal": "Legal Services", "legal services": "Legal Services",
    "immigration": "Immigration Services",
    "benefits": "Benefits",
    "case management": "Case Workers", "case workers": "Case Workers",
    "mail": "Mail",
}

# --- Negation detection ---
_NEGATION_RE = re.compile(
    r"\b(not the|without|don.t show|exclude|skip|remove|"
    r"none of the|anything (?:but|except)|other than|"
    r"not.*(?:ones?|services?|places?|results?)|"
    r"no (?:referral|id|appointment|membership))\b", re.I
)

# --- Structured field filters ---
# Each: (regex_pattern, filter_function, description)
# These check card fields directly — deterministic, no text search.
_STRUCTURED_FILTERS = [
    (re.compile(r"\b(no referral|walk.?in|no appointment|drop.?in|no membership)\b", re.I),
     lambda card: not card.get("requires_membership"),
     "no referral needed"),

    (re.compile(r"\b(no id|no identification|don.t need id|without id|no documents?)\b", re.I),
     lambda card: not card.get("required_documents"),
     "no ID required"),

    (re.compile(r"\b(good review|highly rated|well reviewed|has reviews?)\b", re.I),
     lambda card: bool(card.get("review_highlight")),
     "has reviews"),

    (re.compile(r"\b(wheelchair|accessible|ada)\b", re.I),
     lambda card: bool(card.get("accessibility")) or _desc_contains(card, "accessible", "wheelchair"),
     "accessible"),

    (re.compile(r"\b(speaks? spanish|habla español|en español)\b", re.I),
     lambda card: _lang_contains(card, "Spanish") or _desc_contains(card, "spanish"),
     "speaks Spanish"),

    (re.compile(r"\b(for famil|takes? kids|with children|accept.*children)\b", re.I),
     lambda card: _has_taxonomy(card, "Families") or _elig_contains(card, "families"),
     "for families"),

    (re.compile(r"\b(for (?:women|females?)|women.?only)\b", re.I),
     lambda card: _has_taxonomy(card, "Single Adult") or _elig_contains(card, "female", "women"),
     "for women"),

    (re.compile(r"\b(for (?:men|males?)|men.?only)\b", re.I),
     lambda card: _has_taxonomy(card, "Single Adult") or _elig_contains(card, "male", "men"),
     "for men"),

    (re.compile(r"\b(for youth|for young|under 25|under 21|teens?|teenagers?)\b", re.I),
     lambda card: _has_taxonomy(card, "Youth") or _has_taxonomy(card, "LGBTQ Young Adult"),
     "for youth"),

    (re.compile(r"\b(for seniors?|for older|elderly|over 60|over 65)\b", re.I),
     lambda card: _has_taxonomy(card, "Senior"),
     "for seniors"),

    (re.compile(r"\b(for veterans?|for vets?|military)\b", re.I),
     lambda card: _has_taxonomy(card, "Veterans") or _has_taxonomy(card, "Veterans Short-Term Housing"),
     "for veterans"),
]


def _has_taxonomy(card: dict, taxonomy_name: str) -> bool:
    """Check if card's service_taxonomies contains the given taxonomy."""
    tags = card.get("service_taxonomies") or []
    return taxonomy_name in tags


def _elig_contains(card: dict, *terms: str) -> bool:
    """Check if eligibility_summary contains any of the given terms."""
    summary = (card.get("eligibility_summary") or "").lower()
    return any(t.lower() in summary for t in terms)


def _lang_contains(card: dict, language: str) -> bool:
    """Check if languages list contains the given language."""
    langs = card.get("languages") or []
    return language in langs


def _desc_contains(card: dict, *terms: str) -> bool:
    """Check if description contains any of the given terms."""
    desc = (card.get("description") or "").lower()
    return any(t.lower() in desc for t in terms)


def _also_has(card: dict, service_label: str) -> bool:
    """Check if also_available includes the given service label."""
    also = card.get("also_available") or []
    return any(service_label.lower() in a.lower() for a in also)


# --- Phrase extraction: strip intent signal words to get filter concept ---
_STRIP_INTENT_RE = re.compile(
    r"^(only the|just the|just show me the|show me only the|just show me|"
    r"just show the|only show me the|only show the|only show me|"
    r"can you locate more like|can you locate more|can you locate similar|"
    r"more like|filter to|narrow to|narrow down to|"
    r"i only need|i just need|i just want|"
    r"locate more like|show only"
    # Negation prefixes
    r"|not the|don.t show me the|don.t show me|don.t show the"
    r"|exclude the|exclude|remove the|remove|skip the|skip"
    r"|anything but|anything except|everything but|everything except"
    r"|other than the|other than"
    r"|without)\s*", re.I
)

_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "only", "just", "more", "like", "that", "this", "those",
    "these", "relevant", "ones", "one", "can", "you", "locate",
    "find", "show", "filter", "narrow", "similar", "with",
    "for", "and", "or", "of", "in", "to", "me", "my",
}


def _extract_raw_phrase(message: str) -> str:
    """Extract the filter concept from a refinement message.

    Strips intent signal words and returns the core phrase.
    Example: "Only the adult families intake is relevant"
             → "adult families intake"
    Example: "Not the DHS ones"
             → "DHS"
    """
    stripped = _STRIP_INTENT_RE.sub("", message.strip())
    # Remove trailing filler: "is relevant", "is important", etc.
    stripped = re.sub(
        r"\s*(?:is|are)\s+(?:relevant|important|what i need|good)\.?\s*$",
        "", stripped, flags=re.I,
    )
    # Remove trailing result-reference words: "ones", "services", "shelters"
    stripped = re.sub(
        r"\s+(?:ones?|services?|places?|results?|shelters?|options?|locations?)\s*$",
        "", stripped, flags=re.I,
    )
    return stripped.strip() or message.strip()


def _extract_keywords(raw_phrase: str) -> list[str]:
    """Extract meaningful search keywords from raw_phrase.

    Strips stop words. Returns lowercase keywords of 3+ characters.
    """
    words = re.findall(r"[a-zA-Z]+", raw_phrase.lower())
    return [w for w in words if w not in _STOP_WORDS and len(w) >= 3]


def _filter_by_taxonomy(cards: list[dict], raw_phrase: str) -> tuple[list[dict], set[str]]:
    """Filter cards by matching raw_phrase against service_taxonomies.

    Tries exact phrase, individual words, and bigrams against
    _TAXONOMY_FILTER_ALIASES. Returns (matched_cards, matched_taxonomy_names).
    """
    phrase_lower = raw_phrase.lower().strip()

    # Build candidate list: full phrase, individual words, bigrams
    candidates = [phrase_lower]
    words = phrase_lower.split()
    candidates.extend(words)
    for i in range(len(words) - 1):
        candidates.append(f"{words[i]} {words[i+1]}")

    matched_taxonomies = set()
    for candidate in candidates:
        taxonomy_name = _TAXONOMY_FILTER_ALIASES.get(candidate)
        if taxonomy_name:
            matched_taxonomies.add(taxonomy_name)

    if not matched_taxonomies:
        return [], set()

    matched = [
        card for card in cards
        if card.get("service_taxonomies")
        and any(t in card["service_taxonomies"] for t in matched_taxonomies)
    ]
    return matched, matched_taxonomies


def _filter_by_structured(cards: list[dict], message: str) -> tuple[list[dict], str]:
    """Apply structured field filters from dispatch table.

    Returns (matched_cards, filter_description) or ([], "") if no match.
    """
    lower = message.lower()
    for pattern, filter_fn, description in _STRUCTURED_FILTERS:
        if pattern.search(lower):
            matched = [c for c in cards if filter_fn(c)]
            return matched, description
    return [], ""


def _filter_by_colocated(cards: list[dict], message: str) -> tuple[list[dict], str]:
    """Filter by co-located services ('also has food', 'with showers')."""
    m = re.search(r"\balso (?:has|have|offers?|provides?)\s+(\w+)", message, re.I)
    if not m:
        m = re.search(r"\bwith\s+(food|shelter|shower|clothing|health|legal)\b", message, re.I)
    if m:
        service_label = m.group(1).strip()
        matched = [c for c in cards if _also_has(c, service_label)]
        return matched, f"also has {service_label}"
    return [], ""


def _text_search_cards(cards: list[dict], keywords: list[str]) -> list[dict]:
    """Filter cards where service_name or description contains ANY keyword.

    Case-insensitive. service_name matches weighted 2× vs description.
    Returns cards sorted by match score (best matches first).

    NOTE: ANY-keyword matching can produce loose results when the user's
    refinement phrase contains common words. "adult families intake"
    works well because all 3 words are specific. A phrase like
    "the one with the good program" would match any card containing
    "good" or "program" — potentially all of them.

    Mitigation: taxonomy matching (Tier 1a) fires first and catches
    most structured refinements before text search runs. Text search
    is the fallback for unstructured/unique phrases.
    """
    results = []
    for card in cards:
        name = (card.get("service_name") or "").lower()
        desc = (card.get("description") or "").lower()
        org = (card.get("organization") or "").lower()

        match_score = 0
        for kw in keywords:
            if kw in name:
                match_score += 2
            if kw in desc:
                match_score += 1
            if kw in org:
                match_score += 1

        if match_score > 0:
            results.append((match_score, card))

    results.sort(key=lambda x: x[0], reverse=True)
    return [card for _, card in results]


def classify_post_results_question(message: str) -> Optional[dict]:
    """Detect whether a message is a follow-up about displayed results.

    Returns an intent dict or None if the message isn't about results.

    Intent shapes:
        {"type": "filter_open"}
        {"type": "filter_free"}
        {"type": "ask_field", "field": "hours"|"address"|"phone"|"website"}
        {"type": "specific_index", "index": int}
        {"type": "specific_name", "query": str}
        {"type": "unknown_about_results"}
    """
    lower = message.lower().strip()

    # --- New-request escape hatch ---
    # If the message looks like a NEW service request rather than a question
    # about displayed results, bail out and let the main router handle it.
    # Signals: "I need", "can I get", "looking for", "find me", "help me find",
    # "search for", or a location + need verb combination.
    _NEW_REQUEST_RE = re.compile(
        r"\b(i need|i'm looking|im looking|looking for|can i get|find me|"
        r"help me find|search for|can you find|can you search|"
        r"where can i (?:go|find|get)|i want to find|"
        r"can you locate(?! more| like| similar)"
        r"|do you have|is there)\b", re.I
    )
    if _NEW_REQUEST_RE.search(lower):
        return None

    # Specific service by index: "the first one", "#2", "number 3"
    idx = _extract_service_index(lower)
    if idx is not None:
        return {"type": "specific_index", "index": idx}

    # "Tell me more about [name]" — try to extract a service name
    more_match = _SPECIFIC_MORE_RE.search(lower)
    if more_match:
        # Check if there's a name after the "tell me about" phrase
        remainder = lower[more_match.end():].strip()
        # Remove common filler words
        remainder = re.sub(r"^(the|that|this|it|about)\s+", "", remainder).strip()
        if remainder and len(remainder) > 2:
            return {"type": "specific_name", "query": remainder}
        # "Tell me more" without a name — ambiguous
        return {"type": "unknown_about_results"}

    # Filter questions
    # Gap 15: check for specific day-of-week BEFORE generic open/hours filters.
    # "are you open Saturday" should route to ask_hours_day (day-specific),
    # not filter_open (which means "open right now").
    day_match = _DAY_PATTERN.search(lower)
    weekend_match = _WEEKEND_RE.search(lower)
    # A day-specific question can use either hours vocabulary ("hours",
    # "what time", "schedule") OR open vocabulary ("are you open", "open on")
    # — both are asking about a specific day's availability.
    # Also accept bare "open" when paired with a day name — "open Monday?"
    # is clearly asking about Monday's availability.
    _bare_open = re.search(r"\bopen\b", lower, re.I)
    _day_hours_signal = _ASK_HOURS_RE.search(lower) or _FILTER_OPEN_RE.search(lower) or _bare_open
    if day_match and _day_hours_signal:
        weekday = _DAY_NAMES.get(day_match.group(1).lower())
        if weekday is not None:
            return {"type": "ask_hours_day", "weekday": weekday}
    if weekend_match and _day_hours_signal:
        return {"type": "ask_hours_day", "weekday": 6, "weekend": True}

    if _FILTER_OPEN_RE.search(lower):
        return {"type": "filter_open"}
    if _FILTER_FREE_RE.search(lower):
        return {"type": "filter_free"}

    if _ASK_HOURS_RE.search(lower):
        return {"type": "ask_field", "field": "hours"}
    if _ASK_ADDRESS_RE.search(lower):
        return {"type": "ask_field", "field": "address"}
    if _ASK_PHONE_RE.search(lower):
        return {"type": "ask_field", "field": "phone"}
    if _ASK_WEBSITE_RE.search(lower):
        return {"type": "ask_field", "field": "website"}

    # General reference to results but we don't understand the question
    if _RESULT_REFERENCE_RE.search(lower):
        return {"type": "unknown_about_results"}

    # --- Tier 1: Unambiguous refinement (regex, <1ms) ---
    # These signals ONLY appear in refinement context — they inherently
    # reference the displayed results. No false positives possible.
    #
    # IMPORTANT: patterns like "just show", "only the", "the X intake"
    # are intentionally NOT here — they're ambiguous between refinement
    # and new request depending on what follows. Those go to Tier 2 (LLM).
    _REFINE_RE = re.compile(
        r"\b(more like that|more like those|more like this"
        r"|locate more|locate similar"
        r"|similar to"
        r"|ones like|ones that|ones with|ones for"
        r"|(?:filter|narrow|refine)(?:ing)?\b"
        r"|only.*(?:is|are) relevant)\b", re.I
    )
    if _REFINE_RE.search(lower):
        return {"type": "filter_subcategory", "raw_phrase": _extract_raw_phrase(message)}

    # --- Targeted negation refinement (regex — only unambiguous signals) ---
    # Most negation messages ("not the DHS ones", "without referrals") are
    # natural language best handled by the LLM tier below. Only the patterns
    # that are NEVER ambiguous in a post-results context go here.
    _NEGATION_REFINE_RE = re.compile(
        r"\b(exclude \w|anything (?:but|except) \w|everything (?:but|except) \w)\b",
        re.I,
    )
    if _NEGATION_REFINE_RE.search(lower):
        return {
            "type": "filter_subcategory",
            "raw_phrase": _extract_raw_phrase(message),
            "_is_negation": True,
        }

    # --- Tier 2: Ambiguous intent — LLM classification (~100ms) ---
    # Messages that MIGHT be refinements or MIGHT be new requests.
    # A single bounded Haiku call classifies the intent. This avoids
    # both failure modes:
    #   - Broad regex catching new requests as refinements (extra taps)
    #   - Falling through to the ungrounded conversational LLM (hallucination)
    #
    # Only fires when there's enough content to be ambiguous (4+ words)
    # and the message wasn't already handled by the patterns above.
    if len(message.split()) >= 3:
        llm_intent = _classify_post_results_llm(message)
        if llm_intent == "refine":
            return {"type": "filter_subcategory", "raw_phrase": _extract_raw_phrase(message)}
        elif llm_intent == "new_request":
            return None  # escape to main router
        elif llm_intent == "about_results":
            return {"type": "unknown_about_results"}
        # "other" or None (LLM unavailable) → fall through

    # Not a post-results question
    return None


# ---------------------------------------------------------------------------
# POST-RESULTS LLM INTENT CLASSIFICATION
# ---------------------------------------------------------------------------

_POST_RESULTS_CLASSIFY_PROMPT = """\
You are classifying a follow-up message in a social services chatbot. \
The user has already received service results and is now sending another message.

Classify the message into exactly ONE category:

- refine: The user wants to narrow or filter the results they're looking at. \
They're referencing the displayed results and want a subset. \
This includes BOTH positive filters ("only the family ones") AND \
negative filters / exclusions ("not the DHS ones", "without referrals"). \
Examples: "just the family ones", "only show me intake", "the ones for youth", \
"just show me the DHS ones", "only the ones that are open", \
"not the DHS ones", "without referrals", "skip the ones that need ID", \
"don't show me the closed ones", "other than the intake"

- new_request: The user wants to search for a DIFFERENT type of service. \
They're not filtering results — they want something new entirely. \
Examples: "show me food pantries", "I want dental care", \
"what about legal help", "can I get clothing too"

- about_results: The user is asking a question about the displayed results \
but not trying to filter them. \
Examples: "which one is closest", "are any of these safe", \
"what's the difference between them"

- other: None of the above. General conversation, frustration, or unrelated.

Return ONLY the category name. No explanation."""


def _classify_post_results_llm(message: str) -> Optional[str]:
    """Classify a post-results message using a bounded Haiku call.

    Only fires for ambiguous messages that regex couldn't classify.
    Returns one of: "refine", "new_request", "about_results", "other",
    or None if the LLM is unavailable.

    Cost: ~$0.0005 per call. Bounded to 4 possible outputs.
    """
    try:
        from app.llm.claude_client import get_client, CLASSIFICATION_MODEL
        from app.services.audit_log import record_llm_call
        import time

        client = get_client()
        if client is None:
            return None

        t0 = time.perf_counter()
        response = client.messages.create(
            model=CLASSIFICATION_MODEL,
            max_tokens=15,
            system=_POST_RESULTS_CLASSIFY_PROMPT,
            messages=[{"role": "user", "content": message}],
        )
        latency = round((time.perf_counter() - t0) * 1000)

        raw = response.content[0].text.strip().lower()
        record_llm_call(
            task="post_results_classify", model=CLASSIFICATION_MODEL,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency, success=True,
        )

        _VALID = {"refine", "new_request", "about_results", "other"}
        if raw in _VALID:
            logger.info(f"Post-results LLM classified '{message[:50]}' as '{raw}'")
            return raw

        logger.warning(f"Post-results LLM returned unexpected: '{raw}'")
        return None

    except Exception as e:
        logger.error(f"Post-results LLM classification failed: {e}")
        return None


# ---------------------------------------------------------------------------
# TIER 2: LLM KEYWORD EXTRACTION
# ---------------------------------------------------------------------------

_KEYWORD_EXTRACT_PROMPT = """\
You are extracting filter terms from a user's message in a social services chatbot.

The user has already seen a list of services and wants to narrow the results. \
Your job is to identify which taxonomy tags or search keywords best match \
what they're asking for.

Available taxonomy tags in the current results:
{taxonomies}

Available service names:
{service_names}

Rules:
- taxonomy_matches: Return ONLY tags from the "Available taxonomy tags" list above \
that match the user's intent. Empty list if none match.
- keywords: Return 1-3 lowercase search terms to match against service names and \
descriptions. These should be concrete nouns, not verbs or filler.
- Return ONLY valid JSON. No explanation.

Example: User says "the one my case worker mentioned"
Output: {{"taxonomy_matches": ["Case Workers"], "keywords": ["case"]}}

Example: User says "somewhere with a shower"
Output: {{"taxonomy_matches": ["Shower"], "keywords": ["shower"]}}

Example: User says "the DHS one"
Output: {{"taxonomy_matches": [], "keywords": ["dhs"]}}"""


def _extract_keywords_llm(
    raw_phrase: str, services: list[dict]
) -> Optional[dict]:
    """Extract filter keywords using a bounded Haiku call.

    Sends the user's phrase alongside the available taxonomy tags and
    service names from the current results. Returns structured terms
    for taxonomy matching and text search.

    Only fires when Tier 1 (regex) keyword extraction produced no
    matches — typically for indirect language, synonyms, or
    abbreviations that stop-word stripping can't handle.

    Returns:
        {"taxonomies": ["Intake", ...], "keywords": ["intake", ...]}
        or None if LLM is unavailable or response is unparseable.

    Cost: ~$0.001 per call. Bounded to small JSON output.
    """
    try:
        from app.llm.claude_client import get_client, CLASSIFICATION_MODEL
        from app.services.audit_log import record_llm_call
        import time
        import json as _json

        client = get_client()
        if client is None:
            return None

        # Build compact context from current service cards
        all_taxonomies: set[str] = set()
        all_names: list[str] = []
        for card in services:
            tags = card.get("service_taxonomies") or []
            all_taxonomies.update(tags)
            name = card.get("service_name", "")
            if name and name not in all_names:
                all_names.append(name)

        if not all_taxonomies and not all_names:
            return None

        prompt = _KEYWORD_EXTRACT_PROMPT.format(
            taxonomies=", ".join(sorted(all_taxonomies)),
            service_names="\n".join(f"- {n}" for n in all_names[:20]),
        )

        t0 = time.perf_counter()
        response = client.messages.create(
            model=CLASSIFICATION_MODEL,
            max_tokens=100,
            system=prompt,
            messages=[{"role": "user", "content": raw_phrase}],
        )
        latency = round((time.perf_counter() - t0) * 1000)

        raw = response.content[0].text.strip()
        record_llm_call(
            task="filter_keyword_extract", model=CLASSIFICATION_MODEL,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency, success=True,
        )

        # Parse JSON — strip markdown fences if present
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = _json.loads(cleaned)

        # Validate structure
        result = {}
        if isinstance(parsed.get("taxonomy_matches"), list):
            # Only keep taxonomies that actually exist in the card data
            valid = [t for t in parsed["taxonomy_matches"] if t in all_taxonomies]
            if valid:
                result["taxonomies"] = valid
        if isinstance(parsed.get("keywords"), list):
            # Lowercase, non-empty strings only
            valid = [str(k).lower().strip() for k in parsed["keywords"] if str(k).strip()]
            if valid:
                result["keywords"] = valid[:3]

        if result:
            logger.info(
                f"LLM keyword extraction: phrase='{raw_phrase[:50]}' "
                f"taxonomies={result.get('taxonomies', [])} "
                f"keywords={result.get('keywords', [])} "
                f"latency={latency}ms"
            )
            return result

        logger.info(f"LLM keyword extraction returned empty for '{raw_phrase[:50]}'")
        return None

    except Exception as e:
        logger.error(f"LLM keyword extraction failed: {e}")
        return None


def _extract_service_index(text: str) -> Optional[int]:
    """Extract a service index from ordinals or numbers."""
    m = _SPECIFIC_INDEX_RE.search(text)
    if not m:
        return None
    ordinal = m.group(1)
    if ordinal:
        return _ORDINAL_MAP.get(ordinal.lower())
    # Groups 2, 3, 4 are all digit captures from different patterns
    for g in (2, 3, 4):
        if m.group(g):
            return int(m.group(g)) - 1  # Convert 1-based to 0-based
    return None


# ---------------------------------------------------------------------------
# ANSWER BUILDER
# ---------------------------------------------------------------------------

# Quick replies shown after post-results answers
_NAVIGATOR_QR = {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"}
_NEW_SEARCH_QR = {"label": "🔍 New search", "value": "Start over"}
_SHOW_ALL_QR = {"label": "📋 Show all results", "value": "Show all results"}


def _call_qr(service: dict) -> dict:
    """Build a 'Call [name]' quick reply for a specific service.

    Includes an href field with a tel: link so the frontend renders it
    as an <a> tag that triggers the native phone dialer (mobile) or
    calling app prompt (desktop).
    """
    name = service.get("service_name", "the service")
    phone = service.get("phone", "")
    short_name = name[:25] + "…" if len(name) > 25 else name
    # Strip non-digits for the tel: href
    digits = re.sub(r"\D", "", phone)
    return {
        "label": f"📞 Call {short_name}",
        "value": f"Call {phone}",
        "href": f"tel:{digits}" if digits else None,
    }


def _default_qr(services: list) -> list:
    """Standard quick replies after a post-results answer."""
    return [_NAVIGATOR_QR, _NEW_SEARCH_QR]


def answer_from_results(intent: dict, services: list[dict]) -> dict:
    """Build an answer from stored service card data.

    Args:
        intent: From classify_post_results_question()
        services: The list of service cards last shown to the user

    Returns:
        {
            "response": str,
            "services": list[dict],  # subset to (re-)display, or []
            "quick_replies": list[dict],
            "category": str,         # for audit log
        }
    """
    if not services:
        return _cant_answer("I don't have any results to reference.", [])

    intent_type = intent.get("type")

    if intent_type == "filter_open":
        return _handle_filter_open(services)
    elif intent_type == "filter_free":
        return _handle_filter_free(services)
    elif intent_type == "filter_subcategory":
        return _handle_filter_subcategory(intent, services)
    elif intent_type == "refine_results":
        # Legacy compat — treat as filter_subcategory with no raw_phrase
        return _handle_filter_subcategory(
            {"type": "filter_subcategory", "raw_phrase": ""}, services
        )
    elif intent_type == "specific_index":
        return _handle_specific_index(intent["index"], services)
    elif intent_type == "specific_name":
        return _handle_specific_name(intent["query"], services)
    elif intent_type == "ask_field":
        return _handle_ask_field(intent["field"], services)
    else:
        return _cant_answer(
            "I only have the information shown on the service cards. "
            "For more details, I'd recommend calling the service directly "
            "or connecting with a peer navigator who can help.",
            services,
        )


# ---------------------------------------------------------------------------
# INTENT HANDLERS
# ---------------------------------------------------------------------------

def _handle_filter_open(services: list[dict]) -> dict:
    """Filter services to those currently open."""
    open_services = [s for s in services if s.get("is_open") == "open"]

    if open_services:
        count = len(open_services)
        return {
            "response": (
                f"{count} of the {len(services)} results "
                f"{'is' if count == 1 else 'are'} currently open:"
            ),
            "services": open_services,
            "quick_replies": [_SHOW_ALL_QR, _NAVIGATOR_QR, _NEW_SEARCH_QR],
            "category": "post_results",
        }

    # Check if any have schedule data at all
    has_hours = [s for s in services if s.get("hours_today")]
    if has_hours:
        closed_info = _format_hours_summary(has_hours)
        return {
            "response": (
                f"None of the results are open right now. "
                f"Here's what I know about their hours:\n\n{closed_info}\n\n"
                f"For the others, I don't have confirmed hours — "
                f"I'd recommend calling ahead to check."
            ),
            "services": [],
            "quick_replies": [_NAVIGATOR_QR, _NEW_SEARCH_QR],
            "category": "post_results",
        }

    return {
        "response": (
            "I don't have confirmed hours for any of these services. "
            "I'd recommend calling ahead to check if they're open, "
            "or a peer navigator can help you find out."
        ),
        "services": [],
        "quick_replies": [_NAVIGATOR_QR, _NEW_SEARCH_QR],
        "category": "post_results",
    }


def _handle_filter_free(services: list[dict]) -> dict:
    """Filter services to those that are free."""
    free_services = [
        s for s in services
        if s.get("fees") and "free" in s["fees"].lower()
    ]

    if free_services:
        count = len(free_services)
        return {
            "response": (
                f"{count} of the {len(services)} results "
                f"{'is' if count == 1 else 'are'} listed as free:"
            ),
            "services": free_services,
            "quick_replies": [_SHOW_ALL_QR, _NAVIGATOR_QR, _NEW_SEARCH_QR],
            "category": "post_results",
        }

    # Check if any have fee info at all
    has_fees = [s for s in services if s.get("fees")]
    if has_fees:
        fee_info = "\n".join(
            f"• {s['service_name']}: {s['fees']}"
            for s in has_fees
        )
        return {
            "response": (
                f"Here's what I know about fees:\n\n{fee_info}\n\n"
                f"For the others, I don't have fee information. "
                f"You could call to ask, or connect with a peer navigator."
            ),
            "services": [],
            "quick_replies": [_NAVIGATOR_QR, _NEW_SEARCH_QR],
            "category": "post_results",
        }

    return {
        "response": (
            "I don't have fee information for these services. "
            "Many social services in NYC are free — "
            "I'd recommend calling to confirm, or a peer navigator can help."
        ),
        "services": [],
        "quick_replies": [_NAVIGATOR_QR, _NEW_SEARCH_QR],
        "category": "post_results",
    }


def _handle_filter_subcategory(intent: dict, services: list[dict]) -> dict:
    """Filter displayed results by sub-category.

    Three-tier deterministic filtering pipeline:
      Tier 1a: Structured field filters (open, referral, population)
      Tier 1a: Taxonomy tag matching (service_taxonomies)
      Tier 1a: Co-located service filter (also_available)
      Tier 1c: Weighted text search (service_name + description)

    All filtering operates on DB-sourced card data. Zero hallucination.
    The LLM is never used to select or rank services.
    """
    raw_phrase = intent.get("raw_phrase", "")
    original_message = intent.get("_original_message", raw_phrase)
    total = len(services)

    if total <= 2:
        return {
            "response": (
                f"I only found {total} result{'s' if total != 1 else ''} for "
                f"this search, so there isn't much to filter. You can tap on "
                f"the card{'s' if total != 1 else ''} for more details, or I "
                f"can try a new search."
            ),
            "services": [],
            "quick_replies": [_SHOW_ALL_QR, _NAVIGATOR_QR, _NEW_SEARCH_QR],
            "category": "post_results_filter",
        }

    # --- Detect negation ---
    is_negation = (
        intent.get("_is_negation", False)
        or bool(_NEGATION_RE.search(original_message))
    )

    # --- Tier 1a: Structured field filters ---
    matched, filter_desc = _filter_by_structured(services, original_message)
    filter_tier = "structured"

    # --- Tier 1a: Co-located service filter ---
    if not matched:
        matched, filter_desc = _filter_by_colocated(services, original_message)
        filter_tier = "colocated"

    # --- Tier 1a: Taxonomy tag matching ---
    if not matched and raw_phrase:
        matched, matched_taxonomies = _filter_by_taxonomy(services, raw_phrase)
        if matched:
            filter_tier = "taxonomy"
            filter_desc = ", ".join(sorted(matched_taxonomies))

    # --- Tier 1c: Weighted text search ---
    if not matched and raw_phrase:
        keywords = _extract_keywords(raw_phrase)
        if keywords:
            matched = _text_search_cards(services, keywords)
            if matched:
                filter_tier = "text_search"
                filter_desc = " ".join(keywords)

    # --- Tier 2: LLM keyword extraction ---
    # When regex-based extraction misses (indirect language, synonyms,
    # abbreviations), ask Haiku to map the user's phrase to taxonomy
    # names and search keywords using the actual card data as context.
    if not matched and raw_phrase:
        llm_result = _extract_keywords_llm(raw_phrase, services)
        if llm_result:
            # Try taxonomy match with LLM-extracted terms
            if llm_result.get("taxonomies"):
                llm_tax = set(llm_result["taxonomies"])
                matched = [
                    c for c in services
                    if c.get("service_taxonomies")
                    and any(t in c["service_taxonomies"] for t in llm_tax)
                ]
                if matched:
                    filter_tier = "llm_taxonomy"
                    filter_desc = ", ".join(sorted(llm_tax))

            # Try text search with LLM-extracted keywords
            if not matched and llm_result.get("keywords"):
                matched = _text_search_cards(services, llm_result["keywords"])
                if matched:
                    filter_tier = "llm_text_search"
                    filter_desc = " ".join(llm_result["keywords"])

    # --- Apply negation ---
    if is_negation and matched:
        # Invert: show everything EXCEPT matched
        matched_ids = {c.get("service_id") for c in matched}
        matched = [c for c in services if c.get("service_id") not in matched_ids]
        filter_tier += "_negated"

    # --- Log filter operation ---
    logger.info(
        f"filter_subcategory: tier={filter_tier} phrase='{raw_phrase}' "
        f"matched={len(matched)}/{total} negated={is_negation}"
    )

    # --- Build response ---
    if matched:
        count = len(matched)
        display_phrase = raw_phrase[:50] if raw_phrase else filter_desc

        if is_negation:
            response = (
                f"Here {'is' if count == 1 else 'are'} {count} of the "
                f"{total} results excluding '{display_phrase}':"
            )
        elif count == 1:
            response = (
                f"One of the {total} results matches '{display_phrase}':"
            )
        else:
            response = (
                f"I found {count} of the {total} results matching "
                f"'{display_phrase}':"
            )

        return {
            "response": response,
            "services": matched,
            "quick_replies": [_SHOW_ALL_QR, _NAVIGATOR_QR, _NEW_SEARCH_QR],
            "category": "post_results_filter",
            # Metadata for chatbot.py to store _filtered_results
            "_filter_matched": True,
            "_filter_tier": filter_tier,
            "_filter_phrase": raw_phrase,
        }

    # --- No matches ---
    display_phrase = raw_phrase[:50] if raw_phrase else "that"
    return {
        "response": (
            f"None of the {total} results I showed match "
            f"'{display_phrase}'. This might mean the specific "
            f"service you're looking for isn't in my current results. "
            f"Would you like to try a new search, or would a peer "
            f"navigator be helpful?"
        ),
        "services": [],
        "quick_replies": [_SHOW_ALL_QR, _NEW_SEARCH_QR, _NAVIGATOR_QR],
        "category": "post_results_filter",
        "_filter_matched": False,
        "_filter_tier": filter_tier,
        "_filter_phrase": raw_phrase,
    }


def _handle_specific_index(index: int, services: list[dict]) -> dict:
    """Show detail view for a service by index."""
    if index == -1:
        index = len(services) - 1

    if index < 0 or index >= len(services):
        return {
            "response": (
                f"I showed {len(services)} result(s). "
                f"Which one would you like to know more about?"
            ),
            "services": [],
            "quick_replies": _numbered_qrs(services) + [_NEW_SEARCH_QR],
            "category": "post_results",
        }

    return _service_detail_response(services[index], services)


def _handle_specific_name(query: str, services: list[dict]) -> dict:
    """Show detail view for a service matched by name."""
    query_lower = query.lower().rstrip("?.,!;:")

    # Try exact substring match on service name or organization
    matches = [
        s for s in services
        if query_lower in (s.get("service_name") or "").lower()
        or query_lower in (s.get("organization") or "").lower()
    ]

    if len(matches) == 1:
        return _service_detail_response(matches[0], services)

    if len(matches) > 1:
        names = "\n".join(
            f"• {s['service_name']} ({s.get('organization', 'unknown org')})"
            for s in matches
        )
        return {
            "response": f"I found a few matches:\n\n{names}\n\nWhich one?",
            "services": matches,
            "quick_replies": _numbered_qrs(matches) + [_NEW_SEARCH_QR],
            "category": "post_results",
        }

    # No match — try fuzzier matching (first word)
    first_word = query_lower.split()[0] if query_lower.split() else ""
    fuzzy = [
        s for s in services
        if first_word and first_word in (s.get("service_name") or "").lower()
    ]
    if len(fuzzy) == 1:
        return _service_detail_response(fuzzy[0], services)

    return None  # No match — fall through to normal routing


def _handle_ask_field(field: str, services: list[dict]) -> dict:
    """Answer a question about a specific field across all results."""
    field_map = {
        "hours": ("hours_today", "hours"),
        "address": ("address", "address"),
        "phone": ("phone", "phone number"),
        "website": ("website", "website"),
    }

    key, label = field_map.get(field, (field, field))

    entries = []
    for s in services:
        value = s.get(key)
        name = s.get("service_name", "Unknown")
        if value:
            entries.append(f"• {name}: {value}")
        else:
            entries.append(f"• {name}: not available")

    summary = "\n".join(entries)
    has_data = any(s.get(key) for s in services)

    if has_data:
        response = f"Here's the {label} info I have:\n\n{summary}"
    else:
        response = (
            f"I don't have {label} information for any of these services. "
            f"A peer navigator can help you find this out, "
            f"or you can visit the service's YourPeer page for more details."
        )

    return {
        "response": response,
        "services": [],
        "quick_replies": _default_qr(services) + ([_SHOW_ALL_QR] if has_data else []),
        "category": "post_results",
    }


# ---------------------------------------------------------------------------
# DETAIL VIEW
# ---------------------------------------------------------------------------

def _service_detail_response(service: dict, all_services: list[dict]) -> dict:
    """Build a detailed view of a single service from card data only."""
    name = service.get("service_name", "Unknown Service")
    org = service.get("organization")
    lines = [f"Here's what I know about {name}:"]

    if org:
        lines.append(f"Organization: {org}")

    # Status
    status = service.get("is_open")
    hours = service.get("hours_today")
    if status == "open" and hours:
        lines.append(f"Status: Open now ({hours})")
    elif status == "closed" and hours:
        lines.append(f"Status: Closed (hours today: {hours})")
    elif hours:
        lines.append(f"Hours today: {hours}")
    else:
        lines.append("Hours: not available — call to check")

    if service.get("address"):
        lines.append(f"Address: {service['address']}")
    if service.get("phone"):
        lines.append(f"Phone: {service['phone']}")
    if service.get("email"):
        lines.append(f"Email: {service['email']}")
    if service.get("website"):
        lines.append(f"Website: {service['website']}")
    if service.get("fees"):
        lines.append(f"Fees: {service['fees']}")
    if service.get("description"):
        lines.append(f"Description: {service['description']}")
    if service.get("requires_membership"):
        lines.append("Note: Referral may be required")

    # Co-located services
    also = service.get("also_available")
    if also and len(also) > 0:
        lines.append(f"\nAlso available here: {', '.join(also)}")

    lines.append(
        "\nThat's all I have in my records. For anything else, "
        "you could call them directly or connect with a peer navigator."
    )

    qrs = []
    if service.get("phone"):
        qrs.append(_call_qr(service))
    qrs.extend([_NAVIGATOR_QR, _SHOW_ALL_QR, _NEW_SEARCH_QR])

    return {
        "response": "\n".join(lines),
        "services": [service],
        "quick_replies": qrs,
        "category": "post_results",
    }


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _cant_answer(message: str, services: list[dict]) -> dict:
    """Response for questions we can't answer from the data."""
    qrs = [_NAVIGATOR_QR, _NEW_SEARCH_QR]
    return {
        "response": message,
        "services": [],
        "quick_replies": qrs,
        "category": "post_results",
    }


def _format_hours_summary(services: list[dict]) -> str:
    """Format hours for services that have schedule data."""
    lines = []
    for s in services:
        name = s.get("service_name", "Unknown")
        hours = s.get("hours_today", "unknown")
        status = s.get("is_open", "unknown")
        if status == "closed":
            lines.append(f"• {name}: {hours} (closed now)")
        elif status == "open":
            lines.append(f"• {name}: {hours} (open now)")
        else:
            lines.append(f"• {name}: {hours}")
    return "\n".join(lines)


def _call_qrs(services: list[dict], max_buttons: int = 2) -> list[dict]:
    """Build 'Call X' quick replies for services with phone numbers.

    Deduplicates by phone number — if two services share a number,
    only one call button is shown.
    """
    seen_phones: set[str] = set()
    unique: list[dict] = []
    for s in services:
        phone = s.get("phone")
        if phone and phone not in seen_phones:
            seen_phones.add(phone)
            unique.append(s)
    return [_call_qr(s) for s in unique[:max_buttons]]


def _numbered_qrs(services: list[dict], max_buttons: int = 5) -> list[dict]:
    """Build numbered quick replies for service selection."""
    qrs = []
    for i, s in enumerate(services[:max_buttons]):
        name = s.get("service_name", "Unknown")
        short = name[:20] + "…" if len(name) > 20 else name
        qrs.append({"label": f"{i+1}. {short}", "value": f"Tell me about number {i+1}"})
    return qrs
