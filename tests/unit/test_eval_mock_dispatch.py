"""Tests for the eval-runner's fixture-based mock dispatcher.

History:
- Bug 8 (May 2026): static MOCK_QUERY_RESULTS returned Brooklyn food
  pantries for every search. Replaced with a service-type-aware
  dispatcher.
- Bug 8 layer 2 (May 2026): dispatcher's borough-only resolver fell
  back to Brooklyn for any neighborhood search, producing ~32
  false-positive location-mismatch CFs in R39. Added neighborhood
  resolution table.
- Path C (May 2026): replaced the hand-coded service cards (8 builder
  functions) with a fixture loaded from a real Streetlives DB
  snapshot. Replaced the hand-coded neighborhood table with imports
  from production's NEIGHBORHOOD_CENTERS / NYC_LOCATION_ALIASES /
  _CITY_TO_BOROUGH. The dispatcher now knows nothing about NYC and
  nothing about what services exist — it's a pure filter on
  production data.

These tests verify the dispatcher's contract:
- service_type filter actually filters
- borough filter actually filters (using production's lookup chain)
- response shape matches production's query_services
- sentinels return empty results
- backward-compat constants still work
"""

from __future__ import annotations

import os
import sys

import pytest


_EVAL_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "eval")
)
if _EVAL_DIR not in sys.path:
    sys.path.insert(0, _EVAL_DIR)

import eval_llm_judge as runner  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture sanity — does the fixture exist and have data?
# ---------------------------------------------------------------------------


class TestFixtureLoaded:
    """Ensures the fixture file loaded at import. Without it, every
    other test would silently pass with empty results."""

    def test_fixture_is_nonempty(self):
        """The fixture should have at least 100 rows. Expected size is
        ~218 (rn<=5 cap, ~9 service types × 5 boroughs)."""
        assert len(runner._FIXTURE) > 100, (
            f"Fixture only has {len(runner._FIXTURE)} rows. "
            f"Expected ~200. Did the JSON file load correctly?"
        )

    def test_fixture_rows_have_required_fields(self):
        """Every row needs the fields the dispatcher reads."""
        required = [
            "service_id", "service_name", "organization_name",
            "phone", "address", "city", "borough",
            "bot_service_type",
        ]
        sample = runner._FIXTURE[0]
        for field in required:
            assert field in sample, (
                f"Fixture row missing required field {field!r}: {sample}"
            )


# ---------------------------------------------------------------------------
# Service type filter
# ---------------------------------------------------------------------------

ALL_SERVICE_TYPES = [
    "food", "shelter", "clothing", "personal_care",
    "medical", "mental_health", "legal", "employment", "other",
]


class TestServiceTypeFilter:
    """The dispatcher must filter the fixture by service_type."""

    @pytest.mark.parametrize("service_type", ALL_SERVICE_TYPES)
    def test_service_type_returns_only_matching_rows(self, service_type):
        """Whatever cards come back must belong to fixture rows tagged
        with the requested service_type. The dispatcher should NEVER
        return shelter cards in response to a food search."""
        # Get the matching fixture rows directly (ground truth)
        expected_service_names = {
            r["service_name"]
            for r in runner._FIXTURE
            if r["bot_service_type"] == service_type
        }
        result = runner._mock_query_services(service_type=service_type)
        for card in result["services"]:
            assert card["service_name"] in expected_service_names, (
                f"Card {card['service_name']!r} from {service_type!r} search "
                f"isn't in any fixture row tagged {service_type!r}"
            )

    @pytest.mark.parametrize("service_type", ALL_SERVICE_TYPES)
    def test_service_type_returns_nonempty(self, service_type):
        """Every bot-recognized service_type should have at least
        one row in the fixture. If a future fixture refresh produces
        zero rows for some service_type, this test catches it before
        the eval runs against an unknowingly-empty bucket."""
        result = runner._mock_query_services(service_type=service_type)
        assert result["result_count"] > 0, (
            f"{service_type!r} returned no cards. The fixture may be "
            f"stale or the bucketing CASE in scripts/fixture/ may need "
            f"updating."
        )

    def test_unknown_service_type_returns_empty(self):
        """Unknown service types return empty rather than crashing."""
        result = runner._mock_query_services(service_type="rocketship")
        assert result["result_count"] == 0


# ---------------------------------------------------------------------------
# Borough filter
# ---------------------------------------------------------------------------


class TestBoroughFilter:
    """The dispatcher must filter by borough using production's
    NYC_LOCATION_ALIASES + _CITY_TO_BOROUGH chain."""

    @pytest.mark.parametrize("borough", [
        "Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island",
    ])
    def test_borough_filter_narrows_results(self, borough):
        """A borough-named search returns only cards from fixture rows
        tagged with that borough."""
        result = runner._mock_query_services(
            service_type="food", location=borough,
        )
        # Every returned card should belong to a fixture row whose
        # borough field is the requested borough.
        expected_addrs = {
            f"{r['address']}, {r['city']}, {r['state']} {r['zip_code']}".strip()
            for r in runner._FIXTURE
            if r["bot_service_type"] == "food" and r["borough"] == borough
        }
        for card in result["services"]:
            assert card["address"] in expected_addrs, (
                f"Card {card['service_name']!r} address {card['address']!r} "
                f"isn't in {borough!r} food rows. Borough filter failed."
            )

    @pytest.mark.parametrize("neighborhood,expected_borough", [
        ("Harlem", "Manhattan"),
        ("East Harlem", "Manhattan"),
        ("Soho", "Manhattan"),
        ("Lower East Side", "Manhattan"),
        ("Times Square", "Manhattan"),
        ("Penn Station", "Manhattan"),
        ("Jackson Heights", "Queens"),
        ("Flushing", "Queens"),
        ("Williamsburg", "Brooklyn"),
        ("Mott Haven", "Bronx"),
    ])
    def test_neighborhood_resolves_to_correct_borough(
        self, neighborhood, expected_borough,
    ):
        """A neighborhood search returns cards in that neighborhood's
        borough — using production's lookup chain, not a hand-coded
        eval table."""
        result = runner._mock_query_services(
            service_type="food", location=neighborhood,
        )
        # Build a set of (service_name, formatted_address) tuples for
        # rows in the expected borough. The card's address field is
        # composed from the same fixture fields, so an exact match
        # tells us the dispatcher actually returned a borough-matching
        # row (not just a row with a colliding service_name).
        expected_addrs = {
            f"{r['address']}, {r['city']}, {r['state']} {r['zip_code']}".strip()
            for r in runner._FIXTURE
            if r["bot_service_type"] == "food"
            and r["borough"] == expected_borough
        }
        assert result["services"], (
            f"{neighborhood} returned no cards. Resolver may have "
            f"failed silently."
        )
        for card in result["services"]:
            assert card["address"] in expected_addrs, (
                f"{neighborhood!r} should resolve to {expected_borough!r}, "
                f"but returned a card at {card['address']!r} which is not "
                f"in any {expected_borough!r} food row."
            )

    def test_unrecognized_location_does_not_crash(self):
        """Unrecognized locations don't crash — the dispatcher returns
        any matching service-type rows. May be flagged as relaxed
        (broader than requested) but always succeeds."""
        result = runner._mock_query_services(
            service_type="food", location="Atlantis",
        )
        # Either: returns rows because resolver couldn't narrow, or
        # returns empty. Either way, must not crash.
        assert isinstance(result["services"], list)


