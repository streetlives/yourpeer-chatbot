"""
Confirmation and results presentation for the YourPeer chatbot.

Handles:
  - Building human-readable confirmation messages from filled slots
  - Quick-reply button sets for confirmation and follow-up steps
  - "No results" messages with borough suggestions
  - Borough suggestion logic (service-type aware)
"""

from app.services.slot_extractor import NEAR_ME_SENTINEL
from app.privacy.pii_redactor import redact_pii
from app.services.phrase_lists import (
    _SERVICE_LABELS,
    _WELCOME_QUICK_REPLIES,
    _NEARBY_BOROUGHS_BY_SERVICE,
    _NEARBY_BOROUGHS_DEFAULT,
    _SERVICE_TO_BOROUGH_KEY,
)


# ---------------------------------------------------------------------------
# CONFIRMATION MESSAGE
# ---------------------------------------------------------------------------

def _build_confirmation_message(slots: dict) -> str:
    """Build a human-readable confirmation prompt from filled slots.

    Slot values are redacted before echoing to prevent PII leakage
    (e.g. a street address captured as a location).
    """
    service = slots.get("service_type", "services")
    # Prefer the specific sub-type label (e.g. "dental care") over the
    # generic category label (e.g. "health care") when available.
    service_label = slots.get("service_detail") or _SERVICE_LABELS.get(service, service)
    location = slots.get("location", "your area")

    # Organization name search: different message format
    org_name = slots.get("org_name")
    if org_name:
        if slots.get("service_type"):
            msg = f"Does this look right? {service_label} at {org_name}"
        else:
            msg = f"Does this look right? Services at {org_name}"
        if location and location != "your area" and location != NEAR_ME_SENTINEL:
            location_clean, _ = redact_pii(location)
            msg += f" in {location_clean}"
        msg += "."
        return msg
    age = slots.get("age")
    if age == "skipped":
        age = None

    # When using browser geolocation, show "near your location"
    # instead of the raw "__near_me__" sentinel.
    if (
        location == NEAR_ME_SENTINEL
        and slots.get("_latitude") is not None
    ):
        location = "near your location"
    else:
        # Redact any PII that may have been captured in slot values
        location, _ = redact_pii(location)

    # "near your location" reads naturally without "in", but borough/
    # neighborhood names need "in" ("in Brooklyn", "in Harlem").
    if location.startswith("near "):
        location_phrase = location
    else:
        location_phrase = f"in {location}"

    # Build the service label, including co-located services
    queued = slots.get("_queued_services", [])
    if queued:
        co_labels = [
            (q[1] if len(q) > 1 and q[1] else None) or _SERVICE_LABELS.get(q[0], q[0])
            for q in queued
        ]
        all_labels = [service_label] + co_labels
        if len(all_labels) == 2:
            service_label = f"{all_labels[0]} and {all_labels[1]}"
        else:
            service_label = ", ".join(all_labels[:-1]) + f", and {all_labels[-1]}"

    parts = [f"Does this look right? {service_label} {location_phrase}"]
    if age:
        parts[0] += f" (age {age})"

    # --- Identity-aware prefixes ---
    # Build a prefix for the service label based on the user's identity
    # context. Applied ONCE to avoid fragile str.replace() chains.
    # Priority order: LGBTQ (from _gender) > population (from _populations).
    # Only one prefix is shown to keep the message concise.
    _prefix = ""
    gender = slots.get("_gender")
    populations = slots.get("_populations", [])

    if gender == "lgbtq":
        _prefix = "LGBTQ-friendly "
    elif "veteran" in populations:
        _prefix = "veteran-friendly "
    elif "disabled" in populations:
        _prefix = "accessible "
    elif "reentry" in populations:
        _prefix = "reentry-friendly "
    elif "senior" in populations and not age:
        # Only show if age wasn't stated (otherwise "(age 65)" covers it)
        _prefix = "senior-friendly "

    if _prefix:
        parts[0] = parts[0].replace(
            f"Does this look right? {service_label}",
            f"Does this look right? {_prefix}{service_label}",
        )

    family = slots.get("family_status")
    if family == "with_children":
        parts[0] += ", with children"
    elif family == "with_family":
        parts[0] += ", with family"
    elif family == "alone":
        parts[0] += ", for yourself"

    parts[0] += "."

    return " ".join(parts)


# ---------------------------------------------------------------------------
# QUICK REPLY BUILDERS
# ---------------------------------------------------------------------------

def _confirmation_quick_replies(slots: dict) -> list:
    """Quick-reply buttons for the confirmation step."""
    return [
        {"label": "✅ Yes, search", "value": "Yes, search"},
        {"label": "📍 Change location", "value": "Change location"},
        {"label": "🔄 Change service", "value": "Change service"},
        {"label": "❌ Start over", "value": "Start over"},
    ]


def _follow_up_quick_replies(slots: dict) -> list:
    """Quick-reply buttons for follow-up questions (when missing slots)."""
    if not slots.get("service_type"):
        return list(_WELCOME_QUICK_REPLIES)

    # Missing location — suggest common boroughs + geolocation option
    if not slots.get("location") or slots.get("location") == NEAR_ME_SENTINEL:
        return [
            {"label": "📍 Use my location", "value": "__use_geolocation__"},
            {"label": "Manhattan", "value": "Manhattan"},
            {"label": "Brooklyn", "value": "Brooklyn"},
            {"label": "Queens", "value": "Queens"},
            {"label": "Bronx", "value": "Bronx"},
            {"label": "Staten Island", "value": "Staten Island"},
        ]

    # Missing age (shelter only) — offer skip option
    if slots.get("service_type") == "shelter" and not slots.get("age"):
        return [
            {"label": "⏭️ Skip this", "value": "I'd rather not say"},
        ]

    # Missing family status (shelter only) — offer skip + options
    if slots.get("service_type") == "shelter" and not slots.get("family_status"):
        return [
            {"label": "🧑 On my own", "value": "On my own"},
            {"label": "👨‍👩‍👦 With children", "value": "With children"},
            {"label": "👥 With others", "value": "With family"},
            {"label": "⏭️ Skip this", "value": "I'd rather not say"},
        ]

    return []


# ---------------------------------------------------------------------------
# BOROUGH SUGGESTIONS
# ---------------------------------------------------------------------------

def _get_nearby_boroughs(service_type: str | None, borough: str) -> list[str]:
    """Return the best nearby boroughs to suggest for a given service + borough combo."""
    service_key = _SERVICE_TO_BOROUGH_KEY.get((service_type or "").lower())
    if service_key and service_key in _NEARBY_BOROUGHS_BY_SERVICE:
        return _NEARBY_BOROUGHS_BY_SERVICE[service_key].get(borough, [])
    return _NEARBY_BOROUGHS_DEFAULT.get(borough, [])


def _no_results_message(slots: dict) -> str:
    """Helpful message when no services match the query."""
    service = slots.get("service_type", "services")
    location = slots.get("location", "your area")

    from app.rag.query_executor import normalize_location, is_borough
    normalized = normalize_location(location) if location else None

    # Only suggest nearby boroughs if the user searched at the borough level
    nearby = []
    if normalized and is_borough(location):
        nearby = _get_nearby_boroughs(service, normalized)

    parts = [
        f"I wasn't able to find {service} services in {location} "
        f"matching your criteria."
    ]

    if nearby:
        nearby_str = " or ".join(nearby[:2])
        parts.append(f"Would you like me to try {nearby_str} instead?")
    else:
        parts.append("You could try a different neighborhood or borough.")

    parts.append(
        'You can also say "connect with peer navigator" to talk to a real person.'
    )

    return " ".join(parts)
