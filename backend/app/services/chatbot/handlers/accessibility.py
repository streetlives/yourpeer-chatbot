"""Accessibility handlers: Spanish detection, demographic skip, location unknown.

SAMHSA cultural-humility + empowerment principles: acknowledge language
gaps rather than returning silence; let users decline to share
demographic info without penalty; offer a location picker when the user
doesn't know where they are.
"""

import re

from app.services.confirmation import (
    _build_confirmation_message,
    _confirmation_quick_replies,
)
from app.services.session_store import save_session_slots

from ..context import _empty_reply
from ..logging import _log_turn


# Phrases that count as a user declining to share demographic info.
_DEMOGRAPHIC_SKIP_PHRASES = (
    "i'd rather not say", "id rather not say", "i would rather not say",
    "rather not say", "prefer not to say", "prefer not to",
    "skip", "skip this", "don't want to say", "dont want to say",
    "none of your business", "that's personal", "thats personal",
    "pass",
)

# Phrases indicating the user doesn't know their location (triggers
# the location-picker fallback).
_LOCATION_UNKNOWN_PHRASES = (
    "i don't know", "i dont know", "idk", "not sure", "i'm not sure",
    "im not sure", "no idea", "don't know", "dont know",
    "i don't know where i am", "i dont know where i am",
    "not sure where i am", "don't know where i am",
    "dont know where i am", "no clue",
    "anywhere", "wherever", "doesn't matter", "doesnt matter",
    "it doesn't matter", "it doesnt matter",
    "where i am",
)
_LOCATION_UNKNOWN_EXACT = ("here", "right here")


# Common Spanish phrases that should trigger a bilingual acknowledgment.
# SAMHSA Cultural Humility: acknowledge the language gap rather than
# returning silence or an English-only response.
_SPANISH_RE = re.compile(
    r"\b(necesito|ayuda|comida|refugio|albergue|por favor|"
    r"no hablo ingles|no hablo inglés|hola|buenos dias|"
    r"buenas tardes|buenas noches|tengo hambre|"
    r"necesito ayuda|donde puedo|dónde puedo)\b", re.I,
)


def _handle_demographic_skip(session_id, message, redacted_message, existing,
                             tone, request_id):
    """If a demographic question is pending and the user declined, mark the
    slots "skipped" and proceed to confirmation.

    Returns a result dict if the skip pattern fired, None otherwise.
    """
    skip_lower = message.lower().strip()
    is_skip = (
        any(p in skip_lower for p in _DEMOGRAPHIC_SKIP_PHRASES)
        or skip_lower in ("skip", "pass")
    )
    is_demographic_pending = (
        existing.get("service_type")
        and existing.get("location")
        and not existing.get("_pending_confirmation")
        and (not existing.get("age") or not existing.get("family_status"))
    )
    if not (is_skip and is_demographic_pending):
        return None

    # Mark skipped demographics so we don't re-ask
    if not existing.get("age"):
        existing["age"] = "skipped"
    if not existing.get("family_status"):
        existing["family_status"] = "skipped"
    save_session_slots(session_id, existing)

    # Proceed to confirmation with what we have
    existing["_pending_confirmation"] = True
    save_session_slots(session_id, existing)
    confirm_msg = "No problem at all. " + _build_confirmation_message(existing)
    result = {
        "session_id": session_id,
        "response": confirm_msg,
        "follow_up_needed": True,
        "slots": existing,
        "services": [],
        "result_count": 0,
        "relaxed_search": False,
        "quick_replies": _confirmation_quick_replies(existing),
    }
    _log_turn(session_id, redacted_message, result, "demographic_skip",
              request_id=request_id, tone=tone)
    return result


def _handle_location_unknown(session_id, message, redacted_message, existing,
                             tone, request_id):
    """If the user has a service_type but no location, and replied with an
    "I don't know" phrase, offer the geolocation+borough picker.

    Returns a result dict if the pattern fired, None otherwise.
    """
    msg_lower = message.lower().strip()
    is_location_unknown = (
        any(p in msg_lower for p in _LOCATION_UNKNOWN_PHRASES)
        or msg_lower in _LOCATION_UNKNOWN_EXACT
    )
    needs_location_picker = (
        existing.get("service_type")
        and not existing.get("location")
        and not existing.get("_pending_confirmation")
        and is_location_unknown
    )
    if not needs_location_picker:
        return None

    result = _empty_reply(
        session_id,
        "No problem! You can share your location and I'll find what's "
        "nearby, or pick a borough:",
        existing,
        quick_replies=[
            {"label": "📍 Use my location", "value": "__use_geolocation__"},
            {"label": "Manhattan", "value": "Manhattan"},
            {"label": "Brooklyn", "value": "Brooklyn"},
            {"label": "Queens", "value": "Queens"},
            {"label": "Bronx", "value": "Bronx"},
            {"label": "Staten Island", "value": "Staten Island"},
        ],
    )
    _log_turn(session_id, redacted_message, result, "location_unknown",
              request_id=request_id, tone=tone)
    return result


def _handle_spanish_detection(session_id, message, redacted_message, existing,
                              has_service_intent, tone, request_id):
    """Detect Spanish phrases and either return a bilingual response outright
    (Spanish-only message) or return an acknowledgment prefix to prepend to
    the downstream response (Spanish + service request).

    Returns (result, acknowledgment_prefix) where exactly one is non-empty:
      - result is non-None when we've fully answered (no fallthrough needed)
      - acknowledgment_prefix is non-empty when the caller should continue
        processing and prepend the prefix to its eventual response
    """
    if not _SPANISH_RE.search(message):
        return None, ""

    if not has_service_intent:
        # Spanish only, no service request — return bilingual message
        result = _empty_reply(
            session_id,
            "I'm sorry — right now I can only help in English. "
            "A peer navigator may be able to help in Spanish.\n\n"
            "Lo siento — por ahora solo puedo ayudar en inglés. "
            "Un navegador comunitario puede ayudarte en español.",
            existing,
            quick_replies=[
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
            ],
        )
        _log_turn(session_id, redacted_message, result, "spanish_detected",
                  request_id=request_id, tone=tone)
        return result, ""

    # Spanish + service intent — process normally with a bilingual prefix
    acknowledgment = (
        "I can see you may prefer Spanish — lo siento, por ahora "
        "solo puedo ayudar en inglés. I'll do my best to help.\n\n"
    )
    return None, acknowledgment