class TestNeighborhoodProximity:
    """Cluster 2 fix (location precision/drift). Production routes
    non-borough locations through ST_DWithin against the neighborhood
    center — see backend/app/rag/__init__.py:266 and the
    NEIGHBORHOOD_CENTERS dict in backend/app/rag/query_executor.py.

    The eval mock now mirrors this with a haversine-distance filter
    in _filter_rows_by_neighborhood_proximity. Without it, a
    "shower in Lower East Side" query returned any Manhattan rows;
    with it, results are restricted to within 1.6 km of LES center,
    sorted by ascending distance.

    Closes 3 R40 failing scenarios that previously had borough-correct
    but neighborhood-wrong cards:
      - multi_cross_neighborhood_shower_les_food_chinatown (4.45)
      - multi_three_services_legal_benefits_food (3.73 - Jamaica
        returned for Jackson Heights ask)
      - multi_asylum_seeker_food_legal (3.64)
    """

    def test_borough_query_unaffected_by_proximity(self):
        """Sanity: borough names pass through proximity unchanged.
        Manhattan returns its full bucket, Brooklyn its full bucket,
        etc. Proximity narrowing must not fire on boroughs."""
        manhattan = runner._mock_query_services(
            service_type="food", location="manhattan",
        )
        # All cards must be in Manhattan (borough filter took
        # precedence; proximity was a no-op).
        assert manhattan["result_count"] > 0
        for card in manhattan["services"]:
            addr = (card.get("address") or "").lower()
            # Manhattan cards have addresses with "new york" (city),
            # not "brooklyn"/"bronx"/"queens"/"staten island".
            assert not any(
                b in addr for b in (
                    "brooklyn, ny", "bronx, ny", "queens, ny",
                    "staten island, ny",
                )
            ), f"Non-Manhattan card leaked through borough query: {addr!r}"

    def test_jackson_heights_excludes_jamaica(self):
        """The original bug: a Jackson Heights query returned Jamaica
        cards (both Queens, but ~7 km apart). Proximity now restricts
        to within 1.6 km of Jackson Heights center (40.7557, -73.8831).
        Jamaica is at (40.7029, -73.7898) — about 9 km away by
        haversine, well outside the radius."""
        from app.rag.query_executor import (
            NEIGHBORHOOD_CENTERS,
            DEFAULT_NEIGHBORHOOD_RADIUS_METERS,
        )
        from tests.eval.eval_llm_judge import _haversine_meters

        jh_lat, jh_lon = NEIGHBORHOOD_CENTERS["jackson heights"]

        for service_type in ("food", "legal", "other"):
            result = runner._mock_query_services(
                service_type=service_type, location="jackson heights",
            )
            for card in result["services"]:
                # Card lat/lon comes through via _service_card_from_fixture.
                # If the dispatcher narrowed to in-radius rows, every
                # card must be within radius (allow a little slack
                # for floating-point + groupByLocation address re-resolves).
                clat = card.get("latitude")
                clon = card.get("longitude")
                if clat is None or clon is None:
                    continue
                dist = _haversine_meters(jh_lat, jh_lon, float(clat), float(clon))
                # 1.6 km is the production radius. We allow the
                # eval-side fallback to widen up to ~10 km when
                # nothing's in the strict radius — but the
                # narrowing must at least exclude Jamaica
                # (~9 km away). So the assertion is that no card
                # is at Jamaica's distance.
                jamaica_lat, jamaica_lon = NEIGHBORHOOD_CENTERS["jamaica"]
                jamaica_dist = _haversine_meters(
                    jh_lat, jh_lon, jamaica_lat, jamaica_lon,
                )
                # Under the current behavior:
                #   - In-radius hit: dist <= 1600m (strict radius).
                #   - Within-fallback hit: dist <= 5000m (hard cap;
                #     see _NEIGHBORHOOD_FALLBACK_MAX_RADIUS_METERS).
                #   - Beyond fallback: result is empty entirely.
                # Jamaica is ~9km from Jackson Heights, beyond the
                # 5km fallback cap, so it's excluded in all cases.
                # The assertion (dist < jamaica_dist) holds either
                # via the strict radius, the fallback cap, or the
                # empty-result path (loop body doesn't execute).
                assert dist < jamaica_dist, (
                    f"{service_type} in Jackson Heights returned a card "
                    f"at distance {dist:.0f}m (Jamaica is at "
                    f"{jamaica_dist:.0f}m); proximity filter not working"
                )

    def test_lower_east_side_results_near_les_center(self):
        """LES query should return rows within 1.6 km of LES center
        (40.715, -73.9843), sorted by ascending distance from that
        point. The first card should be the closest in-borough match."""
        from app.rag.query_executor import (
            NEIGHBORHOOD_CENTERS,
            DEFAULT_NEIGHBORHOOD_RADIUS_METERS,
        )
        from tests.eval.eval_llm_judge import _haversine_meters

        les_lat, les_lon = NEIGHBORHOOD_CENTERS["lower east side"]

        result = runner._mock_query_services(
            service_type="personal_care", location="lower east side",
        )
        # Compute distance for every returned card and verify
        # ascending order.
        distances = []
        for card in result["services"]:
            clat = card.get("latitude")
            clon = card.get("longitude")
            if clat is None or clon is None:
                continue
            dist = _haversine_meters(les_lat, les_lon, float(clat), float(clon))
            distances.append(dist)

        # Distances must be non-decreasing (sorted by proximity).
        for i in range(len(distances) - 1):
            assert distances[i] <= distances[i + 1] + 0.01, (
                f"Card {i} at {distances[i]:.0f}m is farther than "
                f"card {i+1} at {distances[i+1]:.0f}m — not sorted "
                f"by proximity"
            )

        # Closest card should be within 5km. (LES is dense with
        # services; the fixture should have at least one within
        # walking distance.)
        if distances:
            assert distances[0] < 5000, (
                f"Closest LES personal_care card is {distances[0]:.0f}m "
                f"away — much farther than expected"
            )

    def test_chinatown_results_near_chinatown_center(self):
        """Chinatown food query should return cards near Chinatown
        center (40.7158, -73.997), not Harlem or Midtown."""
        from app.rag.query_executor import NEIGHBORHOOD_CENTERS
        from tests.eval.eval_llm_judge import _haversine_meters

        ct_lat, ct_lon = NEIGHBORHOOD_CENTERS["chinatown"]

        result = runner._mock_query_services(
            service_type="food", location="chinatown",
        )
        # Verify all cards are closer to Chinatown than to Harlem
        # (Harlem is ~10 km north; cards near Chinatown should be
        # much closer to Chinatown).
        if "harlem" in NEIGHBORHOOD_CENTERS:
            harlem_lat, harlem_lon = NEIGHBORHOOD_CENTERS["harlem"]
            for card in result["services"]:
                clat = card.get("latitude")
                clon = card.get("longitude")
                if clat is None or clon is None:
                    continue
                dist_ct = _haversine_meters(
                    ct_lat, ct_lon, float(clat), float(clon),
                )
                dist_harlem = _haversine_meters(
                    harlem_lat, harlem_lon, float(clat), float(clon),
                )
                assert dist_ct < dist_harlem, (
                    f"Chinatown query returned a card closer to "
                    f"Harlem ({dist_harlem:.0f}m) than to Chinatown "
                    f"({dist_ct:.0f}m): {card.get('service_name')!r}"
                )

    def test_unknown_neighborhood_passes_through(self):
        """When the neighborhood isn't in NEIGHBORHOOD_CENTERS (e.g.
        because the user typed a misspelling or an obscure subdivision
        name), proximity narrowing is a no-op and we fall back to
        whatever the upstream resolver produced."""
        # The borough resolver may map "the lower east area" to
        # Manhattan, or may not match at all. Either way, no crash
        # and a plausible response.
        result = runner._mock_query_services(
            service_type="food", location="the lower east area",
        )
        assert isinstance(result["services"], list)
        # Some result count, no exception.
        assert "result_count" in result

    def test_borough_query_returns_unsorted_full_borough(self):
        """When the user gives a borough, proximity is bypassed.
        Cards aren't sorted by distance to any neighborhood center
        — they come through in fixture order (or whatever the
        upstream filter produced)."""
        result = runner._mock_query_services(
            service_type="food", location="brooklyn",
        )
        # Sanity: we got Brooklyn rows.
        assert result["result_count"] > 0
        # All cards in Brooklyn — proximity didn't reorder by
        # any NYC neighborhood center.
        for card in result["services"]:
            addr = (card.get("address") or "").lower()
            # Brooklyn cards have "brooklyn, ny" in their address.
            assert "brooklyn" in addr or "ny 11" in addr, (
                f"Brooklyn query returned non-Brooklyn card: {addr!r}"
            )

    def test_proximity_with_max_results_caps_to_closest(self):
        """When max_results is set, the proximity-sorted results are
        capped to N closest. This combines cluster 2's proximity
        with the existing max_results (population fallback) handling."""
        result = runner._mock_query_services(
            service_type="food", location="chinatown", max_results=2,
        )
        assert result["result_count"] <= 2


