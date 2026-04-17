"""
NYC borough geographic boundaries.

Given a (lat, lon) coordinate pair inside NYC, determine which of the five
boroughs contains it. Uses NYC Department of City Planning borough polygons
(water-clipped, simplified to ~0.0001° precision) vendored at
backend/app/rag/data/nyc_boroughs.geojson. Source:
https://data.cityofnewyork.us/City-Government/Borough-Boundaries/gthc-hcne

Why this exists
---------------
The Streetlives DB's physical_addresses table has no `borough` column, and
`pa.city` has known data-quality issues (casing inconsistencies, wrong-
borough assignments, typos). Neither can be trusted for accurate borough
classification. This module derives the borough from the service's
`l.position` coordinates directly — the authoritative signal.

Used for two things:

1. **Detecting data-quality issues in pa.city**: post-query validator compares
   the geographic borough to the pa.city-inferred borough and logs mismatches.
   This surfaces services whose stated city is wrong so Community Information
   Specialists can correct them upstream.

2. **Future: replacing pa.city-based borough filtering** entirely with
   coordinate-based ST_Within queries. For now, pa.city remains the primary
   filter — this module is additive/defensive.

See docs/BOUNDARY_AUDIT.md for the full context and the reported "Manhattan
service actually in Bronx" bug that motivated this.

Design
------
- Lazy loading: the 195KB GeoJSON is parsed on first call, not at import time.
- Thread-safe init via a lock — FastAPI runs async handlers, and we don't
  want two concurrent requests both parsing the file.
- STRtree spatial index: five polygons is small enough that linear scan
  would work, but STRtree is ~2x faster per lookup and costs nothing to set up.
- Returns the canonical borough name ("Manhattan", "Brooklyn", "Queens",
  "Bronx", "Staten Island") matching the values used elsewhere in this
  codebase (e.g., _BOROUGH_TO_PRIMARY_CITY in query_executor.py).
- Returns None for coordinates outside NYC (e.g., Long Island, New Jersey,
  or in the ocean/bay).

Performance
-----------
- Load: ~50-100ms one-time parse at first call.
- Per-lookup: ~20-50 microseconds with the STRtree index.
- 25 service cards per search → ~1ms total overhead. Negligible.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

from shapely.geometry import Point, shape
from shapely.strtree import STRtree


logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).parent / "data" / "nyc_boroughs.geojson"

# The GeoJSON uses these names; they match our canonical values.
_EXPECTED_NAMES = {"Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"}

# Lazy-loaded state
_tree: Optional[STRtree] = None
_polygons: list = []          # parallel to _tree index → shapely polygon
_polygon_names: list[str] = []  # parallel to _tree index → borough name
_init_lock = threading.Lock()


def _load() -> None:
    """Parse the vendored GeoJSON and build the spatial index.

    Idempotent and thread-safe. First caller populates the module-level
    tree; subsequent callers see it already built.
    """
    global _tree, _polygons, _polygon_names

    # Double-checked locking: the fast path is "already loaded"; only
    # take the lock if we might need to load.
    if _tree is not None:
        return

    with _init_lock:
        if _tree is not None:
            return  # another thread beat us to it

        if not _DATA_PATH.exists():
            raise FileNotFoundError(
                f"NYC borough boundaries GeoJSON not found at {_DATA_PATH}. "
                f"This file should be vendored in the repo — it's not a "
                f"runtime download. See docs/BOUNDARY_AUDIT.md §Data Sources."
            )

        with _DATA_PATH.open() as f:
            geojson = json.load(f)

        features = geojson.get("features", [])
        polys: list = []
        names: list[str] = []
        seen_names = set()
        for feat in features:
            name = feat.get("properties", {}).get("name")
            geom_data = feat.get("geometry")
            if not name or not geom_data:
                logger.warning(
                    "Skipping malformed borough feature (missing name or geometry)"
                )
                continue
            if name not in _EXPECTED_NAMES:
                logger.warning(
                    "Unexpected borough name in GeoJSON: %r (expected one of %s)",
                    name, sorted(_EXPECTED_NAMES),
                )
                continue
            polys.append(shape(geom_data))
            names.append(name)
            seen_names.add(name)

        missing = _EXPECTED_NAMES - seen_names
        if missing:
            # Don't fail — degrade gracefully. Log loud so ops notices.
            logger.error(
                "NYC boroughs GeoJSON missing expected boroughs: %s. "
                "Coordinate-based borough lookup will return None for those "
                "areas.", sorted(missing),
            )

        _polygons = polys
        _polygon_names = names
        _tree = STRtree(polys)

        logger.info(
            "Loaded NYC borough boundaries: %d polygons (%s)",
            len(polys), ", ".join(names),
        )


def borough_from_coords(lat: float, lon: float) -> Optional[str]:
    """Return the canonical NYC borough name containing the given point,
    or None if the point is outside all five boroughs.

    Args:
        lat: Latitude in WGS84 decimal degrees.
        lon: Longitude in WGS84 decimal degrees (negative in NYC).

    Returns:
        One of "Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island",
        or None if the point is outside NYC (or in a water area excluded
        from the DCP polygons).

    Note:
        The input signature is (lat, lon) but Shapely's Point expects
        (x, y) == (lon, lat). This function handles the swap; callers
        should pass lat first to match Python conventions.

    Thread-safe. First call may take ~100ms to parse the vendored GeoJSON;
    subsequent calls are ~50 microseconds each.
    """
    if lat is None or lon is None:
        return None

    # Sanity check on inputs — NYC is roughly lat 40.49–40.92, lon -74.27 to -73.69.
    # Reject obviously-wrong values early to avoid spending STRtree cycles
    # and to surface coordinate swaps (lat/lon transposed is a common bug).
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        logger.warning(
            "borough_from_coords called with out-of-range coords: lat=%s lon=%s",
            lat, lon,
        )
        return None

    if _tree is None:
        _load()

    # Guard against _load failing to populate (missing file with
    # graceful-degradation branch above).
    if _tree is None or not _polygons:
        return None

    # Shapely Point takes (x, y) == (lon, lat)
    point = Point(lon, lat)

    # STRtree.query returns candidate indices; filter with actual contains()
    # because the tree uses bounding-box pre-filtering.
    for idx in _tree.query(point):
        if _polygons[idx].contains(point):
            return _polygon_names[idx]

    return None


def is_loaded() -> bool:
    """Return True if the GeoJSON has been parsed. For tests and health checks."""
    return _tree is not None


def _reset_for_tests() -> None:
    """Clear the lazy-loaded state. ONLY for tests that need to exercise
    the load path from a clean slate. Not part of the public API.
    """
    global _tree, _polygons, _polygon_names
    with _init_lock:
        _tree = None
        _polygons = []
        _polygon_names = []
