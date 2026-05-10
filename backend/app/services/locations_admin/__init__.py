"""Locations admin — aggregation queries and integrity checks.

Day-1 module exposes section 1 (`get_locations_stats`) and
section 2b (`get_locations_list`) of the Locations admin page.
Subsequent days extend with sections 3-7.

All functions return dicts that match their TypeScript counterparts
in `frontend-next/src/lib/admin/locations-types.ts`. The contract
verify script (`frontend-next/scripts/verify/locations-contract.mjs`)
locks the surface in place.
"""
from app.services.locations_admin.aggregations import (
    get_locations_stats,
    get_locations_list,
    get_freshness_histogram,
    get_locations_by_borough,
    FRESHNESS_THRESHOLD_DAYS,
    RECENT_FLAGS_LOOKBACK_DAYS,
    HEATMAP_TOP_N_CATEGORIES,
    FEEDBACK_MIN_SAMPLE,
    TIMESERIES_WEEKS,
    NYC_BOROUGHS,
)

__all__ = [
    "get_locations_stats",
    "get_locations_list",
    "get_freshness_histogram",
    "get_locations_by_borough",
    "FRESHNESS_THRESHOLD_DAYS",
    "RECENT_FLAGS_LOOKBACK_DAYS",
    "HEATMAP_TOP_N_CATEGORIES",
    "FEEDBACK_MIN_SAMPLE",
    "TIMESERIES_WEEKS",
    "NYC_BOROUGHS",
]