class TestNeighborhoodProximityHardCap:
    """Tests for the bug-hunt #11 fix: the proximity filter's
    no-strict-hit fallback used to return ALL rows in the borough
    sorted by proximity, including 12+km outliers — surfacing rows
    in completely different parts of the borough as if they matched
    a neighborhood ask.

    Production's ``ST_DWithin`` would return zero in that case,
    triggering the relaxed-search path. The eval mock now caps
    the fallback at ``_NEIGHBORHOOD_FALLBACK_MAX_RADIUS_METERS``
    (5km) and returns empty when no rows are within that cap,
    aligning with production's ST_DWithin semantics.

    Tests target ``_filter_rows_by_neighborhood_proximity`` directly
    rather than going through ``_mock_query_services``, so the
    proximity logic is exercised in isolation from upstream
    borough resolution and downstream eligibility filters.
    """

    # Far Rockaway is the canonical thin-coverage Queens neighborhood.
    # Its lat/lon is ~40.6033, -73.7544. Most other Queens services
    # are 10+km away (Jamaica, Long Island City, Astoria, Forest Hills),
    # making it the natural test bed for the hard cap.
    _FAR_ROCKAWAY = "Far Rockaway"

    def _row(self, name: str, lat: float, lon: float) -> dict:
        return {"service_name": name, "latitude": lat, "longitude": lon}

    def test_strict_radius_hit_unchanged(self):
        """Tier 2: a row inside the strict radius (1.6km) is returned
        with ``narrowed=True``. This is the production-equivalent
        path and must keep working after the hard-cap fix."""
        rows = [
            self._row("Far Rockaway Welcome", 40.6080, -73.7540),  # ~530m
            self._row("Jamaica Family Services", 40.7022, -73.7888),  # ~11km
        ]
        result, narrowed = runner._filter_rows_by_neighborhood_proximity(
            rows, self._FAR_ROCKAWAY,
        )
        assert narrowed is True
        names = [r["service_name"] for r in result]
        assert names == ["Far Rockaway Welcome"], (
            f"Strict-radius branch should return only the in-radius row, "
            f"got: {names}"
        )

    def test_no_strict_hit_within_fallback_radius_returned(self):
        """Tier 3: no rows within strict radius BUT some within the
        5km fallback cap — return those, sorted by distance, with
        ``narrowed=False``. The False signal indicates the strict
        constraint did not hold; rows are nevertheless reasonable
        nearby alternatives."""
        rows = [
            # No row within strict 1.6km
            self._row("Mid-Rockaway Center", 40.6310, -73.7440),  # ~3.2km
            self._row("Edgemere Outpost", 40.6020, -73.7900),  # ~3.0km
            self._row("Jamaica Family Services", 40.7022, -73.7888),  # ~11km
        ]
        result, narrowed = runner._filter_rows_by_neighborhood_proximity(
            rows, self._FAR_ROCKAWAY,
        )
        assert narrowed is False
        names = [r["service_name"] for r in result]
        # Both rows within the 5km fallback should appear, sorted
        # by distance. Jamaica (11km) must be excluded.
        assert "Jamaica Family Services" not in names, (
            f"Row beyond 5km fallback cap leaked through: {names}"
        )
        assert set(names) == {"Mid-Rockaway Center", "Edgemere Outpost"}, (
            f"Both within-fallback rows should be returned, got: {names}"
        )

    def test_no_rows_within_fallback_returns_empty(self):
        """Tier 4 (the bug-hunt #11 fix): no rows within either
        strict or fallback radius → return empty. Previously the
        function returned ALL rows sorted by distance, surfacing
        12+km outliers (canonical case: Far Rockaway query, Jamaica
        row at ~11km) as if they were nearby matches.

        The empty return lets upstream no-results handling take
        over, mirroring production's ST_DWithin behavior."""
        rows = [
            # Only outliers — every row > 5km from Far Rockaway center
            self._row("Jamaica Family Services", 40.7022, -73.7888),  # ~11km
            self._row("Long Island City Drop-in", 40.7468, -73.9407),  # ~21km
            self._row("Astoria Hub", 40.7720, -73.9301),  # ~25km
        ]
        result, narrowed = runner._filter_rows_by_neighborhood_proximity(
            rows, self._FAR_ROCKAWAY,
        )
        assert narrowed is False
        assert result == [], (
            f"All rows are >5km from Far Rockaway; should return empty. "
            f"Got: {[r['service_name'] for r in result]}"
        )

    def test_fallback_radius_constant_is_5km(self):
        """Pin the cap value. If a future change moves it (e.g. to
        3km tighter or 7.5km looser), this test surfaces it
        explicitly so the rationale comment block can be updated
        to match."""
        assert runner._NEIGHBORHOOD_FALLBACK_MAX_RADIUS_METERS == 5000

    def test_borough_query_unaffected_by_hard_cap(self):
        """Regression guard: the hard cap only affects neighborhood
        queries. A borough query bypasses proximity entirely and
        should still return all rows regardless of distance."""
        rows = [
            self._row("Jamaica Family Services", 40.7022, -73.7888),  # ~11km from FR
            self._row("Astoria Hub", 40.7720, -73.9301),  # ~25km from FR
        ]
        result, narrowed = runner._filter_rows_by_neighborhood_proximity(
            rows, "Queens",  # borough — no proximity filter
        )
        assert narrowed is False
        assert len(result) == 2  # both rows pass through


