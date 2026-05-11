"""
Locations admin — aggregation queries.

One function per page section. The Locations admin page renders 11
spec'd analytical sections plus a triage table; each function below
backs one endpoint under /admin/api/locations/*. Functions in display
order:

  * Section 1 — get_locations_stats           (top stat strip)
  * Section 2a — get_freshness_histogram      (age-bucket bars)
  * Section 2b — get_locations_list           (triage table, paginated/sortable/filterable)
  * Section 3a — get_locations_by_borough     (rollup table)
  * Section 3b — get_service_borough_heatmap  (taxonomy × borough)
  * Section 3c — get_coordinate_issues        (geo validation)
  * Section 4a — get_category_coverage        (demand:supply)
  * Section 4b — get_stale_categories         (system-wide neglect)
  * Section 5a/5b — get_location_feedback_aggregates
  * Section 5c — get_recent_feedback_comments
  * Section 6 — get_data_integrity_callouts   (orphan checks etc.)
  * Section 7 — get_locations_timeseries      (weekly added/verified/feedback)

Each function returns a structured dict matching its frontend
TypeScript counterpart (see `frontend-next/src/lib/admin/
locations-types.ts`). Aggregations run against the Streetlives
read-only Postgres via the existing `_execute_sql` helper — no new
schema, no migrations, no new connection.

Tunable constants are exported at module top so future tuning is
one-stop: fresh threshold, recent-flags lookback, top-N categories,
feedback sample minimum, audit-log cap, time-series window. Each
documented inline.

Audit-log reads go through `_get_events_capped(event_type)` (defined
below). It wraps `get_recent_events(limit=AUDIT_LOG_CAP, ...)` and
logs a WARNING when the cap is reached — so silent truncation past
AUDIT_LOG_CAP events becomes a visible signal in production logs
rather than a quietly-undercounting aggregation.
"""
from __future__ import annotations

import logging
import zoneinfo
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.rag.query_executor import _execute_sql
from app.services.audit_log import get_recent_events
from app.services.locations_admin.cache import ttl_cached

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TUNABLE CONSTANTS
#
# Single source of truth for the page's threshold values. Changing
# any of these here changes the page-wide behavior — no other
# constants need to move in lockstep.
# ---------------------------------------------------------------------------

# A location is "fresh" if its last_validated_at is within this many
# days. Matches `_FRESHNESS_DAYS = 90` in query_templates.py so the
# admin view of freshness aligns with the chat-side freshness ranking
# used in service search.
FRESHNESS_THRESHOLD_DAYS = 90

# How far back to count "recent flags" in section 2b's column.
# Calibrated for a 2-6 week team review cadence — 45 days ensures
# nothing slips through unflagged between reviews even at the slow
# end of the cadence range. Worth re-tuning if review cadence
# changes.
RECENT_FLAGS_LOOKBACK_DAYS = 45

# Number of taxonomy categories to surface by default in the
# heatmap (section 3b). The remaining categories are collapsible
# under "+N more". 10 keeps the grid readable on a 1024px viewport
# without label truncation. The top-10-by-service-count list closely
# matches the team's ops vocabulary for "the categories" — food,
# shelter, health, mental health, legal, clothing, hygiene,
# employment, benefits, education.
HEATMAP_TOP_N_CATEGORIES = 10

# Minimum sample size for a location to appear in section 5a's
# "most-flagged" rankings. Set to 2 to avoid one-off noise while
# keeping the bar low — feedback is currently sparse so a higher
# threshold would suppress real signals.
FEEDBACK_MIN_SAMPLE = 2

# Range for the section 7 time series. 26 weeks ≈ 6 months gives
# the team enough history to see seasonal shape without making the
# time series visually busy.
TIMESERIES_WEEKS = 26

# Display timezone for human-facing temporal buckets (week boundaries
# in the time series, "today" in date-driven cutoffs that humans
# read against a calendar).
#
# Streetlives operates in NYC, so admins read the dashboard against
# an ET calendar — "events this week" should match their wall clock.
# Without this, week boundaries default to UTC and events near the
# UTC-day boundary land in the wrong bucket from a NYC admin's view
# (e.g. a Sunday-evening feedback event in ET = 4am Monday UTC =
# bucketed into the next week).
#
# Note we DON'T retroactively apply this to the 7-day / 90-day
# windows in the stat strip and freshness queries — a 5-hour TZ
# offset is immaterial for windows that large, and switching them
# would invalidate cached query plans. The timezone correction only
# applies where the human-week boundary actually matters: the
# timeseries.
DISPLAY_TIMEZONE = zoneinfo.ZoneInfo("America/New_York")

# How many audit-log events of one type we read in a single pass.
# `get_recent_events` returns the most-recent N events of the given
# type, silently truncating older ones when there are more. Set high
# enough that pilot-volume usage never trips it, but observably
# capped — _get_events_capped() warns the moment we hit the limit so
# the silent-truncation case stops being silent.
#
# When this warning starts firing in production logs, the next step
# is either bumping the cap or migrating these aggregations to a
# streaming / paginated read pattern.
AUDIT_LOG_CAP = 10000


def _get_events_capped(event_type: str) -> list:
    """Wrapper around `get_recent_events` that warns when the cap is
    likely hit.

    `get_recent_events(limit=AUDIT_LOG_CAP)` truncates silently when
    there are more than AUDIT_LOG_CAP events of that type — the
    function has no way to signal "there's more." We approximate the
    signal here by checking whether the result length exactly equals
    the cap; if so, log a warning so production observability picks
    it up.

    Note the approximation: there's a single-event window where the
    cap is hit exactly with no truncation, which would log a false-
    positive warning. Acceptable — the noise is small and the cost
    of the alternative (a second query for the true count) isn't
    worth it at the catalog's current scale.

    All locations-admin aggregations that read from the audit log
    should go through this wrapper, not `get_recent_events` directly,
    so the warning surface is consistent.
    """
    events = get_recent_events(limit=AUDIT_LOG_CAP, event_type=event_type)
    if len(events) >= AUDIT_LOG_CAP:
        logger.warning(
            "locations_admin: AUDIT_LOG_CAP (%d) reached for event_type=%s — "
            "older events truncated. Aggregations using this read may "
            "undercount. Consider bumping the cap or migrating to a "
            "paginated read.",
            AUDIT_LOG_CAP,
            event_type,
        )
    return events


# ---------------------------------------------------------------------------
# SECTION 1 — TOP STAT STRIP
# ---------------------------------------------------------------------------

@ttl_cached()
def get_locations_stats() -> dict:
    """Six-card stat strip + 7-day trend deltas.

    Returns shape:
        {
            "total_locations": int,
            "total_services": int,
            "fresh_count": int,           # last_validated_at >= now - 90d
            "never_verified_count": int,  # last_validated_at IS NULL
            "stale_count": int,           # last_validated_at < now - 90d
            "with_feedback_count": int,   # distinct location_ids w/ any location_feedback event
            "trends": {                   # 7-day deltas (positive = growth)
                "total_locations": int,
                "fresh_count": int,
                "with_feedback_count": int,
            }
        }

    Five of the six cards are derived from a single SQL query (no
    round-trip cost beyond the first). The sixth (with_feedback_count)
    reads the local audit-log persistence layer — fast SQLite scan,
    no Postgres hit.

    Trend deltas are best-effort: locations + fresh trends require
    `last_validated_at` history (which we have today via the column);
    feedback trend reads timestamps off the local events. If the
    underlying data is missing, trends return 0 rather than raising
    — the cards still render, just without the trend pill.
    """
    counts_sql = """
    SELECT
      (SELECT COUNT(*) FROM locations) AS total_locations,
      (SELECT COUNT(*) FROM services) AS total_services,
      (SELECT COUNT(*) FROM locations
        WHERE last_validated_at >= CURRENT_DATE - :fresh_days * INTERVAL '1 day'
      ) AS fresh_count,
      (SELECT COUNT(*) FROM locations WHERE last_validated_at IS NULL
      ) AS never_verified_count,
      (SELECT COUNT(*) FROM locations
        WHERE last_validated_at < CURRENT_DATE - :fresh_days * INTERVAL '1 day'
      ) AS stale_count,
      (SELECT COUNT(*) FROM locations
        WHERE last_validated_at >= CURRENT_DATE - INTERVAL '7 days'
      ) AS fresh_last_7d,
      (SELECT COUNT(*) FROM locations
        WHERE last_validated_at >= CURRENT_DATE - INTERVAL '14 days'
          AND last_validated_at <  CURRENT_DATE - INTERVAL '7 days'
      ) AS fresh_prev_7d
    """

    rows = _execute_sql(counts_sql, {"fresh_days": FRESHNESS_THRESHOLD_DAYS})
    if not rows:
        # Defensive — _execute_sql returns [] on connection failure.
        # Caller wraps in try/except and surfaces a structured 500.
        raise RuntimeError("locations stats query returned no rows")
    row = rows[0]

    # with_feedback_count + feedback trend from the local audit log.
    # Generic events table is keyed by `type`; idx_events_type carries
    # the WHERE so this is fast even at 10k+ events.
    feedback_events = _get_events_capped("location_feedback")
    distinct_loc_ids = {ev.get("location_id") for ev in feedback_events if ev.get("location_id")}

    cutoff_7d = datetime.now(timezone.utc) - timedelta(days=7)
    cutoff_14d = datetime.now(timezone.utc) - timedelta(days=14)
    fb_last_7d = sum(1 for ev in feedback_events if _ev_after(ev, cutoff_7d))
    fb_prev_7d = sum(
        1 for ev in feedback_events
        if _ev_after(ev, cutoff_14d) and not _ev_after(ev, cutoff_7d)
    )

    return {
        "total_locations": int(row["total_locations"] or 0),
        "total_services": int(row["total_services"] or 0),
        "fresh_count": int(row["fresh_count"] or 0),
        "never_verified_count": int(row["never_verified_count"] or 0),
        "stale_count": int(row["stale_count"] or 0),
        "with_feedback_count": len(distinct_loc_ids),
        "trends": {
            # Total locations trend isn't available from a single
            # SELECT against the live table — it would require
            # historical snapshots we don't keep. Leave as 0; v2
            # could read from a daily-counts persistence.
            "total_locations": 0,
            "fresh_count": int(row["fresh_last_7d"] or 0) - int(row["fresh_prev_7d"] or 0),
            "with_feedback_count": fb_last_7d - fb_prev_7d,
        },
    }


