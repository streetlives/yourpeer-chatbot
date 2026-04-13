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
        r"do you have|is there)\b", re.I
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

    # Not a post-results question
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