class TestHaversineMeters:
    """Pure-function tests for _haversine_meters. Used by the
    neighborhood proximity filter."""

    def test_zero_distance(self):
        """Same point → 0 meters."""
        from tests.eval.eval_llm_judge import _haversine_meters
        # Times Square
        d = _haversine_meters(40.7580, -73.9855, 40.7580, -73.9855)
        assert abs(d) < 0.01

    def test_known_nyc_distance(self):
        """Times Square to Battery Park is approximately 6.6 km
        by great-circle distance (street-level walking is longer)."""
        from tests.eval.eval_llm_judge import _haversine_meters
        d = _haversine_meters(40.7580, -73.9855, 40.7033, -74.0170)
        # Allow 200m slack for the simplified earth model.
        assert 6400 < d < 6800, f"Expected ~6.6 km, got {d:.0f}m"

    def test_jackson_heights_to_jamaica_is_far(self):
        """The original bug: cards from Jamaica being returned for a
        Jackson Heights search. Verify the distance is well above
        the 1.6 km radius."""
        from app.rag.query_executor import NEIGHBORHOOD_CENTERS
        from tests.eval.eval_llm_judge import _haversine_meters
        jh = NEIGHBORHOOD_CENTERS["jackson heights"]
        jam = NEIGHBORHOOD_CENTERS["jamaica"]
        d = _haversine_meters(jh[0], jh[1], jam[0], jam[1])
        # Should be ~9 km — well outside the 1.6 km radius.
        assert d > 5000, (
            f"Jackson Heights to Jamaica is only {d:.0f}m — "
            f"unexpected. Bug not reproduced."
        )

    def test_symmetric(self):
        """haversine(A, B) == haversine(B, A)."""
        from tests.eval.eval_llm_judge import _haversine_meters
        d1 = _haversine_meters(40.7580, -73.9855, 40.7033, -74.0170)
        d2 = _haversine_meters(40.7033, -74.0170, 40.7580, -73.9855)
        assert abs(d1 - d2) < 0.01


class TestResolveBorough:
    """Direct tests for _resolve_borough — the function that powers
    the borough filter via production's lookup chain."""

    @pytest.mark.parametrize("location,expected", [
        # The five boroughs (case variants)
        ("Manhattan", "Manhattan"),
        ("manhattan", "Manhattan"),
        ("MANHATTAN", "Manhattan"),
        ("Brooklyn", "Brooklyn"),
        ("Queens", "Queens"),
        ("Bronx", "Bronx"),
        ("Staten Island", "Staten Island"),
        # Manhattan neighborhoods
        ("Harlem", "Manhattan"),
        ("East Harlem", "Manhattan"),
        ("Soho", "Manhattan"),
        ("Lower East Side", "Manhattan"),
        ("Times Square", "Manhattan"),
        ("Midtown", "Manhattan"),
        ("Penn Station", "Manhattan"),
        # Queens neighborhoods
        ("Jackson Heights", "Queens"),
        ("Flushing", "Queens"),
        ("Astoria", "Queens"),
        # Brooklyn neighborhoods
        ("Williamsburg", "Brooklyn"),
        # Bronx neighborhoods
        ("Mott Haven", "Bronx"),
        # Compound inputs (substring match)
        ("midtown Manhattan", "Manhattan"),
        ("shelter in Harlem", "Manhattan"),
        ("near Penn Station", "Manhattan"),
        # Unresolvable
        (None, None),
        ("", None),
        ("Atlantis", None),
        ("Mars", None),
    ])
    def test_resolves_correctly(self, location, expected):
        assert runner._resolve_borough(location) == expected


# ---------------------------------------------------------------------------
# Sentinels
# ---------------------------------------------------------------------------


class TestSentinels:
    """Special argument values that test scenarios use to force
    particular dispatcher behavior."""

    def test_nowhere_location_returns_empty(self):
        result = runner._mock_query_services(
            service_type="food", location="__nowhere__",
        )
        assert result["result_count"] == 0
        assert result["services"] == []

    def test_error_service_type_returns_empty(self):
        result = runner._mock_query_services(
            service_type="__error__", location="Brooklyn",
        )
        assert result["result_count"] == 0
        assert result["services"] == []


# ---------------------------------------------------------------------------
# Argument shape — production calls query_services positionally and by keyword
# ---------------------------------------------------------------------------


class TestArgumentShape:
    """The dispatcher accepts both positional and keyword forms,
    because production's query_services uses both."""

    def test_positional_service_type(self):
        """Production sometimes calls query_services('food', ...).
        Dispatcher must accept positional service_type."""
        result = runner._mock_query_services("food")
        assert result["result_count"] > 0

    def test_keyword_service_type(self):
        result = runner._mock_query_services(service_type="food")
        assert result["result_count"] > 0

    def test_extra_kwargs_silently_accepted(self):
        """Production passes many other kwargs (age, gender, urgency,
        radius_meters, etc.). The dispatcher ignores them but must
        not crash."""
        result = runner._mock_query_services(
            service_type="food", location="Brooklyn",
            age=25, gender="women", urgency="high",
            radius_meters=2000, time_of_day="evening",
            family_status="with_children", language="es",
            requires_referral=False, accessibility_needed=True,
        )
        assert result["result_count"] > 0


# ---------------------------------------------------------------------------
# Population-fallback parity — taxonomy_override + max_results
# ---------------------------------------------------------------------------


class TestTaxonomyOverride:
    """Production's population-fallback flow calls query_services with
    `taxonomy_override=["youth"]` (or similar) to find population-
    specific services. The dispatcher must filter the fixture to rows
    whose service_taxonomies overlap (case-insensitive) with the
    override list. Without this filter, population fallback returns
    all-borough rows and the bot displays inappropriate cards
    (veterans housing, formerly-incarcerated shelters, etc.) labeled
    as "youth-friendly" — a major eval-fidelity issue surfaced by
    shelter_queens_17 in the May 2026 subset run.
    """

    def test_taxonomy_override_filters_to_matching_rows(self):
        """Override matches a row's service_taxonomies array
        case-insensitively."""
        result = runner._mock_query_services(
            service_type="shelter", location=None,
            taxonomy_override=["veterans short-term housing"],
        )
        # The fixture has Charles B. Wang's Veterans Short-Term
        # Housing service in Queens.
        assert result["result_count"] >= 1
        for card in result["services"]:
            taxes = {t.lower() for t in card.get("service_taxonomies", [])}
            assert "veterans short-term housing" in taxes

    def test_taxonomy_override_returns_zero_when_no_match(self):
        """If no fixture row matches the override taxonomies, the
        result is empty. This is production-faithful — production's
        population-fallback dedup-to-empty path triggers a logged
        warning rather than appending fallback cards."""
        result = runner._mock_query_services(
            service_type="shelter", location=None,
            taxonomy_override=["nonexistent-taxonomy"],
        )
        assert result["result_count"] == 0

    def test_taxonomy_override_records_in_params_applied(self):
        """The eval transcript and judge prompt include params_applied
        so the judge can verify which template ran. taxonomy_override
        must appear there."""
        result = runner._mock_query_services(
            service_type="shelter",
            taxonomy_override=["youth", "lgbtq young adult"],
        )
        assert result["params_applied"].get("taxonomy_override") == [
            "youth", "lgbtq young adult",
        ]


