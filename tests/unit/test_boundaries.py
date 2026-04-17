"""Tests for app.rag.boundaries — coordinate-to-borough containment lookups.

Covers:
  - Basic borough identification for all five NYC boroughs
  - Out-of-NYC coordinates (NJ, LI, ocean) return None
  - Invalid inputs (None, out-of-range, lat/lon swaps) return None safely
  - Lazy loading: module doesn't parse GeoJSON at import time
  - Thread safety under concurrent first-call
  - Near-boundary points (waterways, bridges) classify correctly

Uses real NYC landmark coordinates as ground truth — the same fixtures
used to validate the vendored GeoJSON during audit.
"""

import threading

import pytest

from app.rag.boundaries import (
    borough_from_coords,
    is_loaded,
    _reset_for_tests,
)


# Reset state before each test so we can exercise the lazy-load path
# deterministically. _reset_for_tests is the private API for exactly this.
@pytest.fixture(autouse=True)
def _reset_boundaries_state():
    _reset_for_tests()
    yield
    _reset_for_tests()


# -------------------------------------------------------------------------
# LANDMARK-BASED BOROUGH IDENTIFICATION
# -------------------------------------------------------------------------
# Each test case is a real NYC landmark with an unambiguous borough.
# If any of these break, the vendored GeoJSON is wrong/misaligned.

class TestLandmarkIdentification:
    """Verify well-known NYC locations classify to the correct borough."""

    def test_empire_state_building_is_manhattan(self):
        assert borough_from_coords(40.7484, -73.9857) == "Manhattan"

    def test_battery_park_is_manhattan(self):
        assert borough_from_coords(40.7033, -74.0170) == "Manhattan"

    def test_washington_heights_is_manhattan(self):
        assert borough_from_coords(40.8417, -73.9394) == "Manhattan"

    def test_yankee_stadium_is_bronx(self):
        assert borough_from_coords(40.8296, -73.9262) == "Bronx"

    def test_riverdale_is_bronx(self):
        assert borough_from_coords(40.8970, -73.9096) == "Bronx"

    def test_co_op_city_is_bronx(self):
        assert borough_from_coords(40.8706, -73.8249) == "Bronx"

    def test_barclays_center_is_brooklyn(self):
        assert borough_from_coords(40.6826, -73.9754) == "Brooklyn"

    def test_williamsburg_is_brooklyn(self):
        assert borough_from_coords(40.7081, -73.9571) == "Brooklyn"

    def test_coney_island_is_brooklyn(self):
        assert borough_from_coords(40.5745, -73.9837) == "Brooklyn"

    def test_flushing_is_queens(self):
        assert borough_from_coords(40.7595, -73.8301) == "Queens"

    def test_astoria_is_queens(self):
        assert borough_from_coords(40.7723, -73.9196) == "Queens"

    def test_jfk_airport_is_queens(self):
        assert borough_from_coords(40.6413, -73.7781) == "Queens"

    def test_staten_island_ferry_terminal_is_si(self):
        assert borough_from_coords(40.6432, -74.0731) == "Staten Island"

    def test_tottenville_is_staten_island(self):
        assert borough_from_coords(40.5118, -74.2488) == "Staten Island"


# -------------------------------------------------------------------------
# OUT-OF-NYC RETURNS None
# -------------------------------------------------------------------------

class TestOutsideNYC:
    """Points outside NYC's five boroughs return None, not a wrong borough."""

    def test_long_island_returns_none(self):
        # Hicksville, NY — clearly Long Island, not Queens
        assert borough_from_coords(40.7684, -73.5251) is None

    def test_jersey_city_returns_none(self):
        assert borough_from_coords(40.7178, -74.0431) is None

    def test_hoboken_returns_none(self):
        assert borough_from_coords(40.7439, -74.0324) is None

    def test_atlantic_ocean_returns_none(self):
        # Well south of Coney Island
        assert borough_from_coords(40.5000, -73.9000) is None

    def test_yonkers_returns_none(self):
        # Yonkers, NY — just north of the Bronx
        assert borough_from_coords(40.9312, -73.8987) is None


# -------------------------------------------------------------------------
# INVALID INPUTS RETURN None SAFELY (no crashes)
# -------------------------------------------------------------------------

