"""Query execution layer — runs after the user confirms a search.

This module owns everything that happens between "user tapped Yes, search"
and "service cards ready for the response." That includes the population-
critical fallback for rare taxonomies, the queue-offer for multi-intent
searches, and the structured success/failure message builders.

The primary entry point is ``_execute_and_respond`` — handlers call it
with a confirmed slot set and get back a fully-formed reply dict.

Audit invariants tested in ``tests/unit/test_audit_regression.py``:
  * The primary ``query_services()`` call must not forward the
    ``current_time`` keyword (that flag activates FILTER_BY_OPEN_NOW and
    switches the chatbot to exclude-semantics)
  * The primary ``query_services()`` call must not forward the
    ``weekday`` keyword (that flag activates FILTER_BY_WEEKDAY and may
    exclude services without schedule rows)
Both guards sit on the call site at the end of ``_execute_and_respond``.
"""

import logging
from typing import Optional

from app.rag import query_services
from app.services.confirmation import _display_location, _no_results_message
from app.privacy.pii_redactor import redact_pii
from app.services.phrase_lists import _SERVICE_LABELS, _WELCOME_QUICK_REPLIES
from app.services.session_store import save_session_slots
from app.services.slot_extraction_regex import NEAR_ME_SENTINEL
from app.services.audit_log import log_query_execution

from .context import _DISPLAY_PAGE_SIZE, _count_unique_locations


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Population-Critical Fallback
# ---------------------------------------------------------------------------
# When a user belongs to a rare population (LGBTQ, youth, senior, veteran)
# and the main shelter query returns results that do NOT match that
# population's rare taxonomy — e.g., 5 generic Soho shelters but no
# "LGBTQ Young Adult" tag — the proximity filter has excluded relevant
# services. Only 2 services are tagged "LGBTQ Young Adult" in the whole
# DB; Ali Forney Center is in Midtown, outside Soho's 1600m radius.
#
# This fallback detects that case and runs a second, targeted query that
# drops proximity in favor of borough-wide, restricted to ONLY the rare
# taxonomies. Results are appended with a contextual note so the user
# understands why they're further away.
#
# See docs/design/POPULATION_FALLBACK_SPEC.md for the full design.

# Rare, population-specific shelter taxonomies. Intentionally excludes
# "drop-in center" and "crisis" — those are in the base default list
# and nearly always have proximity-local results.
_POPULATION_RARE_TAXONOMIES = {
    "youth": ["youth"],
    "lgbtq": ["lgbtq young adult"],
    "senior": ["senior"],
    "veteran": ["veterans", "veterans short-term housing"],
}

# Population-appropriate note prefix for the fallback section. Keys are
# the same labels used in _POPULATION_RARE_TAXONOMIES. When multiple
# populations are active, the composed note joins the relevant labels.
_POPULATION_FALLBACK_LABEL = {
    "youth": "youth-specific",
    "lgbtq": "LGBTQ-friendly",
    "senior": "senior-specific",
    "veteran": "veteran",
}

# Priority order (rarest / most-distinguishing first) for picking ONE
# label per card when a card matches multiple of the user's populations.
# Consulted only by the per-card attribution loop in
# _run_population_fallback; does NOT affect the composed note text or
# the order in which taxonomies are queried.
#
# Rarity reflects relative scarcity of NYC shelter services tagged with
# each population:
#   - LGBTQ Young Adult: very few dedicated services (Ali Forney et al.)
#   - Veterans short-term housing: specialized, limited
#   - Senior: limited specialized shelters
#   - Youth: DYCD operates many; least rare of the four
# Adding a new population requires deciding where it slots in. That
# explicit decision is the point — see comments in
# _run_population_fallback.
_POPULATION_RARE_PRIORITY = ("lgbtq", "veteran", "senior", "youth")

# How many fallback cards to append. Kept small so the main (proximity-
# local) results remain the headline answer.
_POPULATION_FALLBACK_MAX = 3

# Reverse lookup: city value (from normalize_location) → canonical borough
# name (used for borough-level queries in the fallback). Soho normalizes
# to "New York" which maps back to Manhattan here.
_CITY_TO_BOROUGH = {
    "New York": "Manhattan",
    "Brooklyn": "Brooklyn",
    "Queens": "Queens",
    "Bronx": "Bronx",
    "Staten Island": "Staten Island",
}

# Reverse-geocode GPS coordinates to an NYC borough by finding the
# nearest known neighborhood and using its borough. This is more
# accurate than a per-borough centroid because Queens's geometric
# center is in sparsely-populated east Queens (causing Astoria/LIC
# to mismatch), and Manhattan is a long thin island where midtown's
# centroid is far from Washington Heights or Battery Park.
#
# The underlying data is NEIGHBORHOOD_CENTERS (59 NYC neighborhoods
# with coordinates) and NYC_LOCATION_ALIASES (neighborhood → city
# value). The built table maps each neighborhood's (lat, lon) to a
# canonical borough name that feeds into the population fallback's
# city_list-based borough query. Built lazily and cached.
_NEIGHBORHOOD_TO_BOROUGH_TABLE: list[tuple[float, float, str]] = []