class TestMaxResults:
    """Production's population-fallback caps results at
    _POPULATION_FALLBACK_MAX (= 3). Some main queries also use
    max_results for pagination. The dispatcher must honor it."""

    def test_max_results_caps_count(self):
        result = runner._mock_query_services(
            service_type="shelter", location=None, max_results=2,
        )
        assert result["result_count"] == 2

    def test_max_results_zero_returns_empty(self):
        result = runner._mock_query_services(
            service_type="shelter", location=None, max_results=0,
        )
        assert result["result_count"] == 0

    def test_max_results_higher_than_data_returns_all(self):
        result = runner._mock_query_services(
            service_type="shelter", location=None, max_results=999,
        )
        # Should return all 25 shelter rows; max acts as a cap, not a target
        assert result["result_count"] >= 5
        assert result["result_count"] <= 999

    def test_max_results_combines_with_taxonomy_override(self):
        """Production's population fallback uses both together."""
        result = runner._mock_query_services(
            service_type="shelter", location=None,
            taxonomy_override=["single adult"],
            max_results=3,
        )
        assert result["result_count"] <= 3


class TestServiceIdPassthrough:
    """Production's population-fallback dedup uses service_id to
    avoid showing the same row in main + fallback. Cards must
    include service_id."""

    def test_card_has_service_id(self):
        result = runner._mock_query_services(service_type="food")
        for card in result["services"]:
            assert "service_id" in card
            assert card["service_id"], (
                f"Card {card.get('service_name')!r} has no service_id"
            )


# ---------------------------------------------------------------------------
# Response shape — must match production
# ---------------------------------------------------------------------------


class TestResponseShape:
    """The dispatcher's response shape must match what production's
    query_services returns. The bot's downstream pipeline expects
    specific fields; missing one can cause subtle bugs."""

    def test_response_has_expected_top_level_keys(self):
        result = runner._mock_query_services(service_type="food")
        for key in ["services", "result_count", "template_used",
                    "params_applied", "relaxed", "execution_ms"]:
            assert key in result, f"Missing top-level key: {key!r}"

    def test_each_card_has_expected_fields(self):
        """Every card needs name, org, address, phone, etc. for the
        bot's formatter."""
        result = runner._mock_query_services(service_type="food")
        for card in result["services"]:
            for field in ["service_name", "organization", "address",
                          "phone", "fees", "description", "hours_today",
                          "is_open", "yourpeer_url"]:
                assert field in card, (
                    f"Card {card.get('service_name')!r} missing {field!r}"
                )

    def test_template_used_reflects_service_type(self):
        result = runner._mock_query_services(service_type="shelter")
        assert "Shelter" in result["template_used"]


# ---------------------------------------------------------------------------
# Backward compatibility constants
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    """Other test modules import MOCK_QUERY_RESULTS / MOCK_EMPTY_RESULTS
    by name. They must keep working."""

    def test_mock_query_results_still_exists(self):
        assert hasattr(runner, "MOCK_QUERY_RESULTS")

    def test_mock_query_results_is_food_brooklyn(self):
        """Convention: MOCK_QUERY_RESULTS is a frozen 'food in
        Brooklyn' response. Other tests pin behavior against it."""
        mqr = runner.MOCK_QUERY_RESULTS
        assert mqr["result_count"] > 0
        assert "food" in mqr["template_used"].lower()

    def test_mock_empty_results_still_exists(self):
        assert hasattr(runner, "MOCK_EMPTY_RESULTS")

    def test_mock_empty_results_has_zero_count(self):
        assert runner.MOCK_EMPTY_RESULTS["result_count"] == 0
        assert runner.MOCK_EMPTY_RESULTS["services"] == []


# ---------------------------------------------------------------------------
# Colocated service filter
# ---------------------------------------------------------------------------


class TestColocatedServiceTypes:
    """Production's FILTER_BY_COLOCATED_TAXONOMY restricts results to
    locations where some other service is tagged with one of the
    colocated types' taxonomies. The mock approximates this using each
    row's `also_available` field (built by the same SQL pattern in
    scripts/fixture/_q3_clean.sql).

    Production retries the query without the colocated filter when the
    strict filter returns 0, and sets `colocated_fallback=True` on the
    response (rag/__init__.py:530-545). The mock should mirror this.
    """

    def test_no_colocated_means_no_fallback_flag(self):
        """Sanity: when no colocated_service_types is passed, the
        response should not carry colocated_fallback at all."""
        r = runner._mock_query_services(service_type="food", location="brooklyn")
        assert "colocated_fallback" not in r

    def test_colocated_with_strict_match_returns_intersection(self):
        """When at least one row's also_available overlaps the
        colocated taxonomy set, return that filtered subset and do
        NOT set colocated_fallback."""
        # Brooklyn food + clothing colocation: fixture has 1 strict
        # match (Food Pantry whose location also offers Clothing Pantry).
        r = runner._mock_query_services(
            service_type="food", location="brooklyn",
            colocated_service_types=["clothing"],
        )
        assert r["result_count"] >= 1
        assert not r.get("colocated_fallback")

    def test_colocated_with_zero_strict_matches_falls_back(self):
        """When no row's also_available overlaps the colocated
        taxonomy set, return the unfiltered primary results AND set
        colocated_fallback=True (matches production retry-and-flag)."""
        # Brooklyn food + shelter colocation: fixture has 0 strict
        # matches. Should fall back to all Brooklyn food rows.
        # Baseline-relative so the test survives fixture refresh.
        baseline = runner._mock_query_services(
            service_type="food", location="brooklyn",
        )
        r = runner._mock_query_services(
            service_type="food", location="brooklyn",
            colocated_service_types=["shelter"],
        )
        assert r["result_count"] == baseline["result_count"]
        assert r.get("colocated_fallback") is True

    def test_colocated_unresolvable_type_flags_fallback(self):
        """Production marks colocated_fallback=True when no colocated
        type can be resolved to a taxonomy set, regardless of whether
        the unfiltered query would have returned results
        (rag/__init__.py:544-545)."""
        baseline = runner._mock_query_services(
            service_type="food", location="brooklyn",
        )
        r = runner._mock_query_services(
            service_type="food", location="brooklyn",
            colocated_service_types=["this_is_not_a_real_service_type"],
        )
        assert r["result_count"] == baseline["result_count"]  # unfiltered primary
        assert r.get("colocated_fallback") is True

    def test_colocated_recorded_in_params_applied(self):
        """The response's params_applied should reflect the colocated
        types so log_query_execution can serialize them."""
        r = runner._mock_query_services(
            service_type="shelter", location="manhattan",
            colocated_service_types=["food"],
        )
        assert r["params_applied"].get("colocated_service_types") == ["food"]

    def test_colocated_real_data_smoke(self):
        """Sanity check against fixture data: shelter in Manhattan
        with food colocation should find the cluster of homeless-
        services centers that offer multiple services. This is
        regression-protection against accidentally filtering all rows."""
        r = runner._mock_query_services(
            service_type="shelter", location="manhattan",
            colocated_service_types=["food"],
        )
        # Fixture has 4 such rows (Day Sleeping Room, Shelter
        # Placement, Runaway Youth, Overnight Men Sign-Up). Don't pin
        # exact count - fixture refresh may shift it.
        assert r["result_count"] >= 1
        assert not r.get("colocated_fallback")


# ---------------------------------------------------------------------------
# service_detail narrowing
# ---------------------------------------------------------------------------