def _parse_iso_ts(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp string into a tz-aware datetime.

    Returns None for empty/None/malformed inputs — callers decide how
    to handle the absent case (typically "treat as long ago" or "skip
    this event"). Always returns tz-aware: naive timestamps get tagged
    UTC (matches the convention in `_now_iso()` in audit_log.py,
    which always writes tz-aware values).

    Exists so the audit-log walks can do datetime comparisons rather
    than string comparisons. String compare happens to give the right
    answer when both sides come from `datetime.now(timezone.utc).isoformat()`
    (same format → same lexicographic ordering as chronological), but
    silently breaks the moment any one timestamp has different
    fractional-second precision, a different tz-suffix shape, or a
    space separator instead of T. Defensive against future drift.
    """
    if not ts:
        return None
    try:
        # `Z` suffix isn't accepted by fromisoformat() pre-3.11 — replace
        # for backward compatibility. No-op on already-correct strings.
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _ev_after(ev: dict, cutoff: datetime) -> bool:
    """Helper: did `ev` happen after `cutoff`?

    Audit events store ISO8601 timestamps. Tolerate both timezone-aware
    and naive timestamps (older events were logged without tz info).
    Naive timestamps are assumed to be UTC, matching the convention
    in `_now_iso()` in audit_log.py.
    """
    dt = _parse_iso_ts(ev.get("timestamp"))
    if dt is None:
        return False
    return dt >= cutoff


# ---------------------------------------------------------------------------
# SECTION 2b — "NEEDS REVIEW" SORTABLE FILTERABLE TABLE
# ---------------------------------------------------------------------------

# Whitelisted sort keys — must map to a SQL fragment. Hardcoded
# rather than passing user input through to ORDER BY to prevent
# injection. Each key has a default direction baked in (the most
# operationally useful direction); the API supports overriding via
# `sort_dir` param.
_SORT_KEY_TO_SQL = {
    "name": "l.name",
    "organization": "o.name",
    "city": "pa.city",
    "service_count": "service_count",
    "last_validated_at": "l.last_validated_at",
    "recent_flags": "recent_flags",
}

# Whitelisted boroughs — same character class the chat side uses
# (city column, since the schema has no native borough column).
# "Other" matches anything outside this set — typically out-of-NYC
# entries that shouldn't be there.
NYC_BOROUGHS = ("Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island")


# Per-column cache for the generated CASE SQL. Two call sites
# ("pa.city" and "pa2.city") would otherwise rebuild the ~80-branch
# string on every page load. Built lazily on first use rather than
# at import time because _get_city_to_borough's lazy singleton is
# itself constructed on first call.
_BOROUGH_CASE_SQL_CACHE: dict[str, str] = {}

# Cached admin-extended city→borough mapping. Built lazily.
_ADMIN_CITY_TO_BOROUGH: Optional[dict[str, str]] = None


# Admin-only supplements to the chat-side city→borough mapping.
#
# The chat side's NYC_LOCATION_ALIASES covers neighborhoods that
# users type into chat queries — comprehensive for Manhattan, decent
# for Brooklyn and Queens, sparse for Bronx, and zero entries for
# Staten Island. The admin needs to bucket every pa.city value that
# appears in production service data, which is a different set:
# pa.city tends to hold the *city/town* label rather than user-typed
# search terms, so a different set of names shows up.
#
# Adding entries upstream in NYC_LOCATION_ALIASES would also work,
# but the chat side has a coupled `NEIGHBORHOOD_CENTERS` table that
# requires lat/lon coordinates for every alias (enforced by
# `test_all_neighborhoods_have_coordinates`). Researching ~80
# coordinate pairs is more work than this fix calls for. If a future
# chat-side feature wants proximity search on any of these, promote
# them up with coordinates at that point.
#
# All keys are lowercase (the lookup does .lower() on input) and
# correspond to one of NYC_BOROUGHS. Ambiguous names that exist in
# multiple boroughs are deliberately omitted — see _AMBIGUOUS_NAMES
# below for the audit trail.
_ADMIN_EXTRA_CITY_ALIASES: dict[str, str] = {
    # ── Manhattan ─────────────────────────────────────────────────
    # NYC_LOCATION_ALIASES already has most well-known Manhattan
    # neighborhoods (Harlem, Midtown, SoHo, Tribeca, etc). Filling
    # in the obvious gaps.
    "morningside heights": "Manhattan",   # Columbia area
    "hamilton heights":    "Manhattan",
    "battery park city":   "Manhattan",   # distinct from "battery park"
    "roosevelt island":    "Manhattan",
    "marble hill":         "Manhattan",   # political-Manhattan, geographic-Bronx
    "two bridges":         "Manhattan",
    "alphabet city":       "Manhattan",
    "flatiron":            "Manhattan",
    "flatiron district":   "Manhattan",
    "meatpacking district":"Manhattan",
    "bowery":              "Manhattan",
    "stuyvesant town":     "Manhattan",
    "civic center":        "Manhattan",
    "lower manhattan":     "Manhattan",
    "midtown manhattan":   "Manhattan",

    # ── Brooklyn ─────────────────────────────────────────────────
    "greenpoint":          "Brooklyn",
    "coney island":        "Brooklyn",
    "bensonhurst":         "Brooklyn",
    "borough park":        "Brooklyn",
    "boro park":           "Brooklyn",    # common spelling variant
    "brooklyn heights":    "Brooklyn",
    "carroll gardens":     "Brooklyn",
    "boerum hill":         "Brooklyn",
    "clinton hill":        "Brooklyn",
    "canarsie":            "Brooklyn",
    "sheepshead bay":      "Brooklyn",
    "midwood":             "Brooklyn",
    "gravesend":           "Brooklyn",
    "dyker heights":       "Brooklyn",
    "bath beach":          "Brooklyn",
    "mill basin":          "Brooklyn",
    "marine park":         "Brooklyn",
    "ditmas park":         "Brooklyn",
    "windsor terrace":     "Brooklyn",
    "prospect lefferts gardens": "Brooklyn",
    "gowanus":             "Brooklyn",
    "brighton beach":      "Brooklyn",
    "manhattan beach":     "Brooklyn",    # IS in Brooklyn despite the name
    "sea gate":            "Brooklyn",
    "vinegar hill":        "Brooklyn",
    "starrett city":       "Brooklyn",
    "spring creek":        "Brooklyn",
    "weeksville":          "Brooklyn",

    # ── Queens ───────────────────────────────────────────────────
    "forest hills":        "Queens",
    "rego park":           "Queens",
    "kew gardens":         "Queens",
    "richmond hill":       "Queens",
    "south richmond hill": "Queens",
    "south ozone park":    "Queens",
    "ozone park":          "Queens",      # distinct from south ozone park
    "bayside":             "Queens",
    "sunnyside":           "Queens",
    "arverne":             "Queens",
    "laurelton":           "Queens",
    "corona (queens)":     "Queens",      # parenthetical variant in data
    "jamaica (queens)":    "Queens",      # parenthetical variant
    "long island city (queens)": "Queens", # parenthetical variant
    "maspeth":             "Queens",
    "middle village":      "Queens",
    "glendale":            "Queens",
    "howard beach":        "Queens",
    "woodhaven":           "Queens",      # distinct from woodside
    "rosedale":            "Queens",
    "st. albans":          "Queens",
    "st albans":           "Queens",
    "saint albans":        "Queens",      # spelled-out variant in data
    "hollis":              "Queens",
    "queens village":      "Queens",
    "cambria heights":     "Queens",
    "briarwood":           "Queens",
    "fresh meadows":       "Queens",
    "whitestone":          "Queens",
    "college point":       "Queens",
    "floral park":         "Queens",      # NYC-side; Nassau also has one
    "bellerose":           "Queens",      # NYC-side; Nassau also has one
    "glen oaks":           "Queens",
    "douglaston":          "Queens",
    "little neck":         "Queens",
    "springfield gardens": "Queens",
    "rockaway beach":      "Queens",
    "rockaway park":       "Queens",
    "belle harbor":        "Queens",
    "east elmhurst":       "Queens",
    "kew gardens hills":   "Queens",      # distinct from kew gardens
    "lic":                 "Queens",      # common abbreviation
    "auburndale":          "Queens",
    "beechhurst":          "Queens",
    "broad channel":       "Queens",
    "edgemere":            "Queens",
    "hammels":             "Queens",
    "pomonok":             "Queens",
    "rochdale":            "Queens",      # rochdale village

    # ── Bronx ────────────────────────────────────────────────────
    # NYC_LOCATION_ALIASES has only 5 Bronx neighborhoods (south
    # bronx, mott haven, fordham, hunts point, morrisania). Filling
    # the rest.
    "riverdale":           "Bronx",       # NYC-side; NJ also has one
    "kingsbridge":         "Bronx",
    "pelham bay":          "Bronx",
    "pelham parkway":      "Bronx",
    "throgs neck":         "Bronx",
    "castle hill":         "Bronx",
    "soundview":           "Bronx",
    "belmont":             "Bronx",       # Little Italy of the Bronx
    "tremont":             "Bronx",
    "east tremont":        "Bronx",
    "west tremont":        "Bronx",
    "university heights":  "Bronx",
    "highbridge":          "Bronx",
    "bedford park":        "Bronx",
    "norwood":             "Bronx",
    "wakefield":           "Bronx",
    "city island":         "Bronx",
    "co-op city":          "Bronx",
    "coop city":           "Bronx",       # variant spelling
    "williamsbridge":      "Bronx",
    "allerton":            "Bronx",
    "eastchester":         "Bronx",
    "woodlawn":            "Bronx",
    "baychester":          "Bronx",
    "parkchester":         "Bronx",
    "concourse":           "Bronx",       # Grand Concourse area
    "melrose":             "Bronx",
    "claremont":           "Bronx",
    "morris park":         "Bronx",
    "morris heights":      "Bronx",
    "longwood":            "Bronx",
    "port morris":         "Bronx",
    "kingsbridge heights": "Bronx",
    "spuyten duyvil":      "Bronx",
    "edenwald":            "Bronx",
    "van nest":            "Bronx",
    "country club":        "Bronx",
    "schuylerville":       "Bronx",
    "unionport":           "Bronx",

    # ── Staten Island ────────────────────────────────────────────
    # NYC_LOCATION_ALIASES has ZERO Staten Island neighborhoods.
    # Every SI neighborhood currently falls to "Other" pre-fix.
    "st. george":          "Staten Island",
    "st george":           "Staten Island",
    "tompkinsville":       "Staten Island",
    "stapleton":           "Staten Island",
    "new dorp":            "Staten Island",
    "great kills":         "Staten Island",
    "tottenville":         "Staten Island",
    "richmondtown":        "Staten Island",
    "richmond town":       "Staten Island",
    "eltingville":         "Staten Island",
    "annadale":            "Staten Island",
    "huguenot":            "Staten Island",
    "pleasant plains":     "Staten Island",
    "dongan hills":        "Staten Island",
    "grant city":          "Staten Island",
    "clifton":             "Staten Island",
    "rosebank":            "Staten Island",
    "arrochar":            "Staten Island",
    "midland beach":       "Staten Island",
    "south beach":         "Staten Island",
    "new springville":     "Staten Island",
    "bulls head":          "Staten Island",
    "travis":              "Staten Island",
    "westerleigh":         "Staten Island",
    "port richmond":       "Staten Island",
    "mariners harbor":     "Staten Island",
    "charleston":          "Staten Island",
    "rossville":           "Staten Island",
    "woodrow":             "Staten Island",
    "concord":             "Staten Island",
    "castleton corners":   "Staten Island",
    "new brighton":        "Staten Island",
    "west brighton":       "Staten Island",
    "fort wadsworth":      "Staten Island",
    "todt hill":           "Staten Island",
    "emerson hill":        "Staten Island",
    "graniteville":        "Staten Island",
    "willowbrook":         "Staten Island",
    "egbertville":         "Staten Island",
    "oakwood":             "Staten Island",
    "prince's bay":        "Staten Island",
    "princes bay":         "Staten Island",
    "richmond valley":     "Staten Island",
    "arden heights":       "Staten Island",
    "great kills park":    "Staten Island",
    "lighthouse hill":     "Staten Island",
    "old town":            "Staten Island",
    "silver lake":         "Staten Island",
    "sunnyside (si)":      "Staten Island", # disambiguating variant if it ever appears
}


# Audit trail for neighborhood names that exist in MULTIPLE NYC
# boroughs and are therefore NOT in the mapping above. If pa.city
# uses one of these without disambiguation, the location will fall
# into "Other" — that's the safest behavior given we can't tell which
# borough is meant from the city name alone.
#
# Listed here as documentation so future maintainers don't accidentally
# "fix" the omission by picking one borough arbitrarily.
_AMBIGUOUS_NEIGHBORHOOD_NAMES: dict[str, list[str]] = {
    "chelsea":     ["Manhattan", "Staten Island"],  # SI's is tiny; Manhattan win
    "murray hill": ["Manhattan", "Queens"],         # Manhattan's wins by usage
    "bay terrace": ["Queens", "Staten Island"],     # truly ambiguous — skip
    "south beach": ["Staten Island", "...other"],   # SI in NYC context
    "sunnyside":   ["Queens", "Staten Island"],     # Queens wins by usage
    "concord":     ["Staten Island", "...other"],   # generic name elsewhere
}
# (Chelsea and Murray Hill are *already* in NYC_LOCATION_ALIASES
# mapped to Manhattan — that wins by usage volume. Bay Terrace is
# omitted from both maps. Sunnyside and Concord are mapped to their
# usage-winning borough; the SI variants are uncommon enough to
# accept the small false-negative rate.)


def _get_admin_city_to_borough() -> dict[str, str]:
    """Admin-extended city→borough mapping. Single source of truth
    used by both `_borough_case_sql` (for SQL generation) and
    `_bucket_city_to_borough` (for Python parity).

    Composition:
        1. Chat-side `get_nyc_city_to_borough()` (NYC_LOCATION_ALIASES
           inverted to lowercased-city → canonical-borough).
        2. The five canonical borough names as keys mapping to
           themselves — for when pa.city is literally "Manhattan",
           "Queens", etc. The chat side doesn't need these because
           users don't type them, but the DB does store them.
        3. `_ADMIN_EXTRA_CITY_ALIASES` (above) for production
           pa.city values not covered by the chat-side aliases.

    Memoized — the merge runs at most once per process.
    """
    global _ADMIN_CITY_TO_BOROUGH
    if _ADMIN_CITY_TO_BOROUGH is not None:
        return _ADMIN_CITY_TO_BOROUGH

    from app.rag.query_executor import get_nyc_city_to_borough
    mapping = dict(get_nyc_city_to_borough())

    # Borough names as their own keys. Use setdefault so we don't
    # accidentally clobber a chat-side entry (none should exist for
    # these lowercased borough names, but be defensive).
    for borough in NYC_BOROUGHS:
        mapping.setdefault(borough.lower(), borough)

    # Admin-specific Queens neighborhoods.
    for alias, borough in _ADMIN_EXTRA_CITY_ALIASES.items():
        mapping.setdefault(alias, borough)

    _ADMIN_CITY_TO_BOROUGH = mapping
    return mapping


def _bucket_city_to_borough(city: Optional[str]) -> str:
    """Python equivalent of the SQL CASE produced by `_borough_case_sql`.

    Same input → same output as evaluating the CASE inside Postgres.
    Use this when bucketing in Python rather than SQL (e.g. unit tests
    that want to pin the bucketing behavior end-to-end without
    spinning up a DB, or any future caller that's already iterating
    rows in Python).

    Returns one of NYC_BOROUGHS or "Other"; never returns None.
    """
    if not city or not city.strip():
        return "Other"
    mapping = _get_admin_city_to_borough()
    return mapping.get(city.strip().lower(), "Other")


def _borough_case_sql(city_col: str) -> str:
    """Generate the SQL CASE expression that maps a `city` column to
    one of NYC_BOROUGHS or "Other".

    Returns a bare CASE expression — caller adds AS borough or wraps
    in a JOIN. The expression uses the same ~80-entry city→borough
    mapping `_bucket_city_to_borough` uses (chat-side aliases plus
    admin-specific supplements), so case variants ("BROOKLYN"),
    aliases ("The Bronx"), and Queens neighborhoods that appear
    directly in `pa.city` ("Astoria", "Flushing", "Jamaica", "Long
    Island City", "Forest Hills", and so on) bucket to their canonical
    borough rather than collapsing into "Other".

    Without this comprehensive mapping (the previous implementation
    did an exact-string, case-sensitive IN against five canonical
    borough names), the deployed heatmap silently routed:
        - Every case variant ("BROOKLYN", "STATEN ISLAND", ...) to Other
        - Every alias ("The Bronx") to Other
        - Every Queens neighborhood to Other (the Queens column in the
          heatmap showed only locations where pa.city was the literal
          string "Queens" — almost none in production)
        - Every Manhattan location where pa.city was "New York" (the
          common Streetlives value) to Other
    The visible symptom was a heatmap with a tiny Queens column, a
    near-empty Manhattan column, and an "Other" bucket that conflated
    real-NYC-by-neighborhood with genuinely non-NYC cities.

    `city_col` is interpolated into the SQL unescaped — pass it from
    a fixed call site, never from user input. (All callers in this
    module pass literal column references like "pa.city" or "pa2.city".)
    City names and borough names come from the hand-curated Python
    constants in NYC_LOCATION_ALIASES + _ADMIN_EXTRA_CITY_ALIASES; we
    still single-quote-escape each one as defense in depth.

    Build is cached per `city_col` value: with two call sites in the
    module, the generated string is built at most twice per process
    lifetime.
    """
    cached = _BOROUGH_CASE_SQL_CACHE.get(city_col)
    if cached is not None:
        return cached

    mapping = _get_admin_city_to_borough()

    # Sort for deterministic output — easier to diff in logs and
    # snapshot tests, and gives the SQL planner a stable string to
    # hash for plan reuse.
    cases: list[str] = []
    for city in sorted(mapping.keys()):
        borough = mapping[city]
        # The keys in `mapping` are already lowercase. Escape single
        # quotes defensively even though current data has none; one
        # rogue entry later shouldn't break the SQL.
        safe_city = city.replace("'", "''")
        safe_borough = borough.replace("'", "''")
        cases.append(
            f"WHEN LOWER(TRIM({city_col})) = '{safe_city}' "
            f"THEN '{safe_borough}'"
        )

    sql = f"CASE {' '.join(cases)} ELSE 'Other' END"
    _BOROUGH_CASE_SQL_CACHE[city_col] = sql
    return sql


def get_locations_list(
    page: int = 1,
    page_size: int = 25,
    sort_key: str = "last_validated_at",
    sort_dir: str = "asc_nulls_first",
    borough: Optional[list[str]] = None,
    age_bucket: Optional[str] = None,
    has_issues: bool = False,
    category: Optional[list[str]] = None,
    search: Optional[str] = None,
) -> dict:
    """Paginated, sortable, filterable list of locations for triage.

    Returns shape:
        {
            "locations": [
                {
                    "location_id": str,
                    "location_name": str,
                    "organization": str | None,
                    "city": str | None,
                    "borough": str,            # one of NYC_BOROUGHS or "Other"
                    "service_count": int,
                    "service_categories": list[str],   # top 3 distinct
                    "service_categories_more": int,    # remainder count
                    "last_validated_at": str | None,   # ISO8601 or None
                    "has_phone": bool,
                    "has_address": bool,
                    "has_hours": bool,
                    "has_reviews": bool,               # any feedback ever
                    "recent_flags": int,               # negative criteria within RECENT_FLAGS_LOOKBACK_DAYS
                    "yourpeer_url": str,               # https://yourpeer.nyc/locations/<slug>
                },
                ...
            ],
            "total": int,                  # total matching count (for pagination)
            "page": int,
            "page_size": int,
        }

    Defaults match the spec: triage view, most-stale-first, no
    filters. The query is structured so all filtering happens in
    the WHERE clause (one round-trip), and pagination is OFFSET/
    LIMIT on the same query (fine at 2,400 rows; page_size capped
    at 100 by the route).
    """
    # Validate sort key — anything else is a coding bug, not user input
    # (the route already 422s unknown values via Query enum), but
    # defensively reject here too.
    if sort_key not in _SORT_KEY_TO_SQL:
        sort_key = "last_validated_at"
    sort_sql = _SORT_KEY_TO_SQL[sort_key]

    # Sort direction interpretation. The default "asc_nulls_first"
    # surfaces never-verified locations at the top of the triage
    # view, which is the user's stated preference. Other directions
    # are supported for non-default sorts.
    sort_dir_sql = {
        "asc": "ASC NULLS LAST",
        "desc": "DESC NULLS LAST",
        "asc_nulls_first": "ASC NULLS FIRST",
        "desc_nulls_first": "DESC NULLS FIRST",
    }.get(sort_dir, "ASC NULLS FIRST")

    # WHERE clauses + bound params built additively
    where_clauses: list[str] = []
    params: dict[str, Any] = {}

    if borough:
        # Filter on physical_addresses.city. "Other" (anything not in
        # NYC_BOROUGHS) is supported as an explicit option.
        nyc_set = [b for b in borough if b in NYC_BOROUGHS]
        wants_other = "Other" in borough
        clauses_or: list[str] = []
        if nyc_set:
            clauses_or.append("pa.city = ANY(:borough_list)")
            params["borough_list"] = nyc_set
        if wants_other:
            clauses_or.append("(pa.city IS NULL OR NOT (pa.city = ANY(:nyc_full_list)))")
            params["nyc_full_list"] = list(NYC_BOROUGHS)
        if clauses_or:
            where_clauses.append("(" + " OR ".join(clauses_or) + ")")

    # age_bucket filters on last_validated_at against fixed thresholds.
    # Uses CURRENT_DATE - INTERVAL for index-friendly comparisons.
    age_clause = {
        "lt30": "l.last_validated_at >= CURRENT_DATE - INTERVAL '30 days'",
        "30to90": (
            "l.last_validated_at <  CURRENT_DATE - INTERVAL '30 days' AND "
            "l.last_validated_at >= CURRENT_DATE - INTERVAL '90 days'"
        ),
        "90to180": (
            "l.last_validated_at <  CURRENT_DATE - INTERVAL '90 days' AND "
            "l.last_validated_at >= CURRENT_DATE - INTERVAL '180 days'"
        ),
        "180to365": (
            "l.last_validated_at <  CURRENT_DATE - INTERVAL '180 days' AND "
            "l.last_validated_at >= CURRENT_DATE - INTERVAL '365 days'"
        ),
        "gt365": "l.last_validated_at < CURRENT_DATE - INTERVAL '365 days'",
        "never": "l.last_validated_at IS NULL",
    }.get(age_bucket or "")
    if age_clause:
        where_clauses.append(age_clause)

    # Compute "recent_flags" + "has_reviews" from the audit log
    # upfront — we need recent_flags both for the has_issues filter
    # (below, in the WHERE clause) AND for the per-row recent_flags
    # column in the response. Walking the events once is cheaper than
    # twice.
    fb_events = _get_events_capped("location_feedback")
    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_FLAGS_LOOKBACK_DAYS)
    recent_flags_by_loc: dict[str, int] = defaultdict(int)
    has_reviews_by_loc: set[str] = set()
    for ev in fb_events:
        loc_id = ev.get("location_id")
        if not loc_id:
            continue
        loc_id = str(loc_id)
        has_reviews_by_loc.add(loc_id)
        if _ev_after(ev, cutoff) and _has_negative_criterion(ev):
            recent_flags_by_loc[loc_id] += 1

    if search:
        where_clauses.append(
            "(LOWER(l.name) LIKE :search_pat OR LOWER(o.name) LIKE :search_pat)"
        )
        params["search_pat"] = f"%{search.lower()}%"

    if category:
        # Category filter: location must have AT LEAST ONE service in
        # any of the named categories. EXISTS subquery so the predicate
        # short-circuits per location.
        #
        # Validate names against the taxonomies table first — admins
        # type these from URL bars / saved filters / autocomplete, and
        # a typo silently produces zero results with no signal that
        # the filter itself was wrong. Validation isn't enforced at
        # the route layer (taxonomy names are DB-driven, not enumerable
        # at typing time), so it goes here.
        #
        # Strategy: query the taxonomies table for the supplied names,
        # log a warning for unmatched ones, and use only the matched
        # subset in the actual filter. If the user supplied N names and
        # none matched, the resulting filter binds an empty array →
        # Postgres matches no rows → empty result page. That's the
        # right semantic (user asked to filter, filter has no valid
        # values, so nothing passes) but the warning makes the cause
        # visible in logs.
        validation_sql = "SELECT name FROM taxonomies WHERE name = ANY(:names)"
        validation_rows = _execute_sql(validation_sql, {"names": list(category)})
        valid_names = {r.get("name") for r in validation_rows if r.get("name")}
        invalid_names = [c for c in category if c not in valid_names]
        if invalid_names:
            logger.warning(
                "locations_admin: /list received unknown category name(s) %r — "
                "filter will use only the %d valid name(s) %r. Likely a "
                "typo or stale UI value; check the taxonomies table.",
                invalid_names,
                len(valid_names),
                sorted(valid_names),
            )
        where_clauses.append("""
        EXISTS (
            SELECT 1 FROM service_at_locations sal_cat
            JOIN service_taxonomy st_cat ON sal_cat.service_id = st_cat.service_id
            JOIN taxonomies t_cat ON st_cat.taxonomy_id = t_cat.id
            WHERE sal_cat.location_id = l.id
              AND t_cat.name = ANY(:category_list)
        )
        """)
        # Use the validated subset. Preserves the order/duplicates of
        # the user's input for valid names; drops invalid ones.
        params["category_list"] = [c for c in category if c in valid_names]

    # has_issues toggle: missing phone OR missing address OR missing
    # hours OR has any negative recent feedback (recent_flags > 0).
    # Union so it surfaces every location that needs SOME kind of
    # attention. recent_flags isn't a SQL-side concept — it's
    # computed from the audit log — so we feed the flagged
    # location_ids in as a bound array.
    if has_issues:
        flagged_loc_ids = list(recent_flags_by_loc.keys())
        where_clauses.append("""
        (
            best_phone.number IS NULL
            OR pa.address_1 IS NULL
            OR NOT EXISTS (
                SELECT 1 FROM service_at_locations sal_h
                JOIN regular_schedules rs ON rs.service_id = sal_h.service_id
                WHERE sal_h.location_id = l.id
                LIMIT 1
            )
            OR l.id::text = ANY(:flagged_loc_ids)
        )
        """)
        # Even when flagged_loc_ids is empty, binding an empty array
        # keeps the SQL identical across requests (Postgres handles
        # `= ANY(ARRAY[]::text[])` correctly — matches nothing).
        params["flagged_loc_ids"] = flagged_loc_ids

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # The list query — returns rows for the current page.
    # Notes on the JOIN tree:
    #   * physical_addresses is LEFT JOIN because not all locations
    #     have address rows (data quality issue we want to surface,
    #     not exclude).
    #   * best_phone is the same pattern used by the chat-side query
    #     to pick a single phone per location; reused here for
    #     consistency. Inlined below as a LATERAL — easier to read.
    #   * service_count stays inline as a scalar subquery because
    #     it's also a sort key (ORDER BY service_count means the
    #     value has to be visible in the SELECT projection at sort
    #     time). Cheap when service_at_locations.location_id is
    #     indexed; bounded by page_size at evaluation time.
    #   * has_hours stays inline as an EXISTS — cheap because of
    #     early-exit on the first matching row.
    #   * The HEAVIER per-row aggregates (top_categories and
    #     distinct_categories_count) used to live here as scalar
    #     subqueries too, but they walked service_taxonomy ×
    #     taxonomies × service_at_locations per row. They've been
    #     extracted into a single `enrich_sql` query below, keyed
    #     by the page's location_ids — replacing O(page_size × ~5
    #     joins) subquery work with one O(1) query.
    list_sql = f"""
    SELECT
        l.id::text                 AS location_id,
        l.name                     AS location_name,
        l.slug                     AS location_slug,
        o.name                     AS organization,
        pa.city                    AS city,
        pa.address_1               AS address_1,
        l.last_validated_at        AS last_validated_at,
        best_phone.number          AS phone_number,

        (SELECT COUNT(*) FROM service_at_locations sal2
         WHERE sal2.location_id = l.id) AS service_count,

        EXISTS (
            SELECT 1 FROM service_at_locations sal_h
            JOIN regular_schedules rs ON rs.service_id = sal_h.service_id
            WHERE sal_h.location_id = l.id
            LIMIT 1
        ) AS has_hours

    FROM locations l
    JOIN organizations o ON l.organization_id = o.id
    LEFT JOIN physical_addresses pa ON pa.location_id = l.id
    LEFT JOIN LATERAL (
        SELECT p.number FROM phones p
        WHERE p.location_id = l.id
        ORDER BY p.id LIMIT 1
    ) best_phone ON true
    {where_sql}
    ORDER BY {sort_sql} {sort_dir_sql}, l.id
    LIMIT :limit OFFSET :offset
    """

    # Total count for pagination — same WHERE, no JOINs except the
    # ones referenced in WHERE. Run as a separate query because the
    # `LIMIT/OFFSET` would otherwise distort the count.
    count_sql = f"""
    SELECT COUNT(*) AS total
    FROM locations l
    JOIN organizations o ON l.organization_id = o.id
    LEFT JOIN physical_addresses pa ON pa.location_id = l.id
    LEFT JOIN LATERAL (
        SELECT p.number FROM phones p
        WHERE p.location_id = l.id
        ORDER BY p.id LIMIT 1
    ) best_phone ON true
    {where_sql}
    """

    page = max(1, page)
    page_size = max(1, min(100, page_size))
    params["limit"] = page_size
    params["offset"] = (page - 1) * page_size

    list_rows = _execute_sql(list_sql, params)
    count_rows = _execute_sql(count_sql, {k: v for k, v in params.items() if k not in ("limit", "offset")})
    total = int(count_rows[0]["total"]) if count_rows else 0

    # Enrichment query: fetch top_categories + distinct_categories_count
    # for this page's locations in a SINGLE query, keyed by the page's
    # location_ids. Used to be two correlated subqueries in list_sql
    # (each one ran per row of the result page); pulling them out
    # makes the cost predictable and lets the planner do a single
    # well-indexed scan of service_taxonomy.
    #
    # The CTE ranks taxonomies per location by service count (most
    # common first), then ARRAY_AGGs the top 3 names and counts the
    # distinct total. One row out per page location.
    page_ids = [str(r.get("location_id")) for r in list_rows if r.get("location_id")]
    enrichment_by_loc: dict[str, dict[str, Any]] = {}
    if page_ids:
        enrich_sql = """
        WITH ranked AS (
            SELECT
                sal.location_id,
                t.name,
                COUNT(*) AS n,
                ROW_NUMBER() OVER (
                    PARTITION BY sal.location_id
                    ORDER BY COUNT(*) DESC, t.name
                ) AS rn
            FROM service_at_locations sal
            JOIN service_taxonomy st ON st.service_id = sal.service_id
            JOIN taxonomies t ON t.id = st.taxonomy_id
            WHERE sal.location_id::text = ANY(:page_ids)
            GROUP BY sal.location_id, t.name
        )
        SELECT
            location_id::text AS location_id,
            ARRAY_AGG(name ORDER BY rn) FILTER (WHERE rn <= 3) AS top_categories,
            COUNT(DISTINCT name) AS distinct_categories_count
        FROM ranked
        GROUP BY location_id
        """
        for r in _execute_sql(enrich_sql, {"page_ids": page_ids}):
            loc_id = str(r.get("location_id", ""))
            if not loc_id:
                continue
            enrichment_by_loc[loc_id] = {
                "top_categories": list(r.get("top_categories") or []),
                "distinct_categories_count": int(r.get("distinct_categories_count") or 0),
            }

    # recent_flags_by_loc + has_reviews_by_loc were computed upfront
    # so the has_issues filter could feed flagged ids into the WHERE
    # clause. They're re-used here for the per-row response columns.

    locations: list[dict] = []
    for r in list_rows:
        loc_id = str(r.get("location_id", ""))
        city = r.get("city")
        borough_label = city if city in NYC_BOROUGHS else "Other"
        # Per-location enrichments — defaults handle the "location has
        # no service_at_locations rows" case (rare; means the catalog
        # lists the location but with zero offered services).
        enrich = enrichment_by_loc.get(loc_id, {})
        top_cats: list[str] = enrich.get("top_categories", [])
        distinct_total = enrich.get("distinct_categories_count", 0)
        more = max(0, distinct_total - len(top_cats))

        last_v = r.get("last_validated_at")
        last_v_iso: Optional[str]
        if last_v is None:
            last_v_iso = None
        elif hasattr(last_v, "isoformat"):
            last_v_iso = last_v.isoformat()
        else:
            last_v_iso = str(last_v)

        slug = r.get("location_slug") or loc_id

        locations.append({
            "location_id": loc_id,
            "location_name": r.get("location_name") or "Unknown",
            "organization": r.get("organization"),
            "city": city,
            "borough": borough_label,
            "service_count": int(r.get("service_count") or 0),
            "service_categories": top_cats,
            "service_categories_more": more,
            "last_validated_at": last_v_iso,
            "has_phone": bool(r.get("phone_number")),
            "has_address": bool(r.get("address_1")),
            "has_hours": bool(r.get("has_hours")),
            "has_reviews": loc_id in has_reviews_by_loc,
            "recent_flags": recent_flags_by_loc.get(loc_id, 0),
            "yourpeer_url": f"https://yourpeer.nyc/locations/{slug}",
        })

    return {
        "locations": locations,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _has_negative_criterion(event: dict) -> bool:
    """A location_feedback event 'flags' the location if any criterion
    rated it negatively. The schema in audit_log.log_location_feedback
    stores criteria booleans where True = positive, False = negative,
    None = not asked. We count False values."""
    ratings = event.get("ratings") or {}
    return any(v is False for v in ratings.values())


# ---------------------------------------------------------------------------
# SECTION 2a — FRESHNESS HISTOGRAM
# ---------------------------------------------------------------------------

# Bucket boundaries for the histogram, in days. Each tuple is
# (bucket_key, max_days_inclusive, label_for_display). The "never"
# bucket is appended separately since it's a NULL test rather than
# a numeric range. Cumulative-style boundaries (every row counts in
# exactly one bucket) — the SQL CASE is structured to make that true.
_FRESHNESS_BUCKETS = (
    ("lt30",      30,  "<30d"),
    ("30to90",    90,  "30–90d"),
    ("90to180",  180,  "90–180d"),
    ("180to365", 365,  "180d–1y"),
    ("gt365",   None,  ">1y"),       # None = no upper bound
)


@ttl_cached()
def get_freshness_histogram() -> dict:
    """Section 2a: freshness distribution as 6 buckets.

    Returns shape:
        {
            "buckets": [
                {"key": "lt30",      "label": "<30d",     "count": int},
                {"key": "30to90",    "label": "30-90d",   "count": int},
                {"key": "90to180",   "label": "90-180d",  "count": int},
                {"key": "180to365",  "label": "180d-1y",  "count": int},
                {"key": "gt365",     "label": ">1y",      "count": int},
                {"key": "never",     "label": "Never",    "count": int},
            ],
            "total": int,
        }

    Single-query implementation using SQL CASE so each location is
    counted in exactly one bucket. The bucket keys match the
    `age_bucket` filter values on `/list`, so a click on a bar can
    drive the table filter without translation. Order is preserved
    by listing buckets in the response array — the frontend renders
    in array order.
    """
    sql = """
    SELECT
      COUNT(*) FILTER (
        WHERE last_validated_at >= CURRENT_DATE - INTERVAL '30 days'
      ) AS lt30,
      COUNT(*) FILTER (
        WHERE last_validated_at <  CURRENT_DATE - INTERVAL '30 days'
          AND last_validated_at >= CURRENT_DATE - INTERVAL '90 days'
      ) AS m_30to90,
      COUNT(*) FILTER (
        WHERE last_validated_at <  CURRENT_DATE - INTERVAL '90 days'
          AND last_validated_at >= CURRENT_DATE - INTERVAL '180 days'
      ) AS m_90to180,
      COUNT(*) FILTER (
        WHERE last_validated_at <  CURRENT_DATE - INTERVAL '180 days'
          AND last_validated_at >= CURRENT_DATE - INTERVAL '365 days'
      ) AS m_180to365,
      COUNT(*) FILTER (
        WHERE last_validated_at < CURRENT_DATE - INTERVAL '365 days'
      ) AS gt365,
      COUNT(*) FILTER (WHERE last_validated_at IS NULL) AS never,
      COUNT(*) AS total
    FROM locations
    """

    rows = _execute_sql(sql, {})
    if not rows:
        raise RuntimeError("freshness histogram query returned no rows")
    row = rows[0]

    return {
        "buckets": [
            {"key": "lt30",     "label": "<30d",      "count": int(row.get("lt30") or 0)},
            {"key": "30to90",   "label": "30–90d",    "count": int(row.get("m_30to90") or 0)},
            {"key": "90to180",  "label": "90–180d",   "count": int(row.get("m_90to180") or 0)},
            {"key": "180to365", "label": "180d–1y",   "count": int(row.get("m_180to365") or 0)},
            {"key": "gt365",    "label": ">1y",       "count": int(row.get("gt365") or 0)},
            {"key": "never",    "label": "Never",     "count": int(row.get("never") or 0)},
        ],
        "total": int(row.get("total") or 0),
    }


# ---------------------------------------------------------------------------
# SECTION 3a — BOROUGH BREAKDOWN TABLE
# ---------------------------------------------------------------------------

@ttl_cached()
def get_locations_by_borough() -> dict:
    """Section 3a: per-borough rollup.

    Returns shape:
        {
            "rows": [
                {
                    "borough": "Manhattan",
                    "location_count": int,
                    "service_count": int,
                    "avg_services_per_location": float,
                    "verified_lt90d_pct": float | None,    # null when location_count == 0
                    "top_category": str | None,            # most common service-category at this borough
                },
                ...   # 5 boroughs + "Other"
            ],
            "totals": {
                "location_count": int,
                "service_count": int,
            },
        }

    Single SQL pass with GROUP BY on the same case-statement that the
    `borough` field returns in section 2b. Top-category is computed
    via a per-borough subquery — small per-borough count (6 outputs)
    so the N+1 pattern is acceptable and readable.

    Boroughs always render in a consistent order (Manhattan, Brooklyn,
    Queens, Bronx, Staten Island, Other) regardless of which appear
    in the data. Empty boroughs render with zeros, never missing.
    """
    # Group locations by their effective borough label. Bind the NYC
    # borough list so the CASE doesn't have to repeat string literals.
    grouped_sql = """
    WITH labeled AS (
        SELECT
            l.id,
            %(borough_case)s AS borough,
            l.last_validated_at
        FROM locations l
        LEFT JOIN physical_addresses pa ON pa.location_id = l.id
    )
    SELECT
        borough,
        COUNT(*) AS location_count,
        COUNT(*) FILTER (
            WHERE last_validated_at >= CURRENT_DATE - :fresh_days * INTERVAL '1 day'
        ) AS fresh_count,
        (SELECT COUNT(*) FROM service_at_locations sal
         JOIN locations l2 ON sal.location_id = l2.id
         LEFT JOIN physical_addresses pa2 ON pa2.location_id = l2.id
         WHERE %(borough_case_pa2)s = labeled.borough
        ) AS service_count
    FROM labeled
    GROUP BY borough
    """ % {
        # SQL-fragment substitution (not value interpolation): both
        # `borough_case` substitutions are SQL text returned by the
        # _borough_case_sql() helper, NOT user input. Composition like
        # this can't go through bind params because :name only handles
        # values. The helper builds its output from the NYC_BOROUGHS
        # constant so there's no injection surface.
        "borough_case": _borough_case_sql("pa.city"),
        "borough_case_pa2": _borough_case_sql("pa2.city"),
    }

    grouped_rows = _execute_sql(grouped_sql, {"fresh_days": FRESHNESS_THRESHOLD_DAYS})

    # Index by borough for the assemble step. Initialize all 6 borough
    # labels to zero so empty boroughs still render.
    by_borough: dict[str, dict[str, Any]] = {
        b: {"location_count": 0, "service_count": 0, "fresh_count": 0}
        for b in (*NYC_BOROUGHS, "Other")
    }
    for r in grouped_rows:
        b = r.get("borough") or "Other"
        if b not in by_borough:
            # Defensive — if pa.city has a value we don't recognize and
            # the CASE fell through to 'Other', we still group it
            # there. But this branch handles the unexpected case where
            # the SQL returns a label outside our predefined set.
            by_borough[b] = {"location_count": 0, "service_count": 0, "fresh_count": 0}
        by_borough[b]["location_count"] = int(r.get("location_count") or 0)
        by_borough[b]["service_count"] = int(r.get("service_count") or 0)
        by_borough[b]["fresh_count"] = int(r.get("fresh_count") or 0)

    # Top-category per borough. Run as a separate query because the
    # per-borough top-N is tricky to express cleanly inside the main
    # GROUP BY without window functions; this is more readable. Cost
    # is one extra DB round-trip — fine.
    top_cat_sql = """
    WITH labeled AS (
        SELECT
            l.id AS location_id,
            %(borough_case)s AS borough
        FROM locations l
        LEFT JOIN physical_addresses pa ON pa.location_id = l.id
    ),
    counts AS (
        SELECT
            labeled.borough,
            t.name AS category,
            COUNT(*) AS n,
            ROW_NUMBER() OVER (PARTITION BY labeled.borough ORDER BY COUNT(*) DESC, t.name) AS rn
        FROM labeled
        JOIN service_at_locations sal ON sal.location_id = labeled.location_id
        JOIN service_taxonomy st ON st.service_id = sal.service_id
        JOIN taxonomies t ON t.id = st.taxonomy_id
        GROUP BY labeled.borough, t.name
    )
    SELECT borough, category FROM counts WHERE rn = 1
    """ % {"borough_case": _borough_case_sql("pa.city")}
    top_cat_rows = _execute_sql(top_cat_sql, {})
    top_cat_by_borough = {r.get("borough"): r.get("category") for r in top_cat_rows}

    # Assemble the response in display order.
    response_rows: list[dict[str, Any]] = []
    total_loc = 0
    total_svc = 0
    for b in (*NYC_BOROUGHS, "Other"):
        bb = by_borough[b]
        loc = bb["location_count"]
        svc = bb["service_count"]
        fresh = bb["fresh_count"]
        avg = round(svc / loc, 1) if loc else 0.0
        verified_pct = round(100.0 * fresh / loc, 1) if loc else None
        response_rows.append({
            "borough": b,
            "location_count": loc,
            "service_count": svc,
            "avg_services_per_location": avg,
            "verified_lt90d_pct": verified_pct,
            "top_category": top_cat_by_borough.get(b),
        })
        total_loc += loc
        total_svc += svc

    return {
        "rows": response_rows,
        "totals": {
            "location_count": total_loc,
            "service_count": total_svc,
        },
    }


# ---------------------------------------------------------------------------
# SECTION 3b — SERVICE-CATEGORY × BOROUGH HEAT MAP
# ---------------------------------------------------------------------------

@ttl_cached()
def get_service_borough_heatmap() -> dict:
    """Section 3b: 39-categories × 5-boroughs (+ Other) coverage heatmap.

    Returns shape:
        {
            "categories": [
                {
                    "name": "Food",
                    "total_locations": int,    # across all boroughs
                    "by_borough": {
                        "Manhattan": int,
                        "Brooklyn": int,
                        ...
                        "Other": int,
                    },
                },
                ...
            ],
            "boroughs": ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island", "Other"],
        }

    Categories are sorted by total_locations DESC so the highest-volume
    rows appear first. The frontend defaults to showing top
    HEATMAP_TOP_N_CATEGORIES (10) and offers an "expand" toggle for the
    rest.

    Each cell is the count of DISTINCT locations in that borough that
    offer at least one service tagged with that taxonomy. Distinct on
    location, not service — a single multi-service location offering
    food, clothing, and showers in Brooklyn appears once in each of
    Brooklyn-Food, Brooklyn-Clothing, Brooklyn-Showers (correct) but
    not three times in any single cell.

    Single SQL query with GROUP BY (taxonomy, borough). The
    `service_taxonomy ⋈ service_at_locations ⋈ physical_addresses`
    join executes in single-digit milliseconds at this scale —
    approximately 3,500 services × 2,400 locations is a junction-table
    sweep, not a Cartesian product.

    Wraps the borough-resolution CASE in a CTE so the outer GROUP BY
    references a materialized column. Grouping directly by an output
    alias of a CASE expression that references `pa.city` failed in
    PostgreSQL with "column pa.city must appear in the GROUP BY
    clause" — the GROUP-BY validator inspects the CASE's column
    references before alias resolution, so it can't tell that
    `GROUP BY borough` covers `pa.city`. The CTE form sidesteps this
    cleanly and matches the borough-summary query pattern elsewhere
    in this module.
    """
    sql = """
    WITH labeled AS (
        SELECT
            t.name AS category,
            %(borough_case)s AS borough,
            l.id AS location_id
        FROM taxonomies t
        JOIN service_taxonomy st ON st.taxonomy_id = t.id
        JOIN service_at_locations sal ON sal.service_id = st.service_id
        JOIN locations l ON l.id = sal.location_id
        LEFT JOIN physical_addresses pa ON pa.location_id = l.id
    )
    SELECT
        category,
        borough,
        COUNT(DISTINCT location_id) AS location_count
    FROM labeled
    GROUP BY category, borough
    """ % {"borough_case": _borough_case_sql("pa.city")}
    rows = _execute_sql(sql, {})

    # Aggregate by category. Each category gets a per-borough dict
    # initialized to zero so cells with no data render as explicit 0
    # rather than missing keys (frontend can't distinguish "no data
    # arrived" from "zero" if the key is absent).
    by_category: dict[str, dict[str, int]] = {}
    for r in rows:
        cat = r.get("category")
        if not cat:
            continue
        b = r.get("borough") or "Other"
        # Initialize every borough column on first sight of a category.
        if cat not in by_category:
            by_category[cat] = {label: 0 for label in (*NYC_BOROUGHS, "Other")}
        by_category[cat][b] = int(r.get("location_count") or 0)

    # Compute per-category totals + sort.
    categories: list[dict[str, Any]] = []
    for cat, by_borough in by_category.items():
        total = sum(by_borough.values())
        categories.append({
            "name": cat,
            "total_locations": total,
            "by_borough": by_borough,
        })
    categories.sort(key=lambda c: (-c["total_locations"], c["name"]))

    return {
        "categories": categories,
        "boroughs": [*NYC_BOROUGHS, "Other"],
    }


# ---------------------------------------------------------------------------
# SECTION 3c — COORDINATE VALIDATION TABLE
# ---------------------------------------------------------------------------

@ttl_cached()
def get_coordinate_issues() -> dict:
    """Section 3c: locations whose lat/lon doesn't match their declared city.

    Returns shape:
        {
            "issues": [
                {
                    "location_id": str,
                    "location_name": str,
                    "organization": str | None,
                    "stated_city": str | None,        # pa.city as stored
                    "stated_borough": str | None,     # NYC borough derived from city, if any
                    "computed_borough": str | None,   # from coordinates via NYC DCP polygons
                    "latitude": float,
                    "longitude": float,
                    "yourpeer_url": str,
                },
                ...
            ],
            "total_with_coords": int,
            "outside_nyc_count": int,
        }

    Detection rules — the same logic as `_annotate_geographic_borough`
    in query_executor.py, run across the catalog rather than per-card:

      * Pull every location with non-null position.
      * Compute borough from coordinates via boundaries.borough_from_coords.
      * Compare against the stated city (mapped to a canonical borough
        via stated_borough_from_city).
      * Flag mismatches AND points outside NYC entirely.

    Two separate counters because they're different fix paths:
      * outside_nyc_count → likely bad coordinate data (typo'd lat/lon
        or coords from an out-of-state service mistakenly imported)
      * mismatched borough → likely correct coords but wrong city in
        the address record (or vice versa — manual verification needed)

    The issues array contains both kinds; the frontend distinguishes
    via stated_borough vs. computed_borough in the row display.

    The boundary module does its own bounding-box pre-filter; running
    against ~2,400 points with full polygons takes single-digit
    milliseconds total. Server-side cache covers the page-load case.
    """
    sql = """
    SELECT
        l.id::text          AS location_id,
        l.name              AS location_name,
        l.slug              AS location_slug,
        o.name              AS organization,
        pa.city             AS stated_city,
        ST_Y(l.position::geometry) AS latitude,
        ST_X(l.position::geometry) AS longitude
    FROM locations l
    JOIN organizations o ON l.organization_id = o.id
    LEFT JOIN physical_addresses pa ON pa.location_id = l.id
    WHERE l.position IS NOT NULL
    """
    rows = _execute_sql(sql, {})

    # Lazy-import to mirror query_executor's pattern: boundaries does
    # GeoJSON parsing on first call, which we want to defer until the
    # endpoint is actually hit (not on import-time / module-load).
    # stated_borough_from_city is the public alias for the city→borough
    # resolver — see query_executor.py near the function definition.
    from app.rag.boundaries import borough_from_coords
    from app.rag.query_executor import stated_borough_from_city

    issues: list[dict[str, Any]] = []
    total_with_coords = 0
    outside_nyc_count = 0

    for r in rows:
        total_with_coords += 1
        lat = r.get("latitude")
        lon = r.get("longitude")
        if lat is None or lon is None:
            # WHERE l.position IS NOT NULL filters this out at SQL,
            # but defensively skip in Python too in case of future
            # nullable handling changes.
            continue

        # Coerce to float — psycopg2/SQLAlchemy may return Decimal or
        # similar; borough_from_coords expects float.
        lat_f = float(lat)
        lon_f = float(lon)

        computed = borough_from_coords(lat_f, lon_f)
        stated_city = r.get("stated_city")
        stated = stated_borough_from_city(stated_city) if stated_city else None

        if computed is None:
            # Outside NYC entirely — coords don't fall in any of the 5
            # borough polygons. Surface as a separate counter; included
            # in the issues array since it's still a data-quality
            # concern even if it doesn't have a "stated vs computed"
            # comparison to make.
            outside_nyc_count += 1
            slug = r.get("location_slug") or r.get("location_id") or ""
            issues.append({
                "location_id": str(r.get("location_id") or ""),
                "location_name": r.get("location_name") or "Unknown",
                "organization": r.get("organization"),
                "stated_city": stated_city,
                "stated_borough": stated,
                "computed_borough": None,   # signals "outside NYC"
                "latitude": lat_f,
                "longitude": lon_f,
                "yourpeer_url": f"https://yourpeer.nyc/locations/{slug}",
            })
            continue

        # Mismatch detection — same rule as _annotate_geographic_borough:
        # we need BOTH boroughs to be determinable for the comparison
        # to be meaningful. Stated == None means we couldn't map the
        # city to a known borough (e.g. the city is "" or out-of-NYC);
        # those aren't mismatches per se, just unknowns.
        if stated is not None and stated != computed:
            slug = r.get("location_slug") or r.get("location_id") or ""
            issues.append({
                "location_id": str(r.get("location_id") or ""),
                "location_name": r.get("location_name") or "Unknown",
                "organization": r.get("organization"),
                "stated_city": stated_city,
                "stated_borough": stated,
                "computed_borough": computed,
                "latitude": lat_f,
                "longitude": lon_f,
                "yourpeer_url": f"https://yourpeer.nyc/locations/{slug}",
            })

    # Sort: outside-NYC first (most concerning — likely typo'd coords),
    # then alphabetical by location name within each group.
    issues.sort(key=lambda x: (
        x["computed_borough"] is not None,   # False (outside NYC) sorts first
        x["location_name"].lower(),
    ))

    return {
        "issues": issues,
        "total_with_coords": total_with_coords,
        "outside_nyc_count": outside_nyc_count,
    }


# ---------------------------------------------------------------------------
# SECTION 4a — SERVICE-TYPE COVERAGE WITH DEMAND:SUPPLY
# ---------------------------------------------------------------------------

# Mapping: chatbot template_name (logged on query_execution events as
# `template_name`) → list of canonical taxonomy names that template's
# results would be filtered to. Keep in lockstep with the `taxonomy_aliases`
# arrays in query_templates.py — when a template's coverage list grows,
# this map needs to grow too. The lookup is best-effort: a template
# without an entry gets "unknown coverage" and contributes no demand
# signal to any taxonomy in section 4a.
#
# Why this mapping lives here, not in query_templates.py: query_templates
# is the source of truth for SQL building; this is a presentation-layer
# inversion (taxonomy → template name → demand). Tightly coupling them
# in one direction (templates listing their aliases) is fine, but
# requiring query_templates to also know about admin-stats reverse
# lookups would muddle its purpose. If the coupling becomes painful,
# lift to a shared module.
#
# ⚠️  KEY-SHAPE DEPENDENCY — READ BEFORE EDITING ⚠️
# This map's KEYS must match the keys emitted by
# `_compute_no_result_by_service(queries)` in audit_log.py. Today
# those keys ARE template names ("FoodQuery", "HousingEligibilityQuery"
# etc.) because of a behavior in that function: it tries
# `q["params"]["service_type"]` first but falls through to
# `q["template_name"]` since no query event currently logs a
# `service_type` param. The fall-through path is the de-facto
# contract this map relies on.
#
# That fall-through behavior is also documented as P3 in the metrics
# audit ("Crisis Detection Count says 'X sessions' but counts events"
# family). If anyone fixes _compute_no_result_by_service to truly key
# by service_type — by logging a service_type param on query events,
# or by removing the template_name fall-through — section 4a's
# demand attribution silently breaks: every template's demand falls
# into uncategorized_demand and the chart shows zero demand
# everywhere.
#
# Mitigations when that change lands:
#   1. Refactor demand attribution to log resolved taxonomy names
#      directly per query event (v1.5 plan).
#   2. Or: re-key this map by whatever the new service_type values
#      are. The taxonomy lists in each value should mostly stay the
#      same; only the keys would change.
#
# Cross-reference: audit_log.py :: _compute_no_result_by_service.
# That function has a matching comment pointing back here so the
# dependency is visible from both ends.
_TEMPLATE_TO_TAXONOMIES: dict[str, list[str]] = {
    "FoodQuery": [
        "Food", "Food Pantry", "Food Benefits", "Mobile Pantry",
        "Mobile Food Truck", "Mobile Market",
        "Food Delivery / Meals on Wheels", "Soup Kitchen",
        "Mobile Soup Kitchen", "Brown Bag", "Farmer's Markets",
    ],
    "HousingEligibilityQuery": [
        "Shelter", "Drop-in Center", "Transitional Housing",
        "Permanent Housing", "Emergency Shelter", "Family Shelter",
        "Single Adults Shelter", "Youth Shelter", "Domestic Violence Shelter",
    ],
    "ClothingQuery": [
        "Clothing", "Clothing Pantry", "Interview-Ready Clothing",
    ],
    "HealthcareQuery": [
        "Healthcare", "Medical Care", "Dental Care", "Vision Care",
        "HIV Testing", "STI Testing", "Pharmacy",
        "Reproductive Healthcare",
    ],
    "LegalQuery": [
        "Legal Services", "Legal Aid", "Immigration Legal Services",
        "Tenant Legal Services",
    ],
    "EmploymentQuery": [
        "Employment", "Job Training", "Job Placement", "Resume Help",
    ],
    "PersonalCareQuery": [
        "Personal Care", "Showers", "Laundry", "Hygiene Supplies",
        "Toiletries", "Hair Care",
    ],
    "MentalHealthQuery": [
        "Mental Health", "Mental Health Counseling", "Therapy",
        "Substance Use Treatment", "Substance Use Counseling",
        "Detox", "Crisis Counseling",
    ],
    # OtherServicesQuery and the org_name template don't map cleanly to
    # a fixed taxonomy set — they're catch-alls. Not in this map; their
    # demand signal won't surface in section 4a (correct: we don't
    # know which taxonomy they hit).
}


@ttl_cached()
def get_category_coverage() -> dict:
    """Section 4a: per-taxonomy coverage table with demand:supply ratio.

    Returns shape:
        {
            "categories": [
                {
                    "taxonomy_name": "Food Pantry",
                    "service_count": int,        # services tagged with this taxonomy
                    "location_count": int,       # distinct locations offering
                    "fresh_location_count": int, # of those, last_validated <90d
                    "verified_lt90d_pct": float | None,
                    "demand_query_count": int,   # queries via mapping (best-effort)
                    "no_result_count": int,
                    "no_result_rate": float | None,
                    # Demand:supply ratio = demand_query_count / location_count.
                    # High ratio = "users keep asking, supply is thin."
                    "demand_supply_ratio": float | None,
                },
                ...
            ],
        }

    Sort: demand_supply_ratio DESC, NULLS LAST. Categories with no
    demand signal sort to the bottom. The frontend can re-sort
    client-side; this is just the most operationally useful default.

    Demand mapping notes:
        - Per-template demand is read from the existing
          no_result_by_service metric.
        - Each template's demand is divided EVENLY across the
          taxonomies in its mapping. This isn't precise — a "Food"
          search probably hits "Food Pantry" more than "Farmer's
          Markets" in practice — but a uniform split is honest about
          the uncertainty rather than fabricating a weighting we
          don't have data for. Shown as a column with an info
          tooltip on the frontend.
        - Templates not in _TEMPLATE_TO_TAXONOMIES contribute no
          demand to any taxonomy. The "uncategorized demand" total
          is exposed separately in the response so admins can see
          how much demand is being lost to that bucket.
    """
    # Step 1: SQL aggregation for the supply side. One row per
    # taxonomy with service count, distinct location count, fresh
    # location count.
    sql = """
    SELECT
        t.name AS taxonomy_name,
        COUNT(DISTINCT s.id)        AS service_count,
        COUNT(DISTINCT l.id)        AS location_count,
        COUNT(DISTINCT l.id) FILTER (
            WHERE l.last_validated_at >= CURRENT_DATE - :fresh_days * INTERVAL '1 day'
        ) AS fresh_location_count
    FROM taxonomies t
    JOIN service_taxonomy st ON st.taxonomy_id = t.id
    JOIN services s          ON s.id = st.service_id
    JOIN service_at_locations sal ON sal.service_id = s.id
    JOIN locations l         ON l.id = sal.location_id
    GROUP BY t.name
    """
    rows = _execute_sql(sql, {"fresh_days": FRESHNESS_THRESHOLD_DAYS})

    # Step 2: gather demand from no_result_by_service.
    #
    # We could call audit_log.get_stats() here and read its
    # "no_result_by_service" key — but get_stats walks the entire
    # audit log and computes ~30 metrics (LLM cost rollups,
    # frustration tiers, session durations, …) only one of which
    # we need. Calling the underlying _compute helper directly with
    # a one-shot fetch of query_execution events skips all that
    # other work. Per-section measurement showed an order-of-
    # magnitude speedup on this endpoint at production audit-log
    # size.
    #
    # Imported here rather than at module top to avoid a circular:
    # audit_log doesn't depend on locations_admin and we want to
    # keep it that way. _get_events_capped (defined above) handles
    # the underlying get_recent_events call.
    from app.services.audit_log import _compute_no_result_by_service
    query_events = _get_events_capped("query_execution")
    no_result = _compute_no_result_by_service(query_events)

    # Step 3: invert the template→taxonomies map into per-taxonomy
    # demand contributions. Each template's total queries get
    # divided evenly among the taxonomies it covers. Templates
    # without mapping accumulate to "uncategorized."
    demand_by_taxonomy: dict[str, dict[str, float]] = {}
    uncategorized_demand_queries = 0
    uncategorized_no_result = 0
    for template_name, info in no_result.items():
        total_q = int(info.get("total_queries") or 0)
        no_r = int(info.get("no_result_count") or 0)
        taxonomies = _TEMPLATE_TO_TAXONOMIES.get(template_name)
        if not taxonomies:
            uncategorized_demand_queries += total_q
            uncategorized_no_result += no_r
            continue
        # Even split — see docstring for the rationale.
        per_tax_q = total_q / len(taxonomies)
        per_tax_nr = no_r / len(taxonomies)
        for tax_name in taxonomies:
            d = demand_by_taxonomy.setdefault(
                tax_name, {"queries": 0.0, "no_result": 0.0}
            )
            d["queries"] += per_tax_q
            d["no_result"] += per_tax_nr

    # Step 4: assemble the response.
    categories: list[dict[str, Any]] = []
    for r in rows:
        tax_name = r.get("taxonomy_name") or ""
        loc_count = int(r.get("location_count") or 0)
        fresh_count = int(r.get("fresh_location_count") or 0)
        demand = demand_by_taxonomy.get(tax_name, {"queries": 0.0, "no_result": 0.0})
        demand_q = demand["queries"]
        demand_nr = demand["no_result"]
        verified_pct = round(100.0 * fresh_count / loc_count, 1) if loc_count else None
        no_result_rate = round(demand_nr / demand_q, 2) if demand_q > 0 else None
        # Demand:supply only meaningful when both sides nonzero. When
        # demand is 0 (no traffic to this taxonomy via any template),
        # ratio is None — correct distinction from "ratio is 0" which
        # would mean "queries existed but supply was infinite."
        ratio = round(demand_q / loc_count, 2) if (demand_q > 0 and loc_count > 0) else None

        categories.append({
            "taxonomy_name": tax_name,
            "service_count": int(r.get("service_count") or 0),
            "location_count": loc_count,
            "fresh_location_count": fresh_count,
            "verified_lt90d_pct": verified_pct,
            "demand_query_count": round(demand_q, 1),
            "no_result_count": round(demand_nr, 1),
            "no_result_rate": no_result_rate,
            "demand_supply_ratio": ratio,
        })

    # Sort: demand:supply DESC, NULLS LAST (taxonomies with demand
    # signal float to the top — "users keep asking and supply is
    # thin" is the most operationally useful prompt). Within the
    # null group, sort by location_count DESC so the largest gaps
    # show first.
    categories.sort(
        key=lambda c: (
            c["demand_supply_ratio"] is None,    # False (has ratio) sorts first
            -(c["demand_supply_ratio"] or 0),
            -c["location_count"],
        )
    )

    return {
        "categories": categories,
        "uncategorized_demand": {
            "query_count": uncategorized_demand_queries,
            "no_result_count": uncategorized_no_result,
        },
    }


# ---------------------------------------------------------------------------
# SECTION 4b — STALE CATEGORIES (no recent verification across all locations)
# ---------------------------------------------------------------------------

# How stale a category needs to be to surface here. A category counts
# as "stale" if NO location offering it has been verified within this
# many days — meaning the entire category is at risk of going stale
# system-wide. 180d is intentionally longer than the standard
# FRESHNESS_THRESHOLD_DAYS (90) — section 4b is about
# "category-wide neglect," not just "above the routine freshness bar."
STALE_CATEGORY_LOOKBACK_DAYS = 180

# How many stale categories to surface in the UI. If more than this
# qualify, the section header surfaces the count and the table
# truncates.
STALE_CATEGORIES_TOP_N = 10


@ttl_cached()
def get_stale_categories() -> dict:
    """Section 4b: top-N taxonomies where every offering location is stale.

    Returns shape:
        {
            "categories": [
                {
                    "taxonomy_name": "Free Wi-Fi Access",
                    "location_count": int,     # how many locations offer it
                    "max_verified_at": str | None,  # most-recent across them
                    "days_since_max_verified": int | None,
                },
                ...
            ],
            "total_stale": int,            # total count of stale categories
            "lookback_days": int,
        }

    A category counts as "stale" if its MOST-RECENTLY-VERIFIED
    offering location is older than STALE_CATEGORY_LOOKBACK_DAYS
    (180 days). This is stronger than "average is stale" — it means
    NOT EVEN ONE location offering this taxonomy has been verified
    recently. Categories with no offering locations at all (which
    SQL JOIN will exclude) don't appear here; they'd be a different
    kind of bug surfaced by section 6.

    Sort: oldest max-verification first (most-stale at the top),
    so the categories most-needing-attention surface first.
    """
    sql = """
    SELECT
        t.name AS taxonomy_name,
        COUNT(DISTINCT l.id) AS location_count,
        MAX(l.last_validated_at) AS max_verified_at
    FROM taxonomies t
    JOIN service_taxonomy st ON st.taxonomy_id = t.id
    JOIN service_at_locations sal ON sal.service_id = st.service_id
    JOIN locations l ON l.id = sal.location_id
    GROUP BY t.name
    HAVING (
        MAX(l.last_validated_at) IS NULL
        OR MAX(l.last_validated_at) < CURRENT_DATE - :stale_days * INTERVAL '1 day'
    )
    ORDER BY MAX(l.last_validated_at) ASC NULLS FIRST
    """
    rows = _execute_sql(sql, {"stale_days": STALE_CATEGORY_LOOKBACK_DAYS})

    today = datetime.now(timezone.utc).date()
    categories: list[dict[str, Any]] = []
    for r in rows[:STALE_CATEGORIES_TOP_N]:
        max_v = r.get("max_verified_at")
        max_v_iso: Optional[str] = None
        days_since: Optional[int] = None
        if max_v is not None:
            if hasattr(max_v, "isoformat"):
                max_v_iso = max_v.isoformat()
                # Coerce date / datetime to date for the day count
                if hasattr(max_v, "date"):
                    days_since = (today - max_v.date()).days
                else:
                    days_since = (today - max_v).days
            else:
                max_v_iso = str(max_v)
        categories.append({
            "taxonomy_name": r.get("taxonomy_name") or "",
            "location_count": int(r.get("location_count") or 0),
            "max_verified_at": max_v_iso,
            "days_since_max_verified": days_since,
        })

    return {
        "categories": categories,
        "total_stale": len(rows),
        "lookback_days": STALE_CATEGORY_LOOKBACK_DAYS,
    }


# ---------------------------------------------------------------------------
# SECTION 5a + 5b — LOCATION FEEDBACK AGGREGATES
# ---------------------------------------------------------------------------

# The four criteria captured by `log_location_feedback`. Listed here
# (rather than computed dynamically from event data) so the response
# shape is stable across runs — sections 5a and 5b always render the
# same four columns whether or not the underlying events happen to
# contain them. New criteria require updating this list AND the
# log_location_feedback signature.
_FEEDBACK_CRITERIA = ("safety", "friendliness", "cleanliness", "queer_friendly")

# Number of locations to surface in the "most-flagged" ranking.
# 10 keeps the table scannable and matches the team's review cadence
# (a few minutes of triage is enough to review 10 rows). The
# `total_eligible` aggregate in the response lets the frontend show
# "showing top 10 of N qualifying" framing when there are more.
MOST_FLAGGED_TOP_N = 10


@ttl_cached()
def get_location_feedback_aggregates() -> dict:
    """Sections 5a + 5b: location-feedback aggregation in one response.

    Returns shape:
        {
            "most_flagged": [
                {
                    "location_id": str,
                    "location_name": str | None,    # snapshot from feedback events
                    "total_events": int,
                    "criterion_counts": {           # per-criterion {pos, neg, total_rated}
                        "safety": {"positive": int, "negative": int, "rated": int},
                        ...
                    },
                    "negative_ratings_count": int,  # sum of False ratings across criteria
                    "total_ratings_count": int,     # sum of all ratings (True+False) across criteria
                    "negative_ratio_smoothed": float,  # (neg+1)/(total+2)
                    "raw_negative_ratio": float,    # neg/total — for display alongside smoothed
                    "last_event_at": str,           # ISO8601
                    "comments_count": int,          # events with non-empty comment
                },
                ...
            ],
            "total_eligible": int,                # total locations with ≥ FEEDBACK_MIN_SAMPLE events
            "min_sample": int,                    # FEEDBACK_MIN_SAMPLE
            "criterion_summary": {                # section 5b — per-criterion population %
                "safety": {
                    "events_rated": int,          # how many events had this criterion rated
                    "positive": int,              # True ratings count
                    "negative": int,              # False ratings count
                    "negative_pct": float | None, # negative / events_rated, or null when 0 events
                },
                ...
            },
            "total_events_overall": int,
        }

    Sections 5a (most_flagged) and 5b (criterion_summary) are returned
    in one response because they share the same source events and the
    Python aggregation pass naturally produces both. Two separate
    endpoints would mean two scans of the same event log.

    Smoothing for negative_ratio_smoothed uses Laplace add-one:
    (neg + 1) / (total_rated + 2). This pulls low-sample locations
    slightly toward the population mean rather than letting them
    pin at 100%/0%. Standard pattern, well-understood, no statistical
    surprises. The raw_negative_ratio is also surfaced so admins can
    see the unsmoothed signal alongside.

    Eligibility cutoff: a location must have at least FEEDBACK_MIN_SAMPLE
    events to qualify for the most_flagged ranking. With FEEDBACK_MIN_SAMPLE=2
    (the answer to question 3 in the spec), this avoids one-off noise
    while still surfacing genuine signals at low volume.
    """
    events = _get_events_capped("location_feedback")

    # Per-location aggregation
    by_location: dict[str, dict[str, Any]] = {}
    # Per-criterion population aggregation (section 5b)
    crit_summary: dict[str, dict[str, int]] = {
        c: {"events_rated": 0, "positive": 0, "negative": 0}
        for c in _FEEDBACK_CRITERIA
    }
    total_events_overall = 0

    for ev in events:
        loc_id = ev.get("location_id")
        if not loc_id:
            continue
        loc_id = str(loc_id)
        total_events_overall += 1

        bucket = by_location.setdefault(
            loc_id,
            {
                "location_name": None,
                "total_events": 0,
                "criterion_counts": {
                    c: {"positive": 0, "negative": 0, "rated": 0}
                    for c in _FEEDBACK_CRITERIA
                },
                "negative_ratings_count": 0,
                "total_ratings_count": 0,
                "last_event_at": "",
                "comments_count": 0,
            },
        )
        bucket["total_events"] += 1

        # Capture the most-recent location_name. Names can change over
        # time; snapshotting the latest is a reasonable display
        # choice. Empty / None names are skipped — keeps any prior
        # non-empty name as a fallback.
        loc_name = ev.get("location_name")
        if loc_name:
            bucket["location_name"] = loc_name

        # Update the bucket's last_event_at — the timestamp of the
        # most-recent event seen for this location. The bucket value
        # stays an ISO string (it's exposed verbatim in the response),
        # but the comparison goes through _parse_iso_ts so we're
        # comparing datetimes, not strings. Lexicographic string
        # compare happens to work today because every event's
        # timestamp comes from `datetime.now(timezone.utc).isoformat()`
        # with consistent precision — but that's coincidental, not
        # guaranteed. The moment any timestamp source uses a
        # different fractional-second precision or tz suffix, string
        # compare gives the wrong answer.
        ts = ev.get("timestamp") or ""
        ts_dt = _parse_iso_ts(ts)
        cur_dt = _parse_iso_ts(bucket["last_event_at"])
        # Replace when:
        #   - we have a parseable new ts AND
        #   - either there's no current value, or the new ts is later
        # An unparseable ts is treated as "skip this update," leaving
        # whatever's already in the bucket. Defensive: don't let a
        # garbage ts wipe out a good one.
        if ts_dt is not None and (cur_dt is None or ts_dt > cur_dt):
            bucket["last_event_at"] = ts

        if (ev.get("comment") or "").strip():
            bucket["comments_count"] += 1

        ratings = ev.get("ratings") or {}
        for crit in _FEEDBACK_CRITERIA:
            if crit not in ratings:
                continue
            value = ratings[crit]
            if value is None:
                # Defensive — should already be filtered out at log time
                continue
            crit_summary[crit]["events_rated"] += 1
            bucket["criterion_counts"][crit]["rated"] += 1
            bucket["total_ratings_count"] += 1
            if value is True:
                crit_summary[crit]["positive"] += 1
                bucket["criterion_counts"][crit]["positive"] += 1
            elif value is False:
                crit_summary[crit]["negative"] += 1
                bucket["criterion_counts"][crit]["negative"] += 1
                bucket["negative_ratings_count"] += 1

    # Compute the most-flagged ranking. Apply the FEEDBACK_MIN_SAMPLE
    # cutoff (exclude one-off noise) and smooth low-sample ratios via
    # Laplace add-one smoothing. Sort by smoothed ratio DESC, with
    # raw negative count as a stable tie-break (a 4/4 ranks above
    # 2/2 even if the smoothed ratio happens to be the same — and
    # since Laplace gives 4/4 = 5/6 = 0.833 vs 2/2 = 3/4 = 0.75,
    # they actually differ; the tie-break is for the rare edge case).
    eligible_count = 0
    rows: list[dict[str, Any]] = []
    for loc_id, bucket in by_location.items():
        if bucket["total_events"] < FEEDBACK_MIN_SAMPLE:
            continue
        eligible_count += 1
        total_rated = bucket["total_ratings_count"]
        neg = bucket["negative_ratings_count"]
        # Laplace add-one smoothing: (neg + 1) / (total_rated + 2).
        # Denominator is always >= 2 by construction (the +2 ensures
        # it; total_rated itself is a count that's always >= 0). No
        # division-by-zero guard needed, and no defensive fallback —
        # any non-trivial fallback value would be misleading because
        # smoothing's purpose is to give a meaningful answer even at
        # 0 ratings (1/2 = 0.5 is the neutral prior, which the formula
        # produces naturally when neg=total=0).
        smoothed = (neg + 1) / (total_rated + 2)
        raw = neg / total_rated if total_rated > 0 else 0.0
        rows.append({
            "location_id": loc_id,
            "location_name": bucket["location_name"],
            "total_events": bucket["total_events"],
            "criterion_counts": bucket["criterion_counts"],
            "negative_ratings_count": neg,
            "total_ratings_count": total_rated,
            "negative_ratio_smoothed": round(smoothed, 4),
            "raw_negative_ratio": round(raw, 4),
            "last_event_at": bucket["last_event_at"],
            "comments_count": bucket["comments_count"],
        })

    rows.sort(key=lambda r: (-r["negative_ratio_smoothed"], -r["negative_ratings_count"]))
    most_flagged = rows[:MOST_FLAGGED_TOP_N]

    # Build the per-criterion summary (section 5b)
    criterion_summary: dict[str, dict[str, Any]] = {}
    for crit in _FEEDBACK_CRITERIA:
        c = crit_summary[crit]
        rated = c["events_rated"]
        criterion_summary[crit] = {
            "events_rated": rated,
            "positive": c["positive"],
            "negative": c["negative"],
            "negative_pct": (
                round(100.0 * c["negative"] / rated, 1) if rated > 0 else None
            ),
        }

    return {
        "most_flagged": most_flagged,
        "total_eligible": eligible_count,
        "min_sample": FEEDBACK_MIN_SAMPLE,
        "criterion_summary": criterion_summary,
        "total_events_overall": total_events_overall,
    }


# ---------------------------------------------------------------------------
# SECTION 5c — RECENT COMMENTS STREAM
# ---------------------------------------------------------------------------

# Default cap on how many comments to surface. Most-recent first.
# 50 is a balance: enough to give a useful qualitative read of
# "what are users actually saying," not so many that the page
# becomes a comment-firehose to scroll through.
RECENT_COMMENTS_DEFAULT_LIMIT = 50

# Hard ceiling on the limit query param. Above this and we'd
# need to think about pagination; below this the simple "fetch and
# render" pattern works.
RECENT_COMMENTS_MAX_LIMIT = 200


@ttl_cached()
def get_recent_feedback_comments(limit: int = RECENT_COMMENTS_DEFAULT_LIMIT) -> dict:
    """Section 5c: reverse-chronological list of recent location_feedback
    events that include a non-empty comment.

    Returns shape:
        {
            "comments": [
                {
                    "session_id": str,
                    "location_id": str,
                    "location_name": str | None,
                    "timestamp": str,         # ISO8601
                    "comment": str,           # the user's text
                    "negative_criteria": list[str],   # criteria flagged False (sorted by _FEEDBACK_CRITERIA order)
                    "positive_criteria": list[str],   # criteria flagged True
                },
                ...
            ],
            "total_with_comments": int,       # count across the full event log
            "limit": int,                     # echo of effective limit
        }

    The negative/positive criteria split is pre-computed on the
    backend so the frontend doesn't have to walk the ratings dict
    per row. Sorted in canonical _FEEDBACK_CRITERIA order so the
    UI renders consistently across rows.

    Empty / whitespace-only comments are filtered out — those don't
    have qualitative content to surface. The total_with_comments
    count is computed from the FILTERED set, not the raw event log,
    so admins see "5 of 5 with comments" rather than "5 of 200
    events" (which would conflate the two questions).
    """
    # Clamp limit defensively. The route also Query()-validates this,
    # but defending here too means the function is safe to call from
    # other code paths.
    limit = max(1, min(RECENT_COMMENTS_MAX_LIMIT, int(limit)))

    events = _get_events_capped("location_feedback")

    # Filter to events with non-empty comments. Whitespace-only
    # comments are treated as empty.
    with_comments = [
        ev for ev in events
        if (ev.get("comment") or "").strip()
    ]
    total_with_comments = len(with_comments)

    # Most-recent first. get_recent_events returns oldest-first within
    # the limit window; reverse here so the response leads with the
    # newest activity.
    with_comments.reverse()

    out: list[dict[str, Any]] = []
    for ev in with_comments[:limit]:
        ratings = ev.get("ratings") or {}
        # Walk in canonical order so list ordering is deterministic
        # — frontend can rely on the array order matching the badge
        # order it expects.
        negative_criteria = [
            crit for crit in _FEEDBACK_CRITERIA
            if ratings.get(crit) is False
        ]
        positive_criteria = [
            crit for crit in _FEEDBACK_CRITERIA
            if ratings.get(crit) is True
        ]
        out.append({
            "session_id": str(ev.get("session_id") or ""),
            "location_id": str(ev.get("location_id") or ""),
            "location_name": ev.get("location_name"),
            "timestamp": ev.get("timestamp") or "",
            "comment": (ev.get("comment") or "").strip(),
            "negative_criteria": negative_criteria,
            "positive_criteria": positive_criteria,
        })

    return {
        "comments": out,
        "total_with_comments": total_with_comments,
        "limit": limit,
    }


# ---------------------------------------------------------------------------
# SECTION 6 — DATA INTEGRITY CALLOUTS
# ---------------------------------------------------------------------------
#
# Six query-driven callouts surface when COUNT > 0. Each callout has
# a severity ("warning" / "info"), a title, a count, and a short
# action hint telling admins what to do about it.
#
# The callouts are computed in one endpoint rather than six because:
#   1. Most of them are < 10ms each at this catalog size — six round
#      trips to the DB still total < 100ms.
#   2. Rendering them as a single panel is easier when they arrive as
#      a single response.
#   3. If we later need to optimize, parallel-running them inside the
#      function is one local change — vs. coordinating six separate
#      endpoint calls on the frontend.
# ---------------------------------------------------------------------------

# Phone format the chatbot's formatPhone TS helper produces — matches
# the regex used in `frontend-next/src/lib/chat/format-phone.ts`. Values
# that DON'T match this and aren't empty are flagged as malformed.
# Tolerant of optional extension suffix (' x123', 'ext 4567').
_VALID_PHONE_RE = r"^\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}( ?(x|ext\.?) ?\d+)?$"

# Heuristic for "looks like phone-shaped data" — anything with at least
# 7 digits in it. Used to filter out genuinely-empty / placeholder
# values before applying the strict regex check; a "0" or "n/a" in
# the phone column is a different bug class than a malformed real
# number, and we don't want section 6 to flag every empty-ish row.
_PHONELIKE_RE = r"[0-9].*[0-9].*[0-9].*[0-9].*[0-9].*[0-9].*[0-9]"


@ttl_cached()
def get_data_integrity_callouts() -> dict:
    """Section 6: data-integrity callouts.

    Returns shape:
        {
            "callouts": [
                {
                    "id": str,           # stable key for the frontend
                    "severity": "warning" | "info",
                    "title": str,
                    "count": int,
                    "action_hint": str,
                    "ref": str | None,   # "see section 3c" etc, or null
                },
                ...
            ],
            "total_callouts": int,       # only counts firing ones
            "all_clear": bool,           # True iff no callout fired
        }

    The `all_clear: True` case lets the frontend render a positive
    "✓ no integrity issues found" state instead of an empty list —
    which is the preferred outcome and worth surfacing positively.

    Each callout is a separate SQL query. They're listed in display
    order; the frontend renders them in that order so the visual
    grouping is consistent across runs.
    """
    callouts: list[dict[str, Any]] = []

    # ---- Callout 1: orphaned locations (no services attached) ----
    # These show up in the catalog but produce 0 results in any
    # service-type search. Almost always a stale import or a
    # location whose services have all been hidden.
    orphan_loc_sql = """
    SELECT COUNT(DISTINCT l.id) AS n
    FROM locations l
    LEFT JOIN service_at_locations sal ON sal.location_id = l.id
    WHERE sal.id IS NULL
    """
    n = _scalar_count(orphan_loc_sql)
    if n > 0:
        callouts.append({
            "id": "orphaned_locations",
            "severity": "warning",
            "title": "Orphaned locations",
            "count": n,
            "action_hint": (
                "These locations have no services attached and won't appear "
                "in any chat results. Likely stale imports or hidden-service "
                "remnants — review for deletion or relink."
            ),
            "ref": None,
        })

    # ---- Callout 2: services with no location attached ----
    # Mirror of #1: services that exist but aren't reachable through
    # any location-anchored query. The chat does proximity-search
    # against l.position; services here are invisible to it.
    orphan_svc_sql = """
    SELECT COUNT(DISTINCT s.id) AS n
    FROM services s
    LEFT JOIN service_at_locations sal ON sal.service_id = s.id
    WHERE sal.id IS NULL
    """
    n = _scalar_count(orphan_svc_sql)
    if n > 0:
        callouts.append({
            "id": "orphaned_services",
            "severity": "warning",
            "title": "Services with no location",
            "count": n,
            "action_hint": (
                "These services exist but aren't tied to any location, so "
                "they can't be returned by location-aware searches. Either "
                "attach to a location or hide."
            ),
            "ref": None,
        })

    # ---- Callout 3: malformed phone formats ----
    # PostgreSQL POSIX regex with ~* (case-insensitive). The strict
    # regex is the same shape formatPhone produces; phones that don't
    # match AND look phone-shaped (≥7 digits) are flagged. Empty /
    # 'n/a' / 'tbd' values are out of scope for this callout.
    #
    # Bind params use SQLAlchemy's named-parameter syntax `:name`, not
    # psycopg2's pyformat `%(name)s` — text() only recognizes `:name`,
    # and passing `%(name)s` through results in the literal `%` reaching
    # Postgres which errors with "syntax error at or near \"%\"".
    bad_phones_sql = """
    SELECT COUNT(*) AS n
    FROM phones p
    WHERE p.number IS NOT NULL
      AND p.number ~ :phonelike
      AND p.number !~* :strict
    """
    n = _scalar_count(bad_phones_sql, {
        "phonelike": _PHONELIKE_RE,
        "strict": _VALID_PHONE_RE,
    })
    if n > 0:
        callouts.append({
            "id": "malformed_phones",
            "severity": "warning",
            "title": "Malformed phone formats",
            "count": n,
            "action_hint": (
                "These phone numbers don't match the standard "
                "(NNN) NNN-NNNN pattern. The chat reformats them at "
                "render time, but bad source data risks ambiguity in "
                "the admin views."
            ),
            "ref": None,
        })

    # ---- Callout 4: entity-encoded HTML in descriptions ----
    # Known issue: some import paths double-encoded their HTML so
    # the literal text "&lt;br&gt;" ends up in description fields.
    # The frontend strips these via safe_html_entity_decode at
    # render time, but the underlying data quality is still wrong.
    encoded_html_sql = """
    SELECT COUNT(*) AS n
    FROM services s
    WHERE s.description ILIKE '%&lt;br%' OR s.description ILIKE '%&amp;%'
    """
    n = _scalar_count(encoded_html_sql)
    if n > 0:
        callouts.append({
            "id": "entity_encoded_html",
            "severity": "info",
            "title": "Entity-encoded HTML in descriptions",
            "count": n,
            "action_hint": (
                "Service descriptions containing literal '&lt;br&gt;' or "
                "'&amp;' tokens — likely double-encoded during import. "
                "Frontend handles these at render time, but worth fixing "
                "at source for cleaner downstream consumption."
            ),
            "ref": None,
        })

    # ---- Callout 5: cross-reference to section 3c ----
    # We don't duplicate section 3c's per-row coordinate validation
    # here; instead we check whether section 3c has anything to show
    # and link to it. Avoids data-shape divergence between sections.
    coord_issues = get_coordinate_issues()
    n_outside_nyc = coord_issues["outside_nyc_count"]
    n_total_issues = len(coord_issues["issues"])
    if n_total_issues > 0:
        callouts.append({
            "id": "coordinate_issues_ref",
            "severity": "warning" if n_outside_nyc > 0 else "info",
            "title": "Coordinate validation issues",
            "count": n_total_issues,
            "action_hint": (
                f"{n_outside_nyc} location"
                f"{'s' if n_outside_nyc != 1 else ''} with coordinates "
                "outside NYC, plus borough mismatches. See the "
                "Coordinate validation section above for details."
            ) if n_outside_nyc > 0 else (
                "Locations whose coordinates disagree with their stated "
                "city. See the Coordinate validation section above."
            ),
            "ref": "section_3c_coordinate_validation",
        })

    return {
        "callouts": callouts,
        "total_callouts": len(callouts),
        "all_clear": len(callouts) == 0,
    }


def _scalar_count(sql: str, params: Optional[dict] = None) -> int:
    """Execute a SQL query that returns a single column 'n' on a
    single row, return the int. Used by the integrity callouts —
    each query is a COUNT(*) with no ranking / grouping.

    Defensive against an empty result set (returns 0) and against
    None counts (also 0) so callout queries never cause the section
    to crash on edge cases."""
    rows = _execute_sql(sql, params or {})
    if not rows:
        return 0
    return int(rows[0].get("n") or 0)


# ---------------------------------------------------------------------------
# SECTION 7 — TIME SERIES (last 26 weeks)
# ---------------------------------------------------------------------------
#
# Three weekly series: locations added, locations verified, location
# feedback events. All bucketed into ISO weeks for the last
# TIMESERIES_WEEKS (=26 by default). The series share an x-axis so
# the frontend can render them in three small charts that read as
# a temporal scan of "what's happening on the catalog."
#
# Why ISO weeks (Mon-Sun) and not calendar weeks: Mon-Sun is
# operationally sensible (ops cycles run Mon-Fri, "this week" is
# unambiguous). PostgreSQL's date_trunc('week', x) defaults to ISO
# weeks. No tz conversion issues since everything is server-local.


@ttl_cached()
def get_locations_timeseries() -> dict:
    """Section 7: weekly locations-added / verified / feedback time series.

    Returns shape:
        {
            "weeks": [
                {
                    "week_start": "2025-11-10",     # ISO date, Monday
                    "locations_added": int,
                    "locations_verified": int,
                    "feedback_events": int,
                },
                ...
            ],
            "total_weeks": int,                   # = TIMESERIES_WEEKS
        }

    Always returns exactly TIMESERIES_WEEKS rows in chronological
    order, oldest-first. Empty weeks render as zeros (not missing) so
    the frontend can draw a continuous line / sparkline without
    interpolation logic.

    Three separate SQL queries (one per series) bucketed by
    date_trunc('week', x). Could be one big UNION — the small saving
    isn't worth the complexity given the per-query times are < 5ms.
    """
    # Compute the canonical week list in DISPLAY_TIMEZONE (ET).
    # Admins read this chart against an NYC wall clock; week
    # boundaries that drift by 4-5 hours into UTC produce confusing
    # "Sunday evening's events got counted in next week" effects.
    #
    # The bucketing happens in Python rather than via SQL
    # `AT TIME ZONE` because the DB's column type (timestamptz vs
    # naive timestamp) affects `AT TIME ZONE` semantics, and we don't
    # want correctness to depend on a schema detail. Python ownership
    # also makes the timezone logic directly testable.
    today_et = datetime.now(DISPLAY_TIMEZONE).date()
    days_since_monday = today_et.weekday()    # Mon=0, Sun=6
    this_monday = today_et - timedelta(days=days_since_monday)
    earliest_monday = this_monday - timedelta(weeks=TIMESERIES_WEEKS - 1)

    # Pre-build the canonical week list so empty weeks aren't missing.
    all_weeks = [
        earliest_monday + timedelta(weeks=i)
        for i in range(TIMESERIES_WEEKS)
    ]
    week_keys = [w.isoformat() for w in all_weeks]
    series: dict[str, dict[str, int]] = {
        wk: {"locations_added": 0, "locations_verified": 0, "feedback_events": 0}
        for wk in week_keys
    }

    # WHERE cutoff: the UTC instant corresponding to midnight ET on
    # earliest_monday. A timestamp before this instant is definitely
    # in an earlier week than any we'll display. (We err slightly
    # earlier — by 1 day — to make sure no rows close to the boundary
    # are missed; redundant rows just won't find a bucket and get
    # discarded by the `if wk_iso in series` check below.)
    earliest_utc = datetime.combine(
        earliest_monday - timedelta(days=1),
        datetime.min.time(),
        tzinfo=DISPLAY_TIMEZONE,
    ).astimezone(timezone.utc)
    earliest_utc_iso = earliest_utc.isoformat()

    def _bucket_key_for(dt_utc: datetime) -> Optional[str]:
        """Map a tz-aware UTC datetime to its ET-Monday-of-week ISO
        date key. Returns None when the resulting date is outside
        the canonical week list.
        """
        if dt_utc is None:
            return None
        dt_et = dt_utc.astimezone(DISPLAY_TIMEZONE).date()
        days_back = dt_et.weekday()
        wk = dt_et - timedelta(days=days_back)
        wk_iso = wk.isoformat()
        return wk_iso if wk_iso in series else None

    # ---- Series 1: locations added ----
    # Ungrouped (no SQL date_trunc) — Python does the ET-aware bucketing.
    # Cost is modest: at pilot scale, locations are added at a slow
    # rate, so 26 weeks of adds is well under 100 rows.
    added_sql = """
    SELECT l.created_at AS ts
    FROM locations l
    WHERE l.created_at >= :since
    """
    for r in _execute_sql(added_sql, {"since": earliest_utc_iso}):
        ts = r.get("ts")
        if ts is None:
            continue
        # SQLAlchemy returns timestamptz columns as tz-aware datetime;
        # naive timestamp columns come back naive. Normalize.
        if isinstance(ts, datetime) and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        wk_iso = _bucket_key_for(ts)
        if wk_iso is not None:
            series[wk_iso]["locations_added"] += 1

    # ---- Series 2: locations verified ----
    verified_sql = """
    SELECT l.last_validated_at AS ts
    FROM locations l
    WHERE l.last_validated_at >= :since
    """
    for r in _execute_sql(verified_sql, {"since": earliest_utc_iso}):
        ts = r.get("ts")
        if ts is None:
            continue
        if isinstance(ts, datetime) and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        wk_iso = _bucket_key_for(ts)
        if wk_iso is not None:
            series[wk_iso]["locations_verified"] += 1

    # ---- Series 3: feedback events ----
    # Pulled from the audit log, not the DB — feedback lives in the
    # event store. Walk events, bucket into ET weeks via the same
    # helper as the SQL series so all three sources agree on week
    # boundaries.
    feedback_events = _get_events_capped("location_feedback")
    for ev in feedback_events:
        dt = _parse_iso_ts(ev.get("timestamp"))
        if dt is None:
            continue
        wk_iso = _bucket_key_for(dt)
        if wk_iso is not None:
            series[wk_iso]["feedback_events"] += 1

    # Emit in chronological order, oldest first.
    weeks_out = [
        {"week_start": wk, **series[wk]}
        for wk in week_keys
    ]
    return {
        "weeks": weeks_out,
        "total_weeks": TIMESERIES_WEEKS,
    }
