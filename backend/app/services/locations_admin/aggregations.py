"""
Locations admin — aggregation queries.

Day-1 scope: top stat strip (section 1) and "needs review" sortable
table (section 2b) of the Locations admin page spec.

Each function returns a structured dict matching its frontend
TypeScript counterpart (see `frontend-next/src/lib/admin/
locations-types.ts`). Aggregations run against the Streetlives
read-only Postgres via the existing `_execute_sql` helper —
no new schema, no migrations, no new connection.

Constants are exported at module top so future tuning is one-stop:
fresh threshold, recent-flags lookback, top-N-categories, feedback
sample minimum. Each documented inline.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.rag.query_executor import _execute_sql
from app.services.audit_log import get_recent_events

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


# ---------------------------------------------------------------------------
# SECTION 1 — TOP STAT STRIP
# ---------------------------------------------------------------------------

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
        WHERE last_validated_at >= CURRENT_DATE - INTERVAL '%(fresh)s days'
      ) AS fresh_count,
      (SELECT COUNT(*) FROM locations WHERE last_validated_at IS NULL
      ) AS never_verified_count,
      (SELECT COUNT(*) FROM locations
        WHERE last_validated_at < CURRENT_DATE - INTERVAL '%(fresh)s days'
      ) AS stale_count,
      (SELECT COUNT(*) FROM locations
        WHERE last_validated_at >= CURRENT_DATE - INTERVAL '7 days'
      ) AS fresh_last_7d,
      (SELECT COUNT(*) FROM locations
        WHERE last_validated_at >= CURRENT_DATE - INTERVAL '14 days'
          AND last_validated_at <  CURRENT_DATE - INTERVAL '7 days'
      ) AS fresh_prev_7d
    """ % {"fresh": FRESHNESS_THRESHOLD_DAYS}

    rows = _execute_sql(counts_sql, {})
    if not rows:
        # Defensive — _execute_sql returns [] on connection failure.
        # Caller wraps in try/except and surfaces a structured 500.
        raise RuntimeError("locations stats query returned no rows")
    row = rows[0]

    # with_feedback_count + feedback trend from the local audit log.
    # Generic events table is keyed by `type`; idx_events_type carries
    # the WHERE so this is fast even at 10k+ events.
    feedback_events = get_recent_events(limit=10000, event_type="location_feedback")
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