def _build_neighborhood_borough_table() -> list[tuple[float, float, str]]:
    """Materialize [(lat, lon, borough), ...] for every NYC neighborhood
    that has both coordinates AND a known city→borough mapping.

    Neighborhoods whose city value doesn't map to a borough (shouldn't
    happen with the current data, but defensive) are silently skipped.

    Staten Island is supplemented with hardcoded anchor points because
    NEIGHBORHOOD_CENTERS doesn't currently have any Staten Island
    entries — without these, a GPS user on Staten Island would never
    reverse-geocode to "Staten Island" and the fallback would route
    them to Brooklyn instead.
    """
    from app.rag.query_executor import NEIGHBORHOOD_CENTERS, NYC_LOCATION_ALIASES

    rows: list[tuple[float, float, str]] = []
    for name, (lat, lon) in NEIGHBORHOOD_CENTERS.items():
        city = NYC_LOCATION_ALIASES.get(name.lower())
        borough = _CITY_TO_BOROUGH.get(city) if city else None
        if borough:
            rows.append((lat, lon, borough))

    # Staten Island supplement (no SI entries in NEIGHBORHOOD_CENTERS).
    # A handful of well-spread anchors is enough for nearest-point
    # reverse geocoding to work across the borough.
    rows.extend([
        (40.644, -74.074, "Staten Island"),  # St. George (north shore)
        (40.585, -74.145, "Staten Island"),  # New Dorp / mid-island
        (40.510, -74.230, "Staten Island"),  # Tottenville (south)
    ])
    return rows


def _nearest_borough_by_centroid(lat: float, lon: float) -> Optional[str]:
    """Resolve (lat, lon) to a canonical NYC borough name.

    Two-tier strategy:

    1. **Polygon containment** via NYC DCP boundary polygons (authoritative
       within NYC). If the point is inside any of the five boroughs, that
       borough wins. This is the accurate path — no false attribution near
       borough edges.

    2. **Centroid fallback** if the point is outside NYC (e.g., NJ GPS drift,
       Yonkers, Long Island). Returns the borough of the closest NYC
       neighborhood by squared Euclidean distance in lat/lon space. This
       preserves the original useful property of always returning *some*
       borough for callers like the population-critical fallback, which
       needs a borough to query.

    Function name preserved for back-compat with existing callers.

    Returns None if inputs aren't numeric or the neighborhood fallback
    table is also empty (pathological case — shouldn't happen in prod).
    """
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None

    # Tier 1: polygon containment
    try:
        from app.rag.boundaries import borough_from_coords
        poly_borough = borough_from_coords(lat, lon)
        if poly_borough is not None:
            return poly_borough
    except Exception as e:
        # Boundaries module should never fail, but if the vendored GeoJSON
        # is corrupt or shapely blows up, fall through to the centroid
        # path rather than taking down the whole population fallback.
        logger.warning(
            "borough_from_coords failed (lat=%s lon=%s): %s — "
            "falling back to centroid-based resolution", lat, lon, e,
        )

    # Tier 2: centroid fallback (also handles out-of-NYC GPS)
    global _NEIGHBORHOOD_TO_BOROUGH_TABLE
    if not _NEIGHBORHOOD_TO_BOROUGH_TABLE:
        _NEIGHBORHOOD_TO_BOROUGH_TABLE = _build_neighborhood_borough_table()
    if not _NEIGHBORHOOD_TO_BOROUGH_TABLE:
        return None

    best_borough: Optional[str] = None
    best_d2 = float("inf")
    for nlat, nlon, borough in _NEIGHBORHOOD_TO_BOROUGH_TABLE:
        d2 = (lat - nlat) ** 2 + (lon - nlon) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best_borough = borough
    return best_borough