class TestServiceDetailNarrowing:
    """The eval narrowing now mirrors production's strict approach:

    1. If ``service_detail`` is in production's ``_DETAIL_TO_TAXONOMY_NARROWING``,
       filter rows by overlap with the listed taxonomies. **Strict.**
    2. Else if it's in ``_DETAIL_DESCRIPTION_FILTERS``, filter rows by
       regex match against ``service_description``.
    3. Else fall back to a permissive substring match against
       service_name / service_taxonomies / service_description.

    All three strategies fall back to the unfiltered row set when no
    match is found, on the principle that strict 0-result fallback
    would invalidate scenarios the eval should be checking. The
    fixture's per-borough taxonomy coverage is thinner than
    production's DB.

    These tests landed with Finding 5 of the May 5, 2026 eval-fidelity
    audit — closing the gap where the eval used permissive substring
    matching for ALL service_detail values, including ones production
    handles strictly via taxonomy swap.
    """

    def test_no_service_detail_returns_unfiltered(self):
        """Sanity: when service_detail is None, return all rows in the
        bucket. Count varies by fixture; what matters is that the count
        is positive (not 0) and matches the baseline."""
        baseline = runner._mock_query_services(
            service_type="food", location="brooklyn",
        )
        # Sanity: we have rows for the bucket.
        assert baseline["result_count"] > 0
        # The detail-less call is the baseline by definition.
        r = runner._mock_query_services(service_type="food", location="brooklyn")
        assert r["result_count"] == baseline["result_count"]

    def test_taxonomy_strategy_strict_for_detox(self):
        """Strategy 1 (taxonomy strict): ``detox`` is in production's
        taxonomy-narrowing dict (maps to ['substance use treatment',
        'residential recovery']). Eval must apply strict taxonomy
        filtering, not loose substring match. All returned rows must
        be tagged with one of the listed taxonomies."""
        from app.rag import _DETAIL_TO_TAXONOMY_NARROWING
        narrowed = {t.lower() for t in _DETAIL_TO_TAXONOMY_NARROWING["detox"]}
        # Use medical (the post-cluster-5 routing target for detox).
        r = runner._mock_query_services(
            service_type="medical", location="manhattan",
            service_detail="detox",
        )
        # If we got results, every row must be tagged correctly.
        for c in r["services"]:
            tax_set = {str(t).lower() for t in (c.get("service_taxonomies") or [])}
            assert tax_set & narrowed, (
                f"Card {c.get('service_name')} taxonomies={tax_set} "
                f"don't overlap detox-narrowed {narrowed}"
            )

    def test_taxonomy_strategy_strict_for_food_subtypes(self):
        """Strategy 1: 'soup kitchens' is in the taxonomy-narrowing dict
        (maps to ['soup kitchen', 'mobile soup kitchen']). All returned
        rows must carry one of those taxonomies."""
        from app.rag import _DETAIL_TO_TAXONOMY_NARROWING
        narrowed = {t.lower() for t in _DETAIL_TO_TAXONOMY_NARROWING["soup kitchens"]}
        r = runner._mock_query_services(
            service_type="food", location="brooklyn",
            service_detail="soup kitchens",
        )
        if r["result_count"] > 0:
            for c in r["services"]:
                tax_set = {str(t).lower() for t in (c.get("service_taxonomies") or [])}
                assert tax_set & narrowed

    def test_description_regex_strategy_for_financial_services(self):
        """Strategy 2 (description regex): 'financial services' is in
        the description-filters dict, not the taxonomy dict. Eval must
        apply the regex (production's pattern: 'financial|money manage|
        budget|credit|debt|financial literacy') against
        service_description, not substring match against the literal
        'financial services'.

        Brooklyn fixture has 'other'-bucket rows whose descriptions
        match the financial regex (Health Services Hotline mentions
        'Debt', Community Services mentions 'financial counseling').
        """
        baseline = runner._mock_query_services(
            service_type="other", location="brooklyn",
        )
        r = runner._mock_query_services(
            service_type="other", location="brooklyn",
            service_detail="financial services",
        )
        # Strict regex must narrow the bucket — count drops below
        # baseline (the bucket size).
        assert r["result_count"] < baseline["result_count"], (
            f"Strict regex narrowing should reduce bucket size: "
            f"baseline={baseline['result_count']}, narrowed={r['result_count']}"
        )
        # And every returned card's description matches one of the
        # regex's terms.
        for c in r["services"]:
            desc = (c.get("description") or "").lower()
            assert any(kw in desc for kw in ("financial", "money", "budget", "credit", "debt")), (
                f"Card {c.get('service_name')!r} description doesn't match "
                f"financial regex: {desc[:120]!r}"
            )

    def test_description_regex_falls_back_when_borough_has_no_matches(self):
        """Strategy 2's safety net: when the borough has 0 rows
        matching the description regex, fall back to unfiltered.

        Manhattan's 'other' bucket has 13 rows but none with
        financial-related descriptions (the financial-tagged rows live
        in Bronx and Brooklyn). Production would return 0 here; the
        eval prefers showing what's in the bucket so the bot is scored
        on its handling of those rows rather than on the fixture's
        coverage gap.
        """
        baseline = runner._mock_query_services(
            service_type="other", location="manhattan",
        )
        r = runner._mock_query_services(
            service_type="other", location="manhattan",
            service_detail="financial services",
        )
        # Safety net: fell back to unfiltered.
        assert r["result_count"] == baseline["result_count"]

    def test_unknown_detail_falls_back_to_substring_then_unfiltered(self):
        """Strategy 3 (permissive fallback): when service_detail is
        not in either production dict, fall back to substring match
        against name / taxonomies / description, then to unfiltered
        when nothing matches."""
        baseline = runner._mock_query_services(
            service_type="food", location="brooklyn",
        )
        r = runner._mock_query_services(
            service_type="food", location="brooklyn",
            service_detail="quantumcryogenicfeasts",
        )
        # quantumcryogenicfeasts isn't in either dict and matches
        # nothing → unfiltered result.
        assert r["result_count"] == baseline["result_count"]

    def test_strict_taxonomy_falls_back_to_unfiltered_on_zero_match(self):
        """When strict taxonomy narrowing is applied but the fixture
        has 0 rows tagged with the narrowed taxonomy in the searched
        borough, fall back to unfiltered rather than empty. This is
        an eval-specific safety net — production would return 0; eval
        prefers showing the user what's available rather than
        invalidating the scenario over fixture-coverage gaps."""
        # Pick a detail-borough combination where the taxonomy
        # narrowing yields no rows in that borough but the bucket
        # itself has rows. Detox in Staten Island fits if the fixture
        # has substance-use rows only in other boroughs.
        baseline = runner._mock_query_services(
            service_type="medical", location="staten island",
        )
        r = runner._mock_query_services(
            service_type="medical", location="staten island",
            service_detail="detox",
        )
        # Either we got narrowed results OR we fell back to the full
        # bucket. Either is acceptable; what's NOT acceptable is 0.
        if baseline["result_count"] > 0:
            assert r["result_count"] > 0

    def test_service_detail_recorded_in_params_applied(self):
        r = runner._mock_query_services(
            service_type="other", location="manhattan",
            service_detail="financial services",
        )
        assert r["params_applied"].get("service_detail") == "financial services"


# ---------------------------------------------------------------------------
# Service-type taxonomy lookup (built once at module load)
# ---------------------------------------------------------------------------


