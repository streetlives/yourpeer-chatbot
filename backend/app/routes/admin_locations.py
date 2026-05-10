"""
Locations admin — HTTP endpoints.

Sub-router mounted onto the existing /admin router (which provides
the require_admin_key auth dependency for all child routes). New
endpoints register at /admin/api/locations/<x> and are reachable
from the frontend at /api/admin/locations/<x> via the catch-all
proxy at frontend-next/src/app/api/admin/[...slug]/route.ts.

Full endpoint list (one per page section):
    GET /admin/api/locations/stats                  (section 1)
    GET /admin/api/locations/freshness-histogram    (section 2a)
    GET /admin/api/locations/list                   (section 2b)
    GET /admin/api/locations/by-borough             (section 3a)
    GET /admin/api/locations/heatmap                (section 3b)
    GET /admin/api/locations/coordinate-issues      (section 3c)
    GET /admin/api/locations/category-coverage      (section 4a)
    GET /admin/api/locations/stale-categories       (section 4b)
    GET /admin/api/locations/feedback-aggregates    (sections 5a + 5b)
    GET /admin/api/locations/feedback-comments      (section 5c)
    GET /admin/api/locations/integrity-callouts     (section 6)
    GET /admin/api/locations/timeseries             (section 7)

Each endpoint is a thin wrapper around its aggregation function in
locations_admin/aggregations.py — input parsing + try/except + the
shared _admin_error helper. No business logic lives here.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.services.locations_admin import (
    get_locations_stats,
    get_locations_list,
    get_freshness_histogram,
    get_locations_by_borough,
    get_service_borough_heatmap,
    get_coordinate_issues,
    get_category_coverage,
    get_stale_categories,
    get_location_feedback_aggregates,
    get_recent_feedback_comments,
    get_data_integrity_callouts,
    get_locations_timeseries,
    RECENT_COMMENTS_DEFAULT_LIMIT,
    RECENT_COMMENTS_MAX_LIMIT,
)

logger = logging.getLogger(__name__)

# This sub-router is mounted onto the parent /admin router (see
# routes/admin.py near the bottom). Auth is inherited via the
# parent's `dependencies=[Depends(require_admin_key)]` — no need
# to repeat it here, and repeating would risk drift if the auth
# story ever changes.
router = APIRouter(prefix="/api/locations", tags=["admin", "locations"])


def _admin_error(endpoint: str, e: Exception) -> JSONResponse:
    """Mirror of the helper in routes/admin.py — kept inline to avoid
    a cross-module import that introduces a circular risk if the parent
    admin module ever imports from here. The duplication is small and
    the bodies are identical; either both change together (fine) or
    one is a deliberate divergence (also fine).
    """
    logger.exception(f"Locations admin API error in {endpoint}")
    return JSONResponse(
        status_code=500,
        content={
            "error": True,
            "endpoint": endpoint,
            "detail": f"{type(e).__name__}: {e}",
        },
    )


@router.get("/stats")
def locations_stats():
    """Section 1: top stat strip — six counts plus 7-day trend deltas."""
    try:
        return get_locations_stats()
    except Exception as e:
        return _admin_error("/api/locations/stats", e)


@router.get("/list")
def locations_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    sort_key: str = Query(
        "last_validated_at",
        description=(
            "Column to sort by. Allowed: name, organization, city, "
            "service_count, last_validated_at, recent_flags. Other "
            "values fall back to last_validated_at."
        ),
    ),
    sort_dir: str = Query(
        "asc_nulls_first",
        description=(
            "Sort direction. Allowed: asc, desc, asc_nulls_first, "
            "desc_nulls_first. Default surfaces never-verified rows "
            "at the top of the triage view."
        ),
    ),
    borough: Optional[list[str]] = Query(
        None,
        description=(
            "Filter to one or more borough labels. Allowed values: "
            "Manhattan, Brooklyn, Queens, Bronx, Staten Island, Other. "
            "Multiple values are OR'd."
        ),
    ),
    age_bucket: Optional[str] = Query(
        None,
        description=(
            "Filter by last_validated_at bucket. Allowed: lt30, "
            "30to90, 90to180, 180to365, gt365, never."
        ),
    ),
    has_issues: bool = Query(
        False,
        description=(
            "Filter to rows missing phone OR address OR hours OR with any "
            "negative recent feedback (location_feedback events with at "
            "least one False criterion in the last 45 days). Union — any "
            "one of these is enough to surface the row."
        ),
    ),
    category: Optional[list[str]] = Query(
        None,
        description="Filter to locations offering at least one service in any of the named taxonomy categories.",
    ),
    search: Optional[str] = Query(
        None, description="Free-text match on location name or organization (case-insensitive substring).",
    ),
):
    """Section 2b: paginated, sortable, filterable triage table.

    Default behavior matches the spec: most-stale-first
    (last_validated_at ASC NULLS FIRST) so locations that have never
    been verified rise to the top of the queue.

    All filtering happens in SQL (one round-trip per page); the
    audit-log pass for `recent_flags` and `has_reviews` is in-memory
    against the events table and runs in milliseconds at current scale.
    """
    try:
        return get_locations_list(
            page=page,
            page_size=page_size,
            sort_key=sort_key,
            sort_dir=sort_dir,
            borough=borough,
            age_bucket=age_bucket,
            has_issues=has_issues,
            category=category,
            search=search,
        )
    except Exception as e:
        return _admin_error("/api/locations/list", e)


@router.get("/freshness-histogram")
def locations_freshness_histogram():
    """Section 2a: 6-bucket histogram of last_validated_at age.

    Buckets are stable across calls (same keys, same labels, same
    order); only the counts change. Bucket keys match the
    `age_bucket` filter values on `/list` so a histogram-bar click
    can drive the table filter without translation.
    """
    try:
        return get_freshness_histogram()
    except Exception as e:
        return _admin_error("/api/locations/freshness-histogram", e)


@router.get("/by-borough")
def locations_by_borough():
    """Section 3a: per-borough rollup table.

    Returns one row per borough (5 NYC boroughs + Other), always
    in the canonical display order regardless of which boroughs
    appear in the data. Empty boroughs render with zeros, never
    missing — gives ops a stable shape across query runs.
    """
    try:
        return get_locations_by_borough()
    except Exception as e:
        return _admin_error("/api/locations/by-borough", e)


@router.get("/heatmap")
def locations_heatmap():
    """Section 3b: service-category × borough coverage heatmap.

    Returns ALL taxonomy categories with their per-borough location
    counts. Categories are sorted by total_locations DESC so the
    frontend can default to top-N (HEATMAP_TOP_N_CATEGORIES = 10)
    and offer an expand toggle for the rest.

    Cells are distinct-location counts: a multi-service location in
    Brooklyn that offers food, clothing, and showers contributes 1
    to each of Brooklyn-Food, Brooklyn-Clothing, Brooklyn-Showers
    (correct semantics) but never more than 1 to any single cell.
    """
    try:
        return get_service_borough_heatmap()
    except Exception as e:
        return _admin_error("/api/locations/heatmap", e)


@router.get("/coordinate-issues")
def locations_coordinate_issues():
    """Section 3c: locations with coordinate-vs-stated-city mismatches.

    Includes two kinds of data-quality issues:
      * Coordinates outside NYC entirely (likely typo'd lat/lon)
      * Coordinates fall in a different borough than the stated city
        (one of them is wrong; manual verification needed)

    Both kinds are returned in a single `issues` array; rows with
    `computed_borough: null` are the outside-NYC kind. Sorted with
    outside-NYC first since those are the most concerning data bugs.

    Aggregate counters (`total_with_coords`, `outside_nyc_count`)
    let the frontend show "X of Y locations have issues" framing.
    """
    try:
        return get_coordinate_issues()
    except Exception as e:
        return _admin_error("/api/locations/coordinate-issues", e)


@router.get("/category-coverage")
def locations_category_coverage():
    """Section 4a: per-taxonomy coverage with demand:supply ratio.

    Returns one row per taxonomy with service count, distinct location
    count, fresh-location-count, and (where mappable from chat
    template names) demand signal. Categories are sorted by
    demand:supply DESC, NULLS LAST so high-demand-thin-supply
    categories surface first — the operationally useful default.

    Demand mapping is best-effort: chat templates that don't map
    to a fixed taxonomy set (e.g. OtherServicesQuery, org-name
    searches) contribute to an `uncategorized_demand` aggregate
    counter rather than being attributed to specific taxonomies.
    """
    try:
        return get_category_coverage()
    except Exception as e:
        return _admin_error("/api/locations/category-coverage", e)


@router.get("/stale-categories")
def locations_stale_categories():
    """Section 4b: top-N taxonomies where every offering location is stale.

    A taxonomy counts as stale if its most-recently-verified offering
    location is older than STALE_CATEGORY_LOOKBACK_DAYS (180d) — i.e.
    NOT EVEN ONE location in the category has been verified recently.

    Returns top STALE_CATEGORIES_TOP_N (10) plus a `total_stale`
    counter so the frontend can surface "showing 10 of 23 stale
    categories" framing when applicable.
    """
    try:
        return get_stale_categories()
    except Exception as e:
        return _admin_error("/api/locations/stale-categories", e)


@router.get("/feedback-aggregates")
def locations_feedback_aggregates():
    """Sections 5a + 5b: location-feedback aggregation.

    Returns:
      - `most_flagged`: top-N locations sorted by smoothed negative
        ratio. FEEDBACK_MIN_SAMPLE eligibility cutoff (≥2 events)
        avoids one-off noise. Laplace add-one smoothing
        ((neg+1)/(total+2)) prevents the "tied at 100%" cliff that
        a raw negative-ratio sort would produce on small samples.
      - `criterion_summary`: per-criterion population %.
        How often does safety / friendliness / cleanliness /
        queer_friendly get flagged across ALL feedback events?
        Pairs with `most_flagged` to give context: an individual
        location's 50% safety-negative rate reads differently
        when the population baseline is 5% vs 30%.

    Sections 5a and 5b are returned in one response because they
    share the same source events and the Python aggregation pass
    naturally produces both. Two separate endpoints would mean two
    scans of the same event log.
    """
    try:
        return get_location_feedback_aggregates()
    except Exception as e:
        return _admin_error("/api/locations/feedback-aggregates", e)


@router.get("/feedback-comments")
def locations_feedback_comments(
    limit: int = Query(
        RECENT_COMMENTS_DEFAULT_LIMIT,
        ge=1,
        le=RECENT_COMMENTS_MAX_LIMIT,
        description=(
            "Max number of comments to return. Defaults to 50; capped "
            "at 200 to avoid pulling the entire event log in one go."
        ),
    ),
):
    """Section 5c: reverse-chronological list of recent location_feedback
    events that include a non-empty comment.

    The qualitative companion to /feedback-aggregates — comments
    often surface things that don't fit any criterion checkbox.
    Each item carries enough context (session_id, location_id,
    criteria-flagged) for the frontend to deep-link into the
    originating session's transcript drawer.

    Filters out empty / whitespace-only comments before counting,
    so total_with_comments reads as "events with content," not
    "events that had a comment field."
    """
    try:
        return get_recent_feedback_comments(limit=limit)
    except Exception as e:
        return _admin_error("/api/locations/feedback-comments", e)


@router.get("/integrity-callouts")
def locations_integrity_callouts():
    """Section 6: data-integrity callouts.

    Five queries fan out from one endpoint, each a count check.
    Returns the firing callouts (count > 0) in display order, plus
    `all_clear: True` when nothing fires — surfaces the positive
    state explicitly so the frontend can render a "✓ no integrity
    issues" callout instead of an empty list.
    """
    try:
        return get_data_integrity_callouts()
    except Exception as e:
        return _admin_error("/api/locations/integrity-callouts", e)


@router.get("/timeseries")
def locations_timeseries():
    """Section 7: weekly time series for the last TIMESERIES_WEEKS (=26).

    Returns three series in one response:
      * locations_added per week
      * locations_verified per week (last_validated_at touched)
      * feedback_events per week (location_feedback events)

    Always exactly TIMESERIES_WEEKS rows in chronological order.
    Empty weeks render as zeros, never missing — gives the frontend
    a continuous shape to plot without interpolation.
    """
    try:
        return get_locations_timeseries()
    except Exception as e:
        return _admin_error("/api/locations/timeseries", e)
