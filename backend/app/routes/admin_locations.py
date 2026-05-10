"""
Locations admin — HTTP endpoints.

Sub-router mounted onto the existing /admin router (which provides
the require_admin_key auth dependency for all child routes). New
endpoints register at /admin/api/locations/<x> and are reachable
from the frontend at /api/admin/locations/<x> via the catch-all
proxy at frontend-next/src/app/api/admin/[...slug]/route.ts.

Endpoints (day 1):
    GET /admin/api/locations/stats
    GET /admin/api/locations/list

Subsequent days add: by-borough, heatmap, coordinate-issues,
by-category, stale-categories, feedback-aggregates,
feedback-comments, integrity-checks, timeseries.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.services.locations_admin import (
    get_locations_stats,
    get_locations_list,
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
            "Filter to rows missing phone OR address OR hours. "
            "Recent-feedback issues are a v1 limitation; not yet "
            "intersected here."
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