def _compute_rare_population_taxonomies(slots: dict) -> tuple[list[str], list[str]]:
    """Determine which rare population-specific shelter taxonomies the
    user's context makes relevant.

    Derives from population context (age/gender/_populations) directly,
    NOT from a diff against the default taxonomy list. The rare taxonomies
    (lgbtq young adult, youth, senior, veterans, veterans short-term
    housing) are ALREADY in the base default list — so a diff would be
    empty and the fallback would never fire for the Cornell Q1 case.

    Returns:
        (taxonomies, labels) — both lists, empty when no rare population
        applies. taxonomies is the flat list of DB taxonomy names to query
        against; labels is the list of population labels for the note.
    """
    taxonomies: list[str] = []
    labels: list[str] = []

    age = slots.get("age")
    age_valid = isinstance(age, int) and age != "skipped"
    populations = slots.get("_populations") or []
    gender = slots.get("_gender")
    family_status = slots.get("family_status")

    # Youth: 16-24, unless the user is explicitly searching for family
    # shelter (family_status set to with_children/with_family) — family
    # shelter has its own taxonomy track.
    if age_valid and 16 <= age <= 24 and family_status not in ("with_children", "with_family"):
        taxonomies.extend(_POPULATION_RARE_TAXONOMIES["youth"])
        labels.append("youth")

    # LGBTQ: any of gender=lgbtq/transgender/nonbinary, OR lgbtq in populations
    is_lgbtq = (
        gender in ("lgbtq", "transgender", "nonbinary")
        or "lgbtq" in populations
    )
    if is_lgbtq:
        taxonomies.extend(_POPULATION_RARE_TAXONOMIES["lgbtq"])
        labels.append("lgbtq")

    # Senior: age >= 62
    if age_valid and age >= 62:
        taxonomies.extend(_POPULATION_RARE_TAXONOMIES["senior"])
        labels.append("senior")

    # Veteran
    if "veteran" in populations:
        taxonomies.extend(_POPULATION_RARE_TAXONOMIES["veteran"])
        labels.append("veteran")

    # Dedupe taxonomies while preserving order. Labels are always distinct
    # by construction, no dedupe needed.
    seen = set()
    deduped = [t for t in taxonomies if not (t in seen or seen.add(t))]
    return deduped, labels


def _resolve_borough_from_location(
    location: Optional[str],
    slots: Optional[dict] = None,
) -> Optional[str]:
    """Resolve a user-facing location (borough name OR neighborhood) to a
    canonical borough name.

    If `slots` is provided and `location` is the NEAR_ME_SENTINEL (browser
    geolocation active — no text location), falls back to reverse-geocoding
    the user's lat/lon against borough centroids.

    Returns None if the location can't be resolved.

    Note: as of the Option B change to _run_population_fallback, this
    function is no longer called by the population-critical fallback
    (which now runs citywide regardless of user borough). It's retained
    for potential future "browse by borough" UI and for its test coverage
    of the text→borough normalization logic.
    """
    from app.rag.query_executor import is_borough, normalize_location

    # GPS path — user has no text location, just lat/lon. Reverse-geocode
    # against borough centroids. Matches on the sentinel string OR on a
    # falsy location when coords are present.
    if slots is not None:
        _lat = slots.get("_latitude")
        _lon = slots.get("_longitude")
        if (
            (location == NEAR_ME_SENTINEL or not location)
            and _lat is not None
            and _lon is not None
        ):
            return _nearest_borough_by_centroid(_lat, _lon)

    if not location:
        return None

    if is_borough(location):
        # Already a borough — return the canonical Title-cased name so it
        # keys correctly into _CITY_TO_BOROUGH and drives the fallback's
        # city_list-based borough query. "The Bronx" → "Bronx".
        cleaned = location.strip().title()
        if cleaned.lower() == "the bronx":
            return "Bronx"
        return cleaned

    # Neighborhood — normalize to city, then reverse-map to borough.
    city = normalize_location(location)
    return _CITY_TO_BOROUGH.get(city)


def _taxonomies_overlap(card_taxonomies, rare_set_lower: set) -> bool:
    """Check whether a service card's taxonomy tags intersect the rare set.

    DB values are stored in Title Case (e.g. "LGBTQ Young Adult") while
    the rare set is lowercase — compare case-insensitively.
    """
    if not card_taxonomies:
        return False
    card_lower = {str(t).lower() for t in card_taxonomies if t}
    return bool(card_lower & rare_set_lower)


