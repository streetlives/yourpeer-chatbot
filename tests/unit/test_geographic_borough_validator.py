"""Tests for the geographic borough validator in query_executor.

Covers:
  - _stated_borough_from_city: pa.city → canonical borough inference
  - _annotate_geographic_borough: full per-card annotation with mismatch flag
  - Integration: mismatch detection catches the user-reported
    "Manhattan service actually in the Bronx" bug

The validator's job is to annotate, not filter. Cards flow through
unchanged in count/order; only the two new fields (geographic_borough,
borough_mismatch) are added.
"""

import logging

import pytest

from app.rag.query_executor import (
    _annotate_geographic_borough,
    _stated_borough_from_city,
    _build_city_to_borough,
)


# -------------------------------------------------------------------------
# _stated_borough_from_city
# -------------------------------------------------------------------------

class TestStatedBoroughInference:
    """pa.city values → canonical borough name."""

    @pytest.mark.parametrize("city,expected", [
        ("New York",       "Manhattan"),
        ("Brooklyn",       "Brooklyn"),
        ("Queens",         "Queens"),
        ("Bronx",          "Bronx"),
        ("Staten Island",  "Staten Island"),
    ])
    def test_primary_city_values(self, city, expected):
        """The 5 canonical pa.city values map correctly."""
        assert _stated_borough_from_city(city) == expected

    @pytest.mark.parametrize("city,expected", [
        ("new york",       "Manhattan"),
        ("NEW YORK",       "Manhattan"),
        ("  New York  ",   "Manhattan"),
        ("brooklyn",       "Brooklyn"),
        ("BRONX",          "Bronx"),
    ])
    def test_case_and_whitespace_insensitive(self, city, expected):
        """DB has casing inconsistencies; lookup is normalized."""
        assert _stated_borough_from_city(city) == expected

    @pytest.mark.parametrize("city,expected", [
        ("Astoria",         "Queens"),
        ("Long Island City","Queens"),
        ("Far Rockaway",    "Queens"),
        ("Flushing",        "Queens"),
        ("Williamsburg",    "Brooklyn"),
        ("Bushwick",        "Brooklyn"),
        ("Harlem",          "Manhattan"),
        ("Chelsea",         "Manhattan"),
        ("Mott Haven",      "Bronx"),
    ])
    def test_neighborhood_city_values(self, city, expected):
        """Neighborhood-level pa.city values also map via NYC_LOCATION_ALIASES."""
        assert _stated_borough_from_city(city) == expected

    @pytest.mark.parametrize("city", [
        None, "", "   ", "Jersey City", "Hoboken", "Boston",
        "Not A Real Place",
    ])
    def test_unknown_values_return_none(self, city):
        """Non-NYC / unknown / empty cities return None (not a guess)."""
        assert _stated_borough_from_city(city) is None


class TestCityToBoroughMap:
    """Sanity-check the derived map."""

    def test_covers_all_five_boroughs(self):
        """Every borough must have at least one city entry."""
        m = _build_city_to_borough()
        boroughs = set(m.values())
        assert boroughs == {"Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"}

    def test_all_values_are_lowercased_keys(self):
        """Keys are lowercased for case-insensitive matching."""
        m = _build_city_to_borough()
        for key in m:
            assert key == key.lower(), f"Key {key!r} not lowercased"

    def test_new_york_maps_to_manhattan(self):
        """The dominant pa.city value is 'New York' → Manhattan."""
        m = _build_city_to_borough()
        assert m["new york"] == "Manhattan"


# -------------------------------------------------------------------------
# _annotate_geographic_borough
# -------------------------------------------------------------------------