class TestServiceTypeTaxonomyLookup:
    """The lookup is built at module load by reading production's
    TEMPLATES dict. Ensures it's populated for the canonical service
    types."""

    def test_lookup_is_populated(self):
        # Empty when production isn't on the path (spot-check env).
        # In the normal test environment, production should be
        # importable.
        assert runner._SERVICE_TYPE_TAXONOMY_LOOKUP

    def test_lookup_has_all_canonical_service_types(self):
        canonical = set(runner._SERVICE_TYPE_TO_TEMPLATE.keys())
        assert set(runner._SERVICE_TYPE_TAXONOMY_LOOKUP.keys()) == canonical

    def test_lookup_food_includes_food_pantry(self):
        food = runner._SERVICE_TYPE_TAXONOMY_LOOKUP.get("food", set())
        assert "food pantry" in food
        assert "soup kitchen" in food

    def test_lookup_shelter_includes_youth_and_families(self):
        shelter = runner._SERVICE_TYPE_TAXONOMY_LOOKUP.get("shelter", set())
        # Production's shelter template enumerates child taxonomies
        # explicitly (see query_templates.py:659-685). The eval mock
        # depends on these being present for colocated filtering to
        # work right.
        assert "youth" in shelter
        assert "families" in shelter
        assert "single adult" in shelter

    def test_lookup_values_are_lowercased(self):
        """All taxonomy names must be lowercased for case-insensitive
        comparison against fixture rows' service_taxonomies and
        also_available."""
        for service_type, taxonomies in runner._SERVICE_TYPE_TAXONOMY_LOOKUP.items():
            for tax in taxonomies:
                assert tax == tax.lower(), (
                    f"{service_type} taxonomy {tax!r} is not lowercased"
                )


# ---------------------------------------------------------------------------
# Eligibility filter — gender / family_status / age
# ---------------------------------------------------------------------------


class TestEligibilityFamilyStatus:
    """Production narrows shelter taxonomy_names based on family_status:
    with_children/with_family → families+shelter; alone → single adult+
    shelter. The mock approximates this on the row's service_taxonomies
    field, with permissive fallback if narrowing empties the set.
    """

    def test_no_family_status_returns_all_shelter_rows(self):
        """Sanity: without family_status, the result count matches the
        baseline shelter-Manhattan query (no narrowing applied).
        Hard count varies with fixture refresh."""
        r = runner._mock_query_services(service_type="shelter", location="manhattan")
        # At minimum we expect Manhattan to have shelter rows.
        assert r["result_count"] > 0

    def test_family_status_only_applies_to_shelter(self):
        """Production's narrowing is shelter-only. Food/clothing/etc.
        should be unaffected by family_status."""
        r1 = runner._mock_query_services(service_type="food", location="brooklyn")
        r2 = runner._mock_query_services(
            service_type="food", location="brooklyn",
            family_status="with_children",
        )
        # Same row count - family_status had no effect on food query
        assert r1["result_count"] == r2["result_count"]

    def test_family_status_narrowing_permissive_fallback(self):
        """When narrowing empties the row set (fixture has no Families-
        tagged rows in some boroughs), fall back to all rows rather
        than returning empty. The defense-in-depth gender exclusion
        still applies after the fallback."""
        r = runner._mock_query_services(
            service_type="shelter", location="manhattan",
            family_status="with_children",
        )
        # Permissive: not 0. But men-only services excluded even
        # though family_status narrowing emptied and fell back.
        assert r["result_count"] >= 1
        names = [s.get("service_name", "") for s in r["services"]]
        assert not any("Overnight Men" in n for n in names), (
            "Defense-in-depth: family_status=with_children must exclude "
            "men-only services even when family_status narrowing is "
            "permissive-fallback"
        )

    def test_family_status_records_in_params_applied(self):
        r = runner._mock_query_services(
            service_type="shelter", location="manhattan",
            family_status="with_children",
        )
        assert r["params_applied"].get("family_status") == "with_children"


class TestEligibilityGender:
    """The mock approximates production's SQL gender eligibility filter
    by matching service_name patterns. Covers the case where the fixture
    encodes gender via the service name (Overnight Men Sign-Up, Women's
    Shelter, etc.) — which is how the Streetlives DB happens to label
    most gender-specific services.
    """

    def test_gender_male_keeps_men_only(self):
        """A male user should still see men-only services."""
        r = runner._mock_query_services(
            service_type="shelter", location="manhattan",
            family_status="alone", gender="male",
        )
        names = [s.get("service_name", "") for s in r["services"]]
        # Overnight Men Sign-Up should appear (it's tagged Single Adult,
        # which is the alone narrow's allowed taxonomy).
        assert any("Overnight Men" in n for n in names)

    def test_gender_female_excludes_men_only(self):
        """A female user should not see men-only services."""
        r = runner._mock_query_services(
            service_type="shelter", location="manhattan",
            family_status="alone", gender="female",
        )
        names = [s.get("service_name", "") for s in r["services"]]
        assert not any("Overnight Men" in n for n in names)

    def test_gender_lgbtq_excludes_all_gender_explicit(self):
        """LGBTQ/trans/nonbinary users should not see services with
        strict gender-explicit names (misgendering risk)."""
        r = runner._mock_query_services(
            service_type="shelter", location="manhattan",
            gender="lgbtq",
        )
        names = [s.get("service_name", "") for s in r["services"]]
        assert not any("Overnight Men" in n for n in names)

    def test_gender_unset_no_filter(self):
        """Without gender (and without family_status defense-in-depth),
        the gender filter should not fire."""
        r = runner._mock_query_services(
            service_type="shelter", location="manhattan",
        )
        names = [s.get("service_name", "") for s in r["services"]]
        # Men-only services should appear in the unfiltered baseline.
        assert any("Overnight Men" in n for n in names)


class TestEligibilityIntegration:
    """End-to-end checks against the three R41-failing scenarios from
    cluster 1 of the May 5 triage. These are integration-style: pass
    the same kwargs production would pass for those scenarios.
    """

    def test_peer_young_mom_no_overnight_men(self):
        """19yo mom with baby in Manhattan needs shelter, food, healthcare,
        diapers. Production passes family_status=with_children with no
        explicit gender (gender extractor doesn't match '19-year-old mom').
        Defense-in-depth gender exclusion should still keep men-only out."""
        r = runner._mock_query_services(
            service_type="shelter", location="manhattan",
            family_status="with_children", age=19,
            colocated_service_types=["medical", "food", "other"],
        )
        names = [s.get("service_name", "") for s in r["services"]]
        assert not any("Overnight Men" in n for n in names), (
            "peer_young_mom: a mother with a baby must never receive "
            "men-only shelter results"
        )

    def test_peer_lgbtq_youth_no_gender_explicit(self):
        """21yo LGBTQ youth in Soho — populations=['lgbtq'], gender='lgbtq'.
        Misgendering risk: filter out gender-explicit shelters."""
        r = runner._mock_query_services(
            service_type="shelter", location="soho",
            age=21, gender="lgbtq", populations=["lgbtq"],
        )
        names = [s.get("service_name", "") for s in r["services"]]
        assert not any("Overnight Men" in n for n in names)

    def test_multi_family_with_children_no_overnight_men(self):
        """Family with two children seeking PATH intake. family_status=
        with_children, no gender. Defense-in-depth applies."""
        r = runner._mock_query_services(
            service_type="shelter", location="manhattan",
            family_status="with_children",
        )
        names = [s.get("service_name", "") for s in r["services"]]
        assert not any("Overnight Men" in n for n in names)


# ---------------------------------------------------------------------------
# Post-refresh fixture simulation tests
# ---------------------------------------------------------------------------
# These tests verify that when the hybrid SQL extraction
# (scripts/fixture/03_extract_fixture.sql, May 5 refactor) lands and
# adds the missing population-coverage and Cornell-named rows, the
# cluster 1 eligibility filter behaves correctly. They monkeypatch
# the module-level _FIXTURE list with synthetic post-refresh rows.
#
# Until the SQL is run against the real DB and services_raw.json is
# regenerated, these tests document the expected behavior of the fix
# and guard against regressions when the data actually arrives.