def _ev_after(ev: dict, cutoff: datetime) -> bool:
    """Helper: did `ev` happen after `cutoff`?

    Audit events store ISO8601 timestamps. Tolerate both timezone-aware
    and naive timestamps (older events were logged without tz info).
    Naive timestamps are assumed to be UTC, matching the convention
    in `_now_iso()` in audit_log.py.
    """
    ts = ev.get("timestamp")
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
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

    if search:
        where_clauses.append(
            "(LOWER(l.name) LIKE :search_pat OR LOWER(o.name) LIKE :search_pat)"
        )
        params["search_pat"] = f"%{search.lower()}%"

    if category:
        # Category filter: location must have AT LEAST ONE service in
        # any of the named categories. EXISTS subquery so the predicate
        # short-circuits per location.
        where_clauses.append("""
        EXISTS (
            SELECT 1 FROM service_at_locations sal_cat
            JOIN service_taxonomy st_cat ON sal_cat.service_id = st_cat.service_id
            JOIN taxonomies t_cat ON st_cat.taxonomy_id = t_cat.id
            WHERE sal_cat.location_id = l.id
              AND t_cat.name = ANY(:category_list)
        )
        """)
        params["category_list"] = list(category)

    # has_issues toggle: missing phone OR missing address OR missing
    # hours OR has any negative recent feedback. This is the union
    # so it surfaces every location that needs SOME kind of attention.
    if has_issues:
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
        )
        """)
        # The "OR has negative recent feedback" branch is harder to
        # express in pure SQL without bringing the audit-log into the
        # join chain. v1 covers the three structural-data branches;
        # post-v1 we can intersect with the in-memory recent-flags
        # set if ops triage demands it. Documented limitation.

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # The list query — returns rows for the current page.
    # Notes on the JOIN tree:
    #   * physical_addresses is LEFT JOIN because not all locations
    #     have address rows (data quality issue we want to surface,
    #     not exclude).
    #   * best_phone is the same pattern used by the chat-side query
    #     to pick a single phone per location; reused here for
    #     consistency. Inlined below as a LATERAL — easier to read.
    #   * service_count is computed inline via a correlated subquery
    #     (small per-row cost, fine at this scale; could be moved
    #     to a CTE if perf demands).
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

        -- Top 3 distinct taxonomy names + remainder
        (SELECT ARRAY(
           SELECT t_cat.name FROM service_at_locations sal_t
           JOIN service_taxonomy st_t ON sal_t.service_id = st_t.service_id
           JOIN taxonomies t_cat ON st_t.taxonomy_id = t_cat.id
           WHERE sal_t.location_id = l.id
           GROUP BY t_cat.name
           ORDER BY COUNT(*) DESC, t_cat.name
           LIMIT 3
        )) AS top_categories,

        (SELECT COUNT(DISTINCT t_cat.name) FROM service_at_locations sal_tc
         JOIN service_taxonomy st_tc ON sal_tc.service_id = st_tc.service_id
         JOIN taxonomies t_cat ON st_tc.taxonomy_id = t_cat.id
         WHERE sal_tc.location_id = l.id
        ) AS distinct_categories_count,

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

    # Compute "recent_flags" + "has_reviews" from the audit log.
    # We pull all location_feedback events once (capped at 10k for
    # safety; current volume is far below that) and bucket by
    # location_id. Faster than N subqueries; same answer.
    fb_events = get_recent_events(limit=10000, event_type="location_feedback")
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

    locations: list[dict] = []
    for r in list_rows:
        loc_id = str(r.get("location_id", ""))
        city = r.get("city")
        borough_label = city if city in NYC_BOROUGHS else "Other"
        top_cats: list[str] = list(r.get("top_categories") or [])
        distinct_total = int(r.get("distinct_categories_count") or 0)
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
            CASE
                WHEN pa.city IN ('Manhattan', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island')
                    THEN pa.city
                ELSE 'Other'
            END AS borough,
            l.last_validated_at
        FROM locations l
        LEFT JOIN physical_addresses pa ON pa.location_id = l.id
    )
    SELECT
        borough,
        COUNT(*) AS location_count,
        COUNT(*) FILTER (
            WHERE last_validated_at >= CURRENT_DATE - INTERVAL '%(fresh)s days'
        ) AS fresh_count,
        (SELECT COUNT(*) FROM service_at_locations sal
         JOIN locations l2 ON sal.location_id = l2.id
         LEFT JOIN physical_addresses pa2 ON pa2.location_id = l2.id
         WHERE CASE
                  WHEN pa2.city IN ('Manhattan', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island')
                       THEN pa2.city
                  ELSE 'Other'
               END = labeled.borough
        ) AS service_count
    FROM labeled
    GROUP BY borough
    """ % {"fresh": FRESHNESS_THRESHOLD_DAYS}

    grouped_rows = _execute_sql(grouped_sql, {})

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
            CASE
                WHEN pa.city IN ('Manhattan', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island')
                    THEN pa.city
                ELSE 'Other'
            END AS borough
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
    """
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
    """
    sql = """
    SELECT
        t.name AS category,
        CASE
            WHEN pa.city IN ('Manhattan', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island')
                THEN pa.city
            ELSE 'Other'
        END AS borough,
        COUNT(DISTINCT l.id) AS location_count
    FROM taxonomies t
    JOIN service_taxonomy st ON st.taxonomy_id = t.id
    JOIN service_at_locations sal ON sal.service_id = st.service_id
    JOIN locations l ON l.id = sal.location_id
    LEFT JOIN physical_addresses pa ON pa.location_id = l.id
    GROUP BY t.name, borough
    """
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
        via _stated_borough_from_city).
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
    from app.rag.boundaries import borough_from_coords
    from app.rag.query_executor import _stated_borough_from_city

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
        stated = _stated_borough_from_city(stated_city) if stated_city else None

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