def _run_population_fallback(
    slots: dict,
    rare_taxonomies: list[str],
    labels: list[str],
    existing_service_ids: set,
) -> tuple[list[dict], str]:
    """Execute the fallback query citywide and return (fallback_cards, note_text).

    Returns ([], "") when the fallback query errors out or when all
    fallback cards are duplicates of the main results. Never raises — any
    exception is caught and logged so a fallback failure can't break the
    main response path.

    **Scope: citywide.** We intentionally do NOT filter by the user's
    borough here. Rare-population services are sparse — Ali Forney Center
    (the only LGBTQ Young Adult shelter) is in Manhattan, so a borough-
    scoped query from a Far Rockaway GPS user would return nothing.
    For these rare taxonomies, cross-borough results are strictly
    better than no results. See docs/design/POPULATION_FALLBACK_SPEC.md §Scope.

    Dedupe still applies, so services from the main query don't double-
    up. The "further away" note phrasing is accurate for citywide scope
    too — these cards ARE further from the user, often in a different
    borough, which is exactly why they need the contextual framing.
    """
    _age = slots.get("age")
    age_valid = isinstance(_age, int) and _age != "skipped"

    try:
        # Citywide, no proximity, no gender, no borough. Age is preserved
        # (a 17-year-old still shouldn't see adult-only shelters), but
        # family_status, service_detail, AND location are all dropped —
        # the point of the fallback is to find the rare taxonomies at
        # all, not to satisfy every filter the main query applied.
        fallback_result = query_services(
            service_type="shelter",
            location=None,
            age=_age if age_valid else None,
            gender=None,
            latitude=None,
            longitude=None,
            family_status=None,
            service_detail=None,
            populations=None,
            taxonomy_override=rare_taxonomies,
            max_results=_POPULATION_FALLBACK_MAX,
        )
    except Exception as e:
        logger.warning(f"Population fallback query failed: {e}")
        return [], ""

    cards = fallback_result.get("services", []) or []

    # Dedupe against main results — same service shouldn't appear twice.
    deduped = [c for c in cards if c.get("service_id") not in existing_service_ids]
    if not deduped:
        # Visibility for admin dashboards: the fallback RAN but every
        # card it found was already in the main results. From the
        # user's perspective, no "also found X further away" note
        # appears. Without this log, the state "fallback attempted,
        # fully deduped to nothing" is indistinguishable in the ops
        # feed from "fallback was never attempted." See
        # docs/design/POPULATION_FALLBACK_SPEC.md §Observability.
        logger.warning(
            "Population fallback dedup-to-empty: labels=%s "
            "fallback_cards_fetched=%d main_result_ids=%d. "
            "User sees no fallback note even though the query ran.",
            labels, len(cards), len(existing_service_ids),
        )
        return [], ""

    # Mark each card so the frontend can visually distinguish fallback
    # cards from main results (future-proofing — current UI renders them
    # in the same carousel). Per-card `fallback_population` reflects
    # which specific rare population this particular card matched.
    #
    # When a card matches MULTIPLE of the user's population labels (e.g.,
    # a shelter tagged both "Youth" and "LGBTQ Young Adult" shown to a
    # trans 20-year-old), we pick the *most distinguishing* label — the
    # one that sets this card apart from general shelter results. We
    # walk labels in priority order defined by
    # `_POPULATION_RARE_PRIORITY` below, rarest first. This is a fixed,
    # auditable ordering rather than a runtime DB-count because (1) the
    # relative rarity of these four populations in NYC services data is
    # stable across deploys, (2) adding a new population requires an
    # explicit decision about where it slots in, which is the right
    # forcing function, and (3) it avoids a startup-time DB dependency.
    for card in deduped:
        card["is_population_fallback"] = True
        card_tx_lower = {
            str(t).lower() for t in (card.get("service_taxonomies") or []) if t
        }
        # Iterate labels in rarity-priority order, restricted to the
        # labels the CURRENT user matched. The intersection preserves
        # "we only pick a label the user actually qualifies for" while
        # the sort gives us "rarest wins" among qualifying labels.
        user_labels = set(labels)
        priority_ordered = [
            lb for lb in _POPULATION_RARE_PRIORITY if lb in user_labels
        ]
        matched_label = None
        for label in priority_ordered:
            label_tx_lower = {
                t.lower() for t in _POPULATION_RARE_TAXONOMIES.get(label, [])
            }
            if card_tx_lower & label_tx_lower:
                matched_label = label
                break
        # Fall through to the first label only if NOTHING matched — this
        # shouldn't happen (we ran the fallback BECAUSE of these labels)
        # but is a safe default. Use the original `labels` list's first
        # entry (user's detection order) rather than priority order, to
        # preserve the prior behavior for this defensive branch.
        card["fallback_population"] = matched_label or labels[0]

    # Compose the note. Cap at the first two labels to keep prose readable
    # when a user matches multiple populations (e.g., trans veteran youth).
    note_parts = [_POPULATION_FALLBACK_LABEL.get(lb, lb) for lb in labels[:2]]
    if len(note_parts) == 1:
        note_phrase = note_parts[0]
    else:
        note_phrase = " and ".join(note_parts)
    note = (
        f"\n\nI also found {note_phrase} services further away "
        f"that may be helpful:"
    )
    return deduped, note