class TestAnnotation:
    """Cards get geographic_borough and borough_mismatch fields attached."""

    def test_clean_manhattan_card_no_mismatch(self):
        cards = [{
            "service_id": "1", "service_name": "Svc", "city": "New York",
            "latitude": 40.7484, "longitude": -73.9857,
        }]
        result = _annotate_geographic_borough(cards)
        assert result[0]["geographic_borough"] == "Manhattan"
        assert result[0]["borough_mismatch"] is False

    def test_reported_bug_manhattan_labeled_bronx_located(self, caplog):
        """The core scenario from docs/BOUNDARY_AUDIT.md: a service with
        pa.city='New York' whose coordinates are actually in the Bronx.

        Expected: borough_mismatch=True, both stated and geographic
        boroughs captured, WARNING logged.
        """
        cards = [{
            "service_id": "reported_bug",
            "service_name": "Example Pantry",
            "city": "New York",
            # Yankee Stadium — clearly in the Bronx
            "latitude": 40.8296, "longitude": -73.9262,
        }]
        with caplog.at_level(logging.WARNING, logger="app.rag.query_executor"):
            result = _annotate_geographic_borough(cards)

        assert result[0]["geographic_borough"] == "Bronx"
        assert result[0]["borough_mismatch"] is True
        # The warning should include both boroughs for triage
        log_messages = [r.message for r in caplog.records]
        assert any(
            "borough_mismatch" in m and "Bronx" in m and "Manhattan" in m
            for m in log_messages
        ), f"Expected a mismatch log mentioning both boroughs, got: {log_messages}"

    def test_brooklyn_clean(self):
        cards = [{
            "service_id": "2", "service_name": "Svc",
            "city": "Brooklyn",
            "latitude": 40.6826, "longitude": -73.9754,
        }]
        result = _annotate_geographic_borough(cards)
        assert result[0]["geographic_borough"] == "Brooklyn"
        assert result[0]["borough_mismatch"] is False

    def test_neighborhood_city_value_matches(self):
        """A row with pa.city='Astoria' and coords in Queens → no mismatch."""
        cards = [{
            "service_id": "3", "service_name": "Svc",
            "city": "Astoria",
            "latitude": 40.7723, "longitude": -73.9196,
        }]
        result = _annotate_geographic_borough(cards)
        assert result[0]["geographic_borough"] == "Queens"
        assert result[0]["borough_mismatch"] is False

    def test_missing_coordinates_no_validation(self):
        """Services without position data pass through without flagging."""
        cards = [{
            "service_id": "4", "service_name": "Svc",
            "city": "New York",
            "latitude": None, "longitude": None,
        }]
        result = _annotate_geographic_borough(cards)
        assert result[0]["geographic_borough"] is None
        assert result[0]["borough_mismatch"] is False

    def test_out_of_nyc_coords_no_mismatch(self):
        """A NJ hotline with NYC presence (geo=None) is not a mismatch."""
        cards = [{
            "service_id": "5", "service_name": "NJ Hotline",
            "city": "Jersey City",
            "latitude": 40.7178, "longitude": -74.0431,
        }]
        result = _annotate_geographic_borough(cards)
        assert result[0]["geographic_borough"] is None
        # Both None — no comparison possible, not a mismatch
        assert result[0]["borough_mismatch"] is False

    def test_unknown_stated_borough_no_mismatch(self):
        """Valid NYC coords + unknown pa.city — can't compare, no mismatch."""
        cards = [{
            "service_id": "6", "service_name": "Svc",
            "city": "Unmapped Neighborhood",  # not in NYC_LOCATION_ALIASES
            "latitude": 40.7484, "longitude": -73.9857,  # Manhattan
        }]
        result = _annotate_geographic_borough(cards)
        assert result[0]["geographic_borough"] == "Manhattan"
        # stated is None → comparison impossible → not flagged
        assert result[0]["borough_mismatch"] is False

    def test_empty_card_list(self):
        """Annotating an empty list returns an empty list — no crashes."""
        assert _annotate_geographic_borough([]) == []

    def test_mutates_and_returns_same_list(self):
        """Helper mutates the input list for caller convenience."""
        cards = [{
            "service_id": "7", "service_name": "Svc",
            "city": "New York",
            "latitude": 40.7484, "longitude": -73.9857,
        }]
        result = _annotate_geographic_borough(cards)
        assert result is cards  # same list object
        assert "geographic_borough" in cards[0]

    def test_batch_mixed_cards(self):
        """Multiple cards, mixed clean/mismatch/missing — all annotated."""
        cards = [
            {"service_id": "A", "city": "New York",
             "latitude": 40.7484, "longitude": -73.9857},  # clean Manhattan
            {"service_id": "B", "city": "New York",
             "latitude": 40.8296, "longitude": -73.9262},  # Manhattan-labeled, Bronx-located
            {"service_id": "C", "city": None,
             "latitude": None, "longitude": None},         # no data
            {"service_id": "D", "city": "Brooklyn",
             "latitude": 40.6826, "longitude": -73.9754},  # clean Brooklyn
        ]
        result = _annotate_geographic_borough(cards)
        assert len(result) == 4

        assert result[0]["borough_mismatch"] is False
        assert result[1]["borough_mismatch"] is True
        assert result[2]["borough_mismatch"] is False
        assert result[3]["borough_mismatch"] is False

        # Only the mismatched card has disagreement; others agree or lack data
        assert result[0]["geographic_borough"] == "Manhattan"
        assert result[1]["geographic_borough"] == "Bronx"
        assert result[2]["geographic_borough"] is None
        assert result[3]["geographic_borough"] == "Brooklyn"


# -------------------------------------------------------------------------
# POLICY: annotate-only, never filter
# -------------------------------------------------------------------------

class TestAnnotationPolicyIsNonDestructive:
    """The validator must never filter or reorder cards.

    Policy from BOUNDARY_AUDIT.md: tag and log only for initial rollout.
    If these tests start failing it means someone changed the policy
    without updating docs — a big deal worth flagging in review.
    """

    def test_card_count_preserved(self):
        """Validator never drops a card, even on mismatch."""
        cards = [
            {"service_id": f"X{i}", "city": "New York",
             "latitude": 40.8296, "longitude": -73.9262}  # all mismatches
            for i in range(5)
        ]
        result = _annotate_geographic_borough(cards)
        assert len(result) == 5

    def test_card_order_preserved(self):
        """Validator never reorders cards."""
        cards = [
            {"service_id": f"X{i}", "city": "New York",
             "latitude": 40.7484, "longitude": -73.9857}
            for i in range(10)
        ]
        result = _annotate_geographic_borough(cards)
        assert [c["service_id"] for c in result] == [f"X{i}" for i in range(10)]

    def test_existing_card_fields_preserved(self):
        """Annotation adds fields; it doesn't drop or alter existing ones."""
        cards = [{
            "service_id": "1",
            "service_name": "Test",
            "city": "New York",
            "address": "100 Main St",
            "phone": "212-555-1234",
            "latitude": 40.7484, "longitude": -73.9857,
            "some_other_field": "should remain",
        }]
        result = _annotate_geographic_borough(cards)
        assert result[0]["service_name"] == "Test"
        assert result[0]["address"] == "100 Main St"
        assert result[0]["phone"] == "212-555-1234"
        assert result[0]["some_other_field"] == "should remain"
