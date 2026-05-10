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
            WHERE l.last_validated_at >= CURRENT_DATE - INTERVAL '%(fresh)s days'
        ) AS fresh_location_count
    FROM taxonomies t
    JOIN service_taxonomy st ON st.taxonomy_id = t.id
    JOIN services s          ON s.id = st.service_id
    JOIN service_at_locations sal ON sal.service_id = s.id
    JOIN locations l         ON l.id = sal.location_id
    GROUP BY t.name
    """ % {"fresh": FRESHNESS_THRESHOLD_DAYS}
    rows = _execute_sql(sql, {})

    # Step 2: gather demand from no_result_by_service. Imported here
    # rather than at module top to avoid a circular: audit_log doesn't
    # depend on locations_admin and we want to keep it that way.
    from app.services.audit_log import get_stats as _get_stats
    stats = _get_stats()
    no_result = stats.get("no_result_by_service") or {}

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
        OR MAX(l.last_validated_at) < CURRENT_DATE - INTERVAL '%(stale)s days'
    )
    ORDER BY MAX(l.last_validated_at) ASC NULLS FIRST
    """ % {"stale": STALE_CATEGORY_LOOKBACK_DAYS}
    rows = _execute_sql(sql, {})

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
    events = get_recent_events(limit=10000, event_type="location_feedback")

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

        ts = ev.get("timestamp") or ""
        if ts > bucket["last_event_at"]:
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
        smoothed = (neg + 1) / (total_rated + 2) if total_rated >= 0 else 0.5
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