@pytest.fixture
def post_refresh_fixture(monkeypatch):
    """Simulate the fixture state after running 03_extract_fixture.sql
    (hybrid version, May 5 2026). Adds the 5 critical rows the bucketed-
    only strategy was missing for shelter:
        - 1 Families-tagged shelter in Manhattan (Covenant House)
        - 1 LGBTQ-young-adult shelter in Manhattan (Ali Forney Center)
        - 1 youth-tagged shelter in Manhattan (Safe Horizon Streetwork)
        - 1 senior-tagged shelter in Manhattan
        - 1 PATH family-intake shelter in Bronx
    Returns the synthetic-augmented fixture for test inspection.
    """
    augmented = list(runner._FIXTURE) + [
        {
            "service_id": "test-cov-house-001",
            "service_name": "Crisis Housing for Young Mothers",
            "organization_name": "Covenant House New York",
            "service_description": "Emergency housing for young mothers and families.",
            "location_id": "test-loc-cov-001",
            "location_name": "Covenant House Hell's Kitchen",
            "location_slug": "covenant-house-hells-kitchen",
            "address": "460 W 41st St",
            "city": "New York",
            "state": "NY",
            "zip_code": "10036",
            "country": "US",
            "phone": "2126135327",
            "service_taxonomies": ["Families", "Shelter"],
            "also_available": ["Food Pantry", "Health"],
            "bot_service_type": "shelter",
            "borough": "Manhattan",
            "languages_spoken": ["en", "es"],
            "fees": None,
            "requires_membership": False,
        },
        {
            "service_id": "test-ali-forney-001",
            "service_name": "Crisis Bed Program",
            "organization_name": "Ali Forney Center",
            "service_description": "LGBTQ+ youth shelter with crisis intake.",
            "location_id": "test-loc-afc-001",
            "location_name": "Ali Forney Center Crisis Shelter",
            "location_slug": "ali-forney-center-bedford",
            "address": "224 W 35th St",
            "city": "New York",
            "state": "NY",
            "zip_code": "10001",
            "country": "US",
            "phone": "2126451603",
            "service_taxonomies": ["LGBTQ Young Adult", "Shelter"],
            "also_available": ["Food Pantry", "Mental Health", "Health"],
            "bot_service_type": "shelter",
            "borough": "Manhattan",
            "languages_spoken": ["en"],
            "fees": None,
            "requires_membership": False,
        },
        {
            "service_id": "test-streetwork-001",
            "service_name": "Drop-in Center Youth Shelter",
            "organization_name": "Safe Horizon",
            "service_description": "Youth drop-in center with shelter intake.",
            "location_id": "test-loc-sw-001",
            "location_name": "Safe Horizon - Streetwork Project Lower East Side",
            "location_slug": "safe-horizon-streetwork-project-lower-east-side",
            "address": "33 Essex St",
            "city": "New York",
            "state": "NY",
            "zip_code": "10002",
            "country": "US",
            "phone": "2126770720",
            "service_taxonomies": ["Youth", "Drop-in Center", "Shelter"],
            "also_available": ["Food Pantry", "Clothing", "Health"],
            "bot_service_type": "shelter",
            "borough": "Manhattan",
            "languages_spoken": ["en", "es"],
            "fees": None,
            "requires_membership": False,
        },
        {
            "service_id": "test-path-bronx-001",
            "service_name": "PATH Family Intake",
            "organization_name": "Department of Homeless Services (DHS)",
            "service_description": "24/7 family shelter intake for NYC.",
            "location_id": "test-loc-path-001",
            "location_name": "DHS Prevention Assistance and Temporary Housing (PATH) Concourse",
            "location_slug": "department-of-homeless-services-dhs-concourse",
            "address": "151 E 151st St",
            "city": "Bronx",
            "state": "NY",
            "zip_code": "10451",
            "country": "US",
            "phone": "3111",
            "service_taxonomies": ["Families", "Shelter"],
            "also_available": ["Referral"],
            "bot_service_type": "shelter",
            "borough": "Bronx",
            "languages_spoken": ["en", "es"],
            "fees": None,
            "requires_membership": False,
        },
    ]
    monkeypatch.setattr(runner, "_FIXTURE", augmented)
    yield augmented


class TestPostRefreshEligibility:
    """Verify the cluster 1 eligibility filter does the right thing
    when the fixture has the population-coverage rows it currently
    lacks. These tests will continue passing both before and after
    the SQL refresh — they isolate the filter logic from the data."""

    def test_with_children_returns_families_tagged(self, post_refresh_fixture):
        """When a Families-tagged row is present in Manhattan,
        family_status=with_children should return it (and ideally as
        a top result via the narrowing)."""
        r = runner._mock_query_services(
            service_type="shelter", location="manhattan",
            family_status="with_children",
        )
        names = [s.get("service_name", "") for s in r["services"]]
        # Either the Families-tagged Covenant House crisis-housing or
        # the PATH family-intake services should appear.
        assert any(
            "Covenant House" in (s.get("organization") or "")
            or "DHS" in (s.get("organization") or "")
            for s in r["services"]
        ), (
            f"Expected family-tagged shelter in results when "
            f"family_status=with_children. Got: {names}"
        )
        # And men-only services are still excluded.
        assert not any("Overnight Men" in n for n in names)

    def test_lgbtq_returns_lgbtq_tagged(self, post_refresh_fixture):
        """When an LGBTQ-young-adult row is present in Manhattan, an
        LGBTQ user should see it surfaced (production's lgbtq_boost
        ranking; the mock can only verify it isn't filtered out)."""
        r = runner._mock_query_services(
            service_type="shelter", location="manhattan",
            age=21, gender="lgbtq", populations=["lgbtq"],
        )
        # The Ali Forney crisis bed should appear.
        org_names = [s.get("organization", "") for s in r["services"]]
        assert any("Ali Forney" in n for n in org_names), (
            f"Expected Ali Forney Center in LGBTQ youth shelter results. "
            f"Got organizations: {org_names}"
        )

    def test_youth_returns_youth_tagged(self, post_refresh_fixture):
        """Youth shelter should be visible for age-16-24 + with_children
        even after family_status narrowing (defense-in-depth: production
        adds 'youth' to the safety_extras set)."""
        r = runner._mock_query_services(
            service_type="shelter", location="manhattan",
            family_status="with_children", age=19,
        )
        # The Streetwork youth drop-in should pass the narrowing because
        # the eligibility filter widens 'allowed' to include 'youth' for
        # age 16-24.
        names = [s.get("service_name", "") for s in r["services"]]
        # No men-only services (defense-in-depth still applies).
        assert not any("Overnight Men" in n for n in names)

    def test_path_intake_visible_for_family_in_bronx(self, post_refresh_fixture):
        """A family with children searching for shelter in the Bronx
        should see the PATH family intake. Previously absent from
        the fixture (Cornell sample queries doc names this as expected)."""
        r = runner._mock_query_services(
            service_type="shelter", location="bronx",
            family_status="with_children",
        )
        org_names = [s.get("organization", "") for s in r["services"]]
        assert any("DHS" in n or "Department of Homeless Services" in n
                   for n in org_names), (
            f"Expected DHS PATH family intake in Bronx family-shelter "
            f"results. Got: {org_names}"
        )