class TestInvalidInputs:
    """Bad inputs return None rather than crashing."""

    def test_none_lat_returns_none(self):
        assert borough_from_coords(None, -73.9857) is None

    def test_none_lon_returns_none(self):
        assert borough_from_coords(40.7484, None) is None

    def test_both_none_returns_none(self):
        assert borough_from_coords(None, None) is None

    def test_swapped_lat_lon_returns_none(self):
        # User passes (-73.9857, 40.7484) — lat value out of [-90, 90].
        # Must not crash; must not accidentally classify as a borough.
        assert borough_from_coords(-73.9857, 40.7484) is None

    def test_out_of_range_lat_returns_none(self):
        assert borough_from_coords(91.0, -73.9857) is None
        assert borough_from_coords(-91.0, -73.9857) is None

    def test_out_of_range_lon_returns_none(self):
        assert borough_from_coords(40.7484, 181.0) is None
        assert borough_from_coords(40.7484, -181.0) is None

    def test_zero_zero_returns_none(self):
        # Null Island. Valid range, but obviously not NYC.
        assert borough_from_coords(0.0, 0.0) is None


# -------------------------------------------------------------------------
# LAZY LOADING AND THREAD SAFETY
# -------------------------------------------------------------------------

class TestLazyLoading:
    """GeoJSON is parsed on first call, not at import."""

    def test_not_loaded_before_first_call(self):
        _reset_for_tests()
        assert is_loaded() is False

    def test_loaded_after_first_call(self):
        _reset_for_tests()
        assert is_loaded() is False
        borough_from_coords(40.7484, -73.9857)
        assert is_loaded() is True

    def test_load_is_idempotent(self):
        # Second call does not re-parse
        _reset_for_tests()
        borough_from_coords(40.7484, -73.9857)
        assert is_loaded() is True
        # Should not raise or re-init — just returns the cached tree
        result = borough_from_coords(40.7484, -73.9857)
        assert result == "Manhattan"
        assert is_loaded() is True


class TestThreadSafety:
    """Concurrent first-calls don't race on the lazy init."""

    def test_concurrent_first_calls(self):
        _reset_for_tests()
        results = []
        errors = []

        def worker():
            try:
                r = borough_from_coords(40.7484, -73.9857)
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"
        assert len(results) == 20
        assert all(r == "Manhattan" for r in results)


# -------------------------------------------------------------------------
# NEAR-BOUNDARY POINTS (the interesting cases)
# -------------------------------------------------------------------------

class TestNearBoundaryPoints:
    """Coordinates near waterway borders should still classify correctly.

    These tests defend against over-aggressive polygon simplification —
    if any of these stop passing after a tolerance change, we simplified
    too much.
    """

    def test_harlem_south_of_bronx_border(self):
        # 145th St in Harlem — Manhattan side, just south of the Harlem River
        assert borough_from_coords(40.8210, -73.9442) == "Manhattan"

    def test_south_bronx_north_of_manhattan_border(self):
        # Mott Haven — Bronx side, just north of the Harlem River
        assert borough_from_coords(40.8089, -73.9230) == "Bronx"

    def test_greenpoint_is_brooklyn(self):
        # Greenpoint sits just across Newtown Creek from Queens (LIC)
        assert borough_from_coords(40.7300, -73.9537) == "Brooklyn"

    def test_long_island_city_is_queens(self):
        # LIC across the East River from Manhattan
        assert borough_from_coords(40.7447, -73.9485) == "Queens"

    def test_dumbo_is_brooklyn(self):
        # DUMBO, across the Brooklyn Bridge from Manhattan
        assert borough_from_coords(40.7033, -73.9887) == "Brooklyn"


# -------------------------------------------------------------------------
# RETURN TYPE CONTRACT
# -------------------------------------------------------------------------

class TestReturnTypes:
    """Return values match the declared type and match canonical DB names."""

    def test_returns_exactly_these_borough_names(self):
        """Must return the exact strings used elsewhere in the codebase."""
        allowed = {"Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"}
        for lat, lon in [
            (40.7484, -73.9857),  # Manhattan
            (40.6826, -73.9754),  # Brooklyn
            (40.7595, -73.8301),  # Queens
            (40.8296, -73.9262),  # Bronx
            (40.6432, -74.0731),  # Staten Island
        ]:
            result = borough_from_coords(lat, lon)
            assert result in allowed, (
                f"Returned {result!r} — must be one of {allowed} to match "
                f"_BOROUGH_TO_PRIMARY_CITY keys and other code"
            )

    def test_returns_none_or_string(self):
        """Every return must be either None or a string — never, say, an int or a polygon."""
        for lat, lon in [
            (40.7484, -73.9857),  # inside
            (40.0000, -73.0000),  # outside
            (None, None),         # invalid
        ]:
            result = borough_from_coords(lat, lon)
            assert result is None or isinstance(result, str)
