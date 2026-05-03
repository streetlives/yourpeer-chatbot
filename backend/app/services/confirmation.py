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
# LOCATION DISPLAY FORMATTING
# ---------------------------------------------------------------------------
# _KNOWN_LOCATIONS stores names lowercase for matching. This formatter
# converts them to proper display casing for user-facing messages.

_LOCATION_DISPLAY_OVERRIDES = {
    "soho": "SoHo",
    "noho": "NoHo",
    "nolita": "NoLiTa",
    "dumbo": "DUMBO",
    "tribeca": "TriBeCa",
    "bed-stuy": "Bed-Stuy",
    "bedford-stuyvesant": "Bedford-Stuyvesant",
    "nycha": "NYCHA",
    "the bronx": "the Bronx",
}


def _display_location(location: str) -> str:
    """Convert a lowercase location to proper display casing.

    Handles NYC-specific casing (SoHo, DUMBO, NoHo) and falls
    back to title case for standard names.

    PII redaction markers ([ADDRESS], [PHONE], [NAME], [EMAIL], [SSN])
    pass through unchanged. They're already in canonical UI form
    (all-caps inside square brackets) — .title() would corrupt them
    to "[Address]", "[Phone]", etc., which breaks the downstream
    convention that callers grep for the exact uppercase token.
    """
    if not location:
        return location
    # Redaction markers: any [ALLCAPS] token. Preserve as-is.
    if (
        location.startswith("[")
        and location.endswith("]")
        and location[1:-1].isupper()
    ):
        return location
    override = _LOCATION_DISPLAY_OVERRIDES.get(location.lower())
    if override:
        return override
    return location.title()


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
            msg = f"I\u2019ll look for {service_label} at {org_name}"
        else:
            msg = f"I\u2019ll look for services at {org_name}"
        if location and location != "your area" and location != NEAR_ME_SENTINEL:
            location_clean, _ = redact_pii(location)
            msg += f" in {_display_location(location_clean)}"
        msg += " \u2014 does that sound right?"
        return msg
    age = slots.get("age")
    if age == "skipped":
        age = None

    # Capture raw primary location (pre-display) for same/cross-location
    # queue comparison below. The redacted/display-cased `location` that
    # follows is for rendering, not matching.
    raw_primary_location = slots.get("location")

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
        # Proper display casing (lowercase "manhattan" → "Manhattan")
        location = _display_location(location)

    # "near your location" reads naturally without "in", but borough/
    # neighborhood names need "in" ("in Brooklyn", "in Harlem").
    if location.startswith("near "):
        location_phrase = location
    else:
        location_phrase = f"in {location}"

    # Build the service label, including co-located services.
    # Partition queued services by whether their location matches the
    # primary's. Same-location items fold into the combined label ("food
    # and showers in Brooklyn"). Cross-location items are mentioned
    # separately as ", then X in Y" — see R34 Diagnosis 2, Bug 1.
    # Without this split, "food in Brooklyn and shelter in Manhattan"
    # rendered as "shelter and food in Manhattan" — dropping Brooklyn.
    queued = slots.get("_queued_services", [])
    same_location_queued = []
    cross_location_queued = []
    for q in queued:
        q_loc = q[2] if len(q) > 2 else None
        # A queued item is same-location if it has no location of its
        # own OR its location matches the primary's (case-insensitive).
        if (not q_loc) or (
            raw_primary_location
            and q_loc.lower() == str(raw_primary_location).lower()
        ):
            same_location_queued.append(q)
        else:
            cross_location_queued.append(q)

    if same_location_queued:
        co_labels = [
            (q[1] if len(q) > 1 and q[1] else None) or _SERVICE_LABELS.get(q[0], q[0])
            for q in same_location_queued
        ]
        all_labels = [service_label] + co_labels
        if len(all_labels) == 2:
            service_label = f"{all_labels[0]} and {all_labels[1]}"
        else:
            service_label = ", ".join(all_labels[:-1]) + f", and {all_labels[-1]}"

    parts = [f"I\u2019ll look for {service_label} {location_phrase}"]
    if age:
        parts[0] += f" (age {age})"

    # --- Identity-aware prefixes ---
    _prefix = ""
    gender = slots.get("_gender")
    populations = slots.get("_populations", [])

    # LGBTQ context can land in either slot: _gender="lgbtq" when the
    # phrase itself is a gender/orientation word ("bisexual", "lesbian"),
    # or _populations=["lgbtq"] when the phrase sets a broader population
    # tag ("queer", "transman" — the latter also sets _gender=male, which
    # makes checking both fields essential). Check both so that neither
    # extraction path silently loses the LGBTQ-friendly affirmation.
    if gender == "lgbtq" or "lgbtq" in populations:
        _prefix = "LGBTQ-friendly "
    elif "veteran" in populations:
        _prefix = "veteran-friendly "
    elif "disabled" in populations:
        _prefix = "accessible "
    elif "immigration" in populations:
        # "immigration-friendly" labels services that work with
        # immigration-status-related needs (undocumented, asylum, etc.).
        # Placed above reentry in the elif chain because the LLM
        # historically conflated undocumented with reentry — checking
        # immigration first ensures the correct label wins on the rare
        # case both tags are set.
        _prefix = "immigration-friendly "
    elif "reentry" in populations:
        _prefix = "reentry-friendly "
    elif "foster_youth" in populations:
        _prefix = "youth-friendly "
    elif "senior" in populations and not age:
        # Only show if age wasn't stated (otherwise "(age 65)" covers it)
        _prefix = "senior-friendly "

    if _prefix:
        parts[0] = parts[0].replace(
            f"I\u2019ll look for {service_label}",
            f"I\u2019ll look for {_prefix}{service_label}",
        )

    # Append cross-location queued services as ", then X in Y" — placed
    # before family_status so "with children" applies to the whole
    # multi-search rather than just the last mentioned location.
    if cross_location_queued:
        cross_bits = []
        for q in cross_location_queued:
            q_service = q[0]
            q_detail = q[1] if len(q) > 1 else None
            q_loc = q[2] if len(q) > 2 else None
            q_label = q_detail or _SERVICE_LABELS.get(q_service, q_service)
            # Redact + display-case the queued location
            q_loc_clean, _ = redact_pii(q_loc) if q_loc else (q_loc, None)
            q_loc_display = _display_location(q_loc_clean) if q_loc_clean else ""
            if q_loc_display:
                cross_bits.append(f"{q_label} in {q_loc_display}")
            else:
                cross_bits.append(q_label)
        parts[0] += ", then " + ", then ".join(cross_bits)

    # --- Gender eligibility suffix ---
    # When the user asks for a gender-segregated service (e.g. "shelter
    # for women"), echo that back in the confirmation so they can
    # correct us if we misread. Only female/male get a suffix — "lgbtq"
    # is handled above as a prefix ("LGBTQ-friendly"), and
    # transgender/nonbinary don't map to a NYC eligibility filter we
    # can meaningfully act on (the query layer treats those as sort
    # boosts rather than hard filters; see TestGenderFilterMapping).
    #
    # Placed after cross-location queued and before family_status so
    # the single-intent reading is natural ("shelter in Queens, for
    # women, with children") and the multi-intent reading attaches the
    # filter to the primary service ("shelter in Queens, then food in
    # Brooklyn, for women"). The latter is slightly ambiguous about
    # whether "for women" applies to the queued service too — in
    # practice this is rare enough that the extra clarity isn't worth
    # the complexity of per-queued-item gender echoes.
    if gender == "female":
        parts[0] += ", for women"
    elif gender == "male":
        parts[0] += ", for men"

    family = slots.get("family_status")
    if family == "with_children":
        parts[0] += ", with children"
    elif family == "with_family":
        parts[0] += ", with family"
    elif family == "alone":
        parts[0] += ", for yourself"

    parts[0] += " \u2014 sound good?"

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
        f"I wasn't able to find {service} services in {_display_location(location)} "
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