def _apply_queue_offer(
    session_id: str,
    slots: dict,
    services_list: list,
    bot_response: str,
) -> tuple[str, list]:
    """Append a "You also mentioned X — search for that too?" offer when
    the user queued multiple services in one message.

    Fires only when there's at least one queued service AND the current
    search returned something (``services_list`` non-empty). Pops one
    item from the queue, persists ``_queue_offer_pending`` on the
    session, and replaces the default after-results quick replies with
    yes/no buttons specific to the next queued service.

    Returns (augmented_bot_response, after_results_qr).
    """
    default_qr = [
        {"label": "🔍 New search", "value": "Start over"},
        {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
    ]

    queued = slots.get("_queued_services", [])
    if not (queued and services_list):
        return bot_response, default_qr

    q_item = queued[0]
    next_service = q_item[0]
    next_detail = q_item[1] if len(q_item) > 1 else None
    next_location = q_item[2] if len(q_item) > 2 else None
    remaining = queued[1:]

    if remaining:
        slots["_queued_services"] = remaining
    else:
        slots.pop("_queued_services", None)
    slots["_queue_offer_pending"] = True

    # Persist the full offered item so a typed "yes" (not button-click)
    # can reconstruct the promoted search. The button's qr_value encodes
    # the full command as text, but free-text "yes" has no context
    # without this slot. See R34 Diagnosis 2, Bug 2.
    slots["_queued_offer"] = (next_service, next_detail, next_location)

    if next_location and next_location != slots.get("location"):
        slots["_queued_location"] = next_location
    save_session_slots(session_id, slots)

    label = next_detail or _SERVICE_LABELS.get(next_service, next_service)
    loc_suffix = ""
    if next_location and next_location != slots.get("location"):
        # Match the display-casing + PII-redaction treatment the primary
        # confirmation message applies to its location. Without this,
        # the queue-offer message shows raw lowercase ("showers in lower
        # east side") while the primary confirmation above says
        # display-cased ("Lower East Side") — cosmetic but jarring.
        loc_clean, _ = redact_pii(next_location)
        loc_display = _display_location(loc_clean)
        loc_suffix = f" in {loc_display}"

    # Queue-depth transparency: when more than one item is still
    # pending, the user explicitly asked for several things and only
    # ONE will be offered as a follow-up at a time. Without surfacing
    # the queue depth, the user (and the eval judge) reads the
    # one-item offer as if the rest were silently dropped.
    #
    # Example failure mode (from multi_three_services_legal_benefits_food
    # in the R42-borderline run): user asks for food, asylum services,
    # and food stamps. Confirmation lists all three. Results show 1
    # food card + "You also mentioned asylum services — search?"
    # Judge marks "food stamps appears dropped from the queue." The
    # food stamps slot persists in _queued_services and would be
    # offered next, but the bot didn't tell the user that.
    if remaining:
        # remaining was already sliced from queued[1:]; len(remaining)
        # is the count of items still queued AFTER this offer.
        next_remaining = remaining[0]
        next_remaining_label = (
            (next_remaining[1] if len(next_remaining) > 1 and next_remaining[1] else None)
            or _SERVICE_LABELS.get(next_remaining[0], next_remaining[0])
        )
        if len(remaining) == 1:
            # One more item beyond this offer.
            queue_tail = (
                f" ({next_remaining_label} after that)"
            )
        else:
            # Multiple still queued.
            queue_tail = (
                f" ({next_remaining_label} and "
                f"{len(remaining) - 1} more after that)"
            )
    else:
        queue_tail = ""

    augmented = bot_response + (
        f"\n\nYou also mentioned {label}{loc_suffix} — would you like me to "
        f"search for that too?{queue_tail}"
    )
    # Note: qr_value (the button's returned message) stays lowercase —
    # it's a command string fed back through slot extraction, which is
    # case-insensitive. Display-casing it would add no value and could
    # mask case-sensitivity bugs if any exist downstream.
    qr_value = f"I need {next_service}"
    if next_location:
        qr_value += f" in {next_location}"
    after_results_qr = [
        {"label": f"✅ Yes, search for {label}", "value": qr_value},
        {"label": "❌ No thanks", "value": "No thanks"},
    ]
    return augmented, after_results_qr


def _build_success_response(
    slots: dict,
    results: dict,
    colocated_success: bool,
    colocated_types: list | None,
    session_id: str,
) -> tuple[str, list, list, int, int, bool]:
    """Translate a successful query result into the user-facing response
    message + card list + pagination metadata.

    Handles:
    - Standard success ("I found N option(s)")
    - Co-located multi-service ("I found N location(s) that offer both food and clothing")
    - "Relaxed" search qualifier when the strict query returned 0 and we
      broadened via the relaxed path
    - Population-critical fallback for shelter queries where no local
      result matches a rare-population taxonomy (LGBTQ YA, youth, senior,
      veteran) — appends citywide fallback cards with their own note.

    Returns (bot_response, services_list, all_services, main_displayed_count,
    result_count, relaxed). The distinction between ``services_list``
    (displayed cards, including fallback) and ``all_services`` (main-query
    cards only, the pagination source) is load-bearing — pagination must
    NOT re-show fallback cards on "Show more."
    """
    all_services = results["services"]
    services_list = all_services[:_DISPLAY_PAGE_SIZE]
    main_displayed_count = len(services_list)
    result_count = len(services_list)
    total_count = _count_unique_locations(all_services)
    relaxed = results.get("relaxed", False)

    qualifier = " (I broadened the search a bit)" if relaxed else ""

    if colocated_success and colocated_types:
        primary = _SERVICE_LABELS.get(
            slots.get("service_type", ""), slots.get("service_type", "")
        )
        queued_original = slots.get("_queued_services_original", [])
        co_labels = []
        for i, t in enumerate(colocated_types):
            detail = queued_original[i][1] if i < len(queued_original) else None
            co_labels.append(detail or _SERVICE_LABELS.get(t, t))
        all_labels = [primary] + co_labels
        combined = " and ".join(all_labels) if len(all_labels) <= 2 else (
            ", ".join(all_labels[:-1]) + ", and " + all_labels[-1]
        )
        if total_count > main_displayed_count:
            bot_response = (
                f"I found {total_count} location(s) that offer both "
                f"{combined.lower()}{qualifier} — showing the first "
                f"{main_displayed_count}:"
            )
        else:
            bot_response = (
                f"I found {total_count} location(s) that offer both "
                f"{combined.lower()}{qualifier}:"
            )
    else:
        if total_count > main_displayed_count:
            bot_response = (
                f"I found {total_count} option(s) for you{qualifier} — "
                f"showing the first {main_displayed_count}:"
            )
        else:
            bot_response = f"I found {total_count} option(s) for you{qualifier}:"

    # Population-critical fallback (shelter only). When the user belongs
    # to a rare population (LGBTQ, youth, senior, veteran) and the
    # proximity-local results contain no services tagged with that
    # population's rare taxonomy, run a CITYWIDE targeted query for
    # those taxonomies so Ali Forney / Covenant House / VA etc. can
    # still surface regardless of which borough the user is searching
    # from. See _run_population_fallback and
    # docs/design/POPULATION_FALLBACK_SPEC.md §Scope.
    #
    # Fallback cards are appended to services_list for display but
    # INTENTIONALLY NOT to all_services. all_services drives pagination
    # via slots["_last_results"]; fallback cards are supplementary
    # (shown once with a contextual note) and must not reappear on
    # subsequent "Show more" pages.
    if (
        slots.get("service_type") == "shelter"
        and not results.get("relaxed")
        and not colocated_success
    ):
        rare_tx, rare_labels = _compute_rare_population_taxonomies(slots)
        if rare_tx:
            rare_set_lower = {t.lower() for t in rare_tx}
            has_match = any(
                _taxonomies_overlap(card.get("service_taxonomies"), rare_set_lower)
                for card in all_services
            )
            if not has_match:
                existing_ids = {c.get("service_id") for c in all_services if c.get("service_id")}
                fb_cards, fb_note = _run_population_fallback(
                    slots, rare_tx, rare_labels, existing_ids
                )
                if fb_cards:
                    services_list = services_list + fb_cards
                    result_count = len(services_list)
                    bot_response = bot_response + fb_note
                    # Admin-visibility log. Fallback cards are supplementary
                    # (shown once, not paginated) and appear in services_list
                    # for the current response only — they are intentionally
                    # not stored in _last_results (see comment above about
                    # pagination isolation). Emit the IDs and attributed
                    # labels to the ops feed so a later "is the first one
                    # open?" or admin audit can correlate what the user saw.
                    # Replaces the prior slots["_fallback_results"] stash,
                    # which was written but never read (see
                    # docs/audits/TEST_QUALITY_PLAN.md §3.1).
                    logger.info(
                        "Population fallback cards shown: session=%s "
                        "count=%d ids=%s labels=%s",
                        session_id,
                        len(fb_cards),
                        [c.get("service_id") for c in fb_cards],
                        [c.get("fallback_population") for c in fb_cards],
                    )

    return bot_response, services_list, all_services, main_displayed_count, result_count, relaxed


def _build_db_failure_message(session_id: str, slots: dict) -> str:
    """Generate the user-facing message when the DB query throws.

    **Critical invariant:** never call the LLM on this path. When the DB is
    down, the LLM produces helpful-sounding follow-up questions ("To help
    narrow things down…") that look like the intake flow and trap the user
    in an infinite confirmation loop where they keep confirming but never
    get results. The static messages below are intentional.

    Escalates to a "try yourpeer.nyc directly" message after 2+ failures
    in the same session. Mutates ``slots["_search_fail_count"]``.
    """
    fail_count = slots.get("_search_fail_count", 0) + 1
    slots["_search_fail_count"] = fail_count
    save_session_slots(session_id, slots)
    if fail_count >= 2:
        return (
            "I'm still having trouble searching. "
            "Please visit yourpeer.nyc to search directly, "
            "or try again later."
        )
    return (
        "I'm having trouble connecting to the service database "
        "right now. You can try again in a moment, or visit "
        "yourpeer.nyc to search for services directly."
    )


# Service-detail values that indicate a substance-use treatment search.
# Sourced from _NOTABLE_SUB_TYPES in slot_extraction_regex.py — kept as
# a parallel set here to avoid an upward import. If new substance-use
# treatment service-detail canonicals are added there, they should be
# added here too. Drift detection: the substance-use disclosure tests
# in test_tone_and_empathy.py exercise the canonical values, so a new
# canonical that doesn't fire the addendum will surface there.
_SUBSTANCE_USE_SERVICE_DETAILS = frozenset({
    "detox",
    "rehab services",
    "recovery services",
    "addiction services",
    "substance abuse services",
    "harm reduction services",
    "substance use treatment",
    "treatment programs",
    "treatment centers",
    "inpatient treatment",
    "outpatient treatment",
    "sober living",
})


def _substance_use_safety_addendum(slots: dict) -> str:
    """Return the safety addendum text for a substance-use disclosure,
    or empty string if no addendum should fire.

    The addendum carries the SAMHSA national helpline (1-800-662-4357),
    a 911 prompt for emergencies, and — for alcohol/opiate disclosures
    — a medical-supervision recommendation grounded in the specific
    risks of those withdrawal profiles.

    **Two gates, both required:**

    1. ``slots["_emotional_context"]`` starts with
       ``"substance_use_disclosure"`` — the user disclosed substance
       use at some point in the session.

    2. The CURRENT search is for substance-use treatment:
       ``service_type == "medical"`` AND ``service_detail`` is in
       ``_SUBSTANCE_USE_SERVICE_DETAILS``. Without this gate, the
       persisted ``_emotional_context`` slot leaks the addendum into
       unrelated subsequent searches (e.g., user discloses substance
       use on turn 1, says "actually, I need food instead" on turn 2,
       and the food search results would otherwise carry the
       alcohol/opiate safety text).

    Branches on the emotional_context subtype set by tone.py:

    * ``substance_use_disclosure_alcohol_opiate`` — alcohol withdrawal
      can be life-threatening (delirium tremens, seizures); opiate
      withdrawal carries elevated overdose risk on relapse because
      tolerance drops during abstinence. The medical-supervision note
      is correct for these substances and should be surfaced.

    * ``substance_use_disclosure_other`` — generic substance disclosure
      (cocaine, meth, marijuana, benzos, or unspecified). The
      alcohol/opiate-specific medical-risk language is mistuned for
      these cases, so the addendum stays general: SAMHSA helpline +
      911 prompt, without claims about withdrawal danger that the
      bot can't validate for the user's specific substance.

    Fires regardless of result_count — the duty-of-care to surface
    SAMHSA + 911 doesn't depend on whether we found local options.
    A user disclosing substance use in a borough with thin coverage
    needs the helpline more than one in a borough with abundant
    options, not less. (Bug-hunt finding #8.)
    """
    ctx = slots.get("_emotional_context")
    if not ctx or not ctx.startswith("substance_use_disclosure"):
        return ""
    # Cross-turn carryover gate. The _emotional_context slot persists
    # across turns (shared infrastructure with shame/medical_urgent
    # continuity), so we additionally require the CURRENT search to
    # actually be for substance-use treatment before firing the
    # addendum. See _SUBSTANCE_USE_SERVICE_DETAILS for the canonical
    # list of substance-related service_detail values.
    if slots.get("service_type") != "medical":
        return ""
    if slots.get("service_detail") not in _SUBSTANCE_USE_SERVICE_DETAILS:
        return ""
    if ctx == "substance_use_disclosure_alcohol_opiate":
        return (
            "\n\n"
            "A note on safety: detoxing from alcohol or opiates "
            "can be medically risky — alcohol withdrawal can be "
            "life-threatening, and opiate withdrawal raises the "
            "risk of overdose if you relapse. Please consider a "
            "medically-supervised program. If you need to talk to "
            "someone right now, the SAMHSA national helpline is "
            "free and confidential: 1-800-662-4357 (HELP). For an "
            "emergency, call 911."
        )
    # Generic addendum for non-alcohol/opiate substances. No
    # medical-supervision claim — withdrawal danger varies by
    # substance and the bot can't reliably classify which apply.
    return (
        "\n\n"
        "A note on safety: getting support for substance use is "
        "easier when you have someone walking with you. The SAMHSA "
        "national helpline is free and confidential, and they can "
        "help you find treatment options that fit your situation: "
        "1-800-662-4357 (HELP). For an emergency, call 911."
    )


def _execute_and_respond(
    session_id: str,
    message: str,
    slots: dict,
    request_id: str | None = None,
) -> dict:
    """Execute the DB query and return results. Called after user confirms."""
    bot_response = None
    services_list = []
    all_services = []
    # Count of main-query cards in `services_list`. Stays in sync with
    # `len(services_list)` EXCEPT when the population-critical fallback
    # appends extra cards — those are supplementary and must NOT count
    # toward pagination (they're shown once with their own note and don't
    # reappear on "Show more"). See the fallback block below.
    _main_displayed_count = 0
    result_count = 0
    relaxed = False
    _FETCH_LIMIT = 25

    try:
        location = slots.get("location")
        use_coords = (
            location == NEAR_ME_SENTINEL
            and slots.get("_latitude") is not None
            and slots.get("_longitude") is not None
        )

        queued = slots.get("_queued_services", [])
        # Only co-locate queued services that share the same location.
        # Cross-borough requests (e.g., shelter in Manhattan while searching
        # food in Brooklyn) should remain queued, not co-located.
        primary_location = slots.get("location")
        colocated_types = []
        for q in queued:
            q_location = q[2] if len(q) > 2 else None
            if q_location is None or q_location == primary_location:
                colocated_types.append(q[0])
        colocated_types = colocated_types or None
        if queued:
            slots["_queued_services_original"] = list(queued)

        _age = slots.get("age")
        _family = slots.get("family_status")
        results = query_services(
            service_type=slots.get("service_type"),
            location=location,
            age=_age if _age != "skipped" else None,
            gender=slots.get("_gender"),
            latitude=slots.get("_latitude") if use_coords else None,
            longitude=slots.get("_longitude") if use_coords else None,
            family_status=_family if _family != "skipped" else None,
            colocated_service_types=colocated_types,
            service_detail=slots.get("service_detail"),
            populations=slots.get("_populations"),
            org_name=slots.get("org_name"),
            no_requirements=bool(slots.get("no_requirements")),
            max_results=_FETCH_LIMIT,
        )

        colocated_success = (
            colocated_types
            and results.get("result_count", 0) > 0
            and not results.get("colocated_fallback")
        )
        if colocated_success:
            slots.pop("_queued_services", None)
            slots.pop("_queued_services_original", None)
            save_session_slots(session_id, slots)

        log_query_execution(
            session_id=session_id,
            template_name=results.get("template_used", "unknown"),
            params=results.get("params_applied", {}),
            result_count=results.get("result_count", 0),
            relaxed=results.get("relaxed", False),
            execution_ms=results.get("execution_ms", 0),
            freshness=results.get("freshness"),
            request_id=request_id,
        )

        if results.get("error"):
            logger.warning(f"Query error: {results['error']}")
            bot_response = (
                "I ran into an issue with that search. "
                "You can try again, or visit yourpeer.nyc directly."
            )
        elif results["result_count"] > 0:
            (bot_response, services_list, all_services,
             _main_displayed_count, result_count, relaxed) = _build_success_response(
                slots, results, colocated_success, colocated_types, session_id,
            )
        else:
            bot_response = _no_results_message(slots)

        # Substance-use safety addendum.
        #
        # Set by tone._compute_tone_prefix when the user's message
        # includes alcohol/opiate/addiction disclosure language with
        # no exclusion patterns (third-party, professional lookup,
        # long-term recovery, non-substance addictions).
        #
        # Layered AFTER the if/elif/else so it applies regardless of
        # whether we returned results, returned the no-results
        # message, or hit a query error. The duty-of-care to surface
        # SAMHSA + 911 doesn't depend on whether we found local
        # options — if anything, a substance-use disclosure with
        # zero local results needs the helpline more, not less.
        # (Bug-hunt finding #8.)
        #
        # Branches by subtype: alcohol/opiate disclosures get the
        # medical-supervision note; other substances get a generic
        # SAMHSA + 911 message without the alcohol/opiate-specific
        # withdrawal claims. (Bug-hunt finding #9.)
        addendum = _substance_use_safety_addendum(slots)
        if addendum and bot_response:
            bot_response += addendum

    except Exception as e:
        logger.error(f"Database query failed: {e}")
        # CRITICAL: Do NOT call _fallback_response (LLM) for DB failures.
        # See _build_db_failure_message for the full rationale.
        bot_response = _build_db_failure_message(session_id, slots)

    if bot_response is None:
        # Same principle: don't call LLM for search-path failures.
        bot_response = (
            "I wasn't able to complete the search. "
            "You can try again, or visit yourpeer.nyc directly."
        )

    # Queue offer for multi-intent: if the user asked for multiple services,
    # offer to search the next one. Snapshot the queue BEFORE the helper
    # mutates it — the "Show more" suppression below checks whether a
    # queue offer was MADE, not whether more items remain after.
    queued_before_offer = slots.get("_queued_services", [])
    bot_response, after_results_qr = _apply_queue_offer(
        session_id, slots, services_list, bot_response,
    )

    if services_list:
        slots.pop("_search_fail_count", None)  # Clear on success
        slots["_last_results"] = all_services  # main-query results only — pagination source
        # _displayed_count counts the MAIN cards the user has seen (not
        # fallback cards, which are supplementary and shown once with a
        # note). Using len(services_list) here would double-count fallback
        # cards and cause "Show more" to re-show them on page 2.
        _displayed = _main_displayed_count if _main_displayed_count else len(services_list)
        slots["_displayed_count"] = _displayed
        save_session_slots(session_id, slots)

        # If there are undisplayed main results, add "show more" quick reply.
        # Button label shows the next page's *location* count (not raw
        # service count) so the number matches the carousel card count.
        # Suppressed when a queue offer was made — the queue offer buttons
        # are the user's next action, adding "Show more" alongside would
        # clutter the UI.
        _undisplayed = len(all_services) - _displayed
        if _undisplayed > 0 and not queued_before_offer:
            _next_page_services = all_services[_displayed:_displayed + _DISPLAY_PAGE_SIZE]
            _show_next = _count_unique_locations(_next_page_services)
            after_results_qr.insert(0, {
                "label": f"📋 Show {_show_next} more result{'s' if _show_next != 1 else ''}",
                "value": "Show more results",
            })

    return {
        "session_id": session_id,
        "response": bot_response,
        "follow_up_needed": False,
        "slots": slots,
        "services": services_list,
        "result_count": result_count,
        "relaxed_search": relaxed,
        "quick_replies": after_results_qr if services_list else list(_WELCOME_QUICK_REPLIES),
    }
