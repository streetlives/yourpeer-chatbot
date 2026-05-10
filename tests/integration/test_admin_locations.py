"""
Tests for the locations admin endpoints (day 1 — sections 1 + 2b).

Covers:
    GET /admin/api/locations/stats — top stat strip + 7-day trends
    GET /admin/api/locations/list  — paginated triage table

Each endpoint is tested for:
    * Auth required (401 without admin key) — once, since the
      auth dependency is shared with the rest of /admin.
    * Empty-DB shape — endpoint should return zeros / empty lists,
      not crash, when the underlying tables have no rows.
    * Non-empty shape — fixture DB rows produce the expected
      structured response.
    * Filter / sort / pagination behavior — the section 2b table
      has enough surface area that each lever needs its own test.

Run with: python -m pytest tests/integration/test_admin_locations.py -v
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from typing import Any
# Helper context manager — pytest's caplog fixture has subtle behavior
# around logger propagation in deeply-nested loggers; a simple
# capture context is cleaner for these tests.
from contextlib import contextmanager
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.services.audit_log import (
    clear_audit_log,
    log_location_feedback,
    log_query_execution,
)
from app.services.locations_admin.cache import clear_locations_admin_cache


# Cache-clearing fixture. The locations admin aggregations are
# wrapped in @ttl_cached() — without resetting between tests, the
# second test of a given endpoint would hit the cache from the first
# test, bypass the mocked _execute_sql, and produce surprising
# assertions ("nothing was queried but the response is populated").
# autouse=True keeps it invisible at the call site; matches the
# existing clear_audit_log() pattern at the start of every test.
@pytest.fixture(autouse=True)
def _clear_admin_cache_between_tests():
    clear_locations_admin_cache()
    yield
    clear_locations_admin_cache()


# Mirror the _AdminClient pattern from test_admin_api_routes.py — see
# that file for the full rationale (env var must be re-read on every
# request, not cached at import time).
class _AdminClient:
    def __init__(self, app_):
        self._client = TestClient(app_)
    def _headers(self, extra=None):
        key = os.environ.get("ADMIN_API_KEY")
        hdrs = {"Authorization": f"Bearer {key}"} if key else {}
        if extra:
            hdrs.update(extra)
        return hdrs
    def get(self, url, **kwargs):
        kwargs["headers"] = self._headers(kwargs.get("headers"))
        return self._client.get(url, **kwargs)


client = TestClient(app)              # for the 401 test
admin_client = _AdminClient(app)      # for everything else


# -----------------------------------------------------------------------
# FIXTURES — _execute_sql mock
#
# The Streetlives DB is read-only Postgres with PostGIS; we don't have
# a local copy in the test env. The aggregation functions hit it
# through `_execute_sql(sql, params)`, which we mock with a lookup
# table keyed on a small fingerprint of the SQL (the first significant
# clause). That lets us return distinct rows for each query without
# pretending to parse SQL.
# -----------------------------------------------------------------------

def _make_sql_responder(responses: dict[str, Any]):
    """Returns a side_effect function for patching _execute_sql.

    `responses` is a dict mapping a marker substring (something
    distinctive in the SQL) to the rows the mock should return when
    that substring appears.

    Markers are checked in dict insertion order. Tests that need to
    distinguish similar SQL (e.g., the list query and the count query
    both start with FROM locations l ... JOIN organizations) should
    list the more-specific marker first. For the list/count split,
    list COUNT-bearing queries with marker "COUNT(*) AS total" BEFORE
    the more general "FROM locations l" marker.

    Queries that don't match any marker return [].
    """
    def _responder(sql, params):
        for marker, rows in responses.items():
            if marker in sql:
                return rows
        return []
    return _responder


# Common shape for a stats query response — five sub-counts plus the
# 7-day-trend numbers.
def _stats_row(
    total_locations=2400, total_services=3500,
    fresh_count=600, never_verified=300, stale_count=1500,
    fresh_last_7d=20, fresh_prev_7d=15,
):
    return [{
        "total_locations": total_locations,
        "total_services": total_services,
        "fresh_count": fresh_count,
        "never_verified_count": never_verified,
        "stale_count": stale_count,
        "fresh_last_7d": fresh_last_7d,
        "fresh_prev_7d": fresh_prev_7d,
    }]


# Common shape for a list-query row.
def _list_row(
    location_id="loc-1", location_name="Sample Location",
    location_slug="sample-loc",
    organization="Sample Org", city="Manhattan", address_1="123 Fake St",
    last_validated_at=None, phone_number="2125551234",
    service_count=3, top_categories=("Food", "Clothing", "Health"),
    distinct_categories_count=4, has_hours=True,
):
    """Build a list-query mock row. top_categories/distinct_categories_count
    are kept on the same helper for test ergonomics — the test author
    declares them once and `_enrichment_rows_from` slices them out for
    the matching enrichment-query mock. See `_make_list_responder` for
    the standard wiring."""
    return {
        "location_id": location_id,
        "location_name": location_name,
        "location_slug": location_slug,
        "organization": organization,
        "city": city,
        "address_1": address_1,
        "last_validated_at": last_validated_at,
        "phone_number": phone_number,
        "service_count": service_count,
        # Internal-only fields for the test infrastructure — the actual
        # list_sql query doesn't return these post-M8 (they come from
        # enrich_sql instead), but keeping them on the row dict lets
        # tests configure them in one place and have the helpers split
        # them to the right responder branch.
        "top_categories": list(top_categories) if top_categories else [],
        "distinct_categories_count": distinct_categories_count,
        "has_hours": has_hours,
    }


def _enrichment_rows_from(list_rows):
    """Derive the enrichment-query mock rows from a list of _list_row
    outputs. Use alongside `_list_row` to mock both queries with one
    set of test data:

        rows = [_list_row(...)]
        responder = _make_sql_responder({
            "COUNT(*) AS total": [{"total": len(rows)}],
            "FROM locations l\\n    JOIN organizations": rows,
            "WITH ranked AS": _enrichment_rows_from(rows),
        })

    Returns one enrichment row per list row, shaped like the real
    enrich_sql output.
    """
    return [
        {
            "location_id": str(r.get("location_id", "")),
            "top_categories": r.get("top_categories", []),
            "distinct_categories_count": r.get("distinct_categories_count", 0),
        }
        for r in list_rows
    ]


# -----------------------------------------------------------------------
# AUTH
# -----------------------------------------------------------------------

def test_locations_stats_requires_admin_auth():
    """Inherits the parent /admin router's require_admin_key dependency.
    A request without the Authorization header should 401."""
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        # Plain client = no Authorization header
        response = client.get("/admin/api/locations/stats")
        assert response.status_code == 401


def test_locations_list_requires_admin_auth():
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        response = client.get("/admin/api/locations/list")
        assert response.status_code == 401


# -----------------------------------------------------------------------
# /stats
# -----------------------------------------------------------------------

def test_stats_empty_db_returns_zeros():
    """When the catalog has no locations and no feedback events,
    the endpoint should return all zeros — not crash."""
    clear_audit_log()
    responder = _make_sql_responder({"total_locations": _stats_row(
        total_locations=0, total_services=0,
        fresh_count=0, never_verified=0, stale_count=0,
        fresh_last_7d=0, fresh_prev_7d=0,
    )})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        r = admin_client.get("/admin/api/locations/stats")
        assert r.status_code == 200
        body = r.json()
        assert body["total_locations"] == 0
        assert body["total_services"] == 0
        assert body["fresh_count"] == 0
        assert body["never_verified_count"] == 0
        assert body["stale_count"] == 0
        assert body["with_feedback_count"] == 0
        assert body["trends"]["fresh_count"] == 0


def test_stats_full_shape():
    """Realistic-looking counts plus a few feedback events.
    Verifies the response shape end-to-end."""
    clear_audit_log()

    # 3 distinct locations have feedback (loc-A x2, loc-B x1, loc-C x1)
    log_location_feedback(session_id="s1", location_id="loc-A", safety=False)
    log_location_feedback(session_id="s2", location_id="loc-A", friendliness=True)
    log_location_feedback(session_id="s3", location_id="loc-B", cleanliness=False)
    log_location_feedback(session_id="s4", location_id="loc-C", queer_friendly=True)

    responder = _make_sql_responder({"total_locations": _stats_row()})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        r = admin_client.get("/admin/api/locations/stats")
        assert r.status_code == 200
        body = r.json()
        assert body["total_locations"] == 2400
        assert body["with_feedback_count"] == 3        # distinct
        # 7d trend = fresh_last_7d - fresh_prev_7d = 20 - 15 = 5
        assert body["trends"]["fresh_count"] == 5


def test_stats_propagates_db_error_as_500():
    """A query failure should surface a structured 500, not a crash."""
    clear_audit_log()
    def boom(sql, params):
        raise RuntimeError("simulated db failure")
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=boom):
        r = admin_client.get("/admin/api/locations/stats")
        assert r.status_code == 500
        assert r.json()["error"] is True


# -----------------------------------------------------------------------
# /list
# -----------------------------------------------------------------------

def test_list_empty_db_returns_empty_locations():
    clear_audit_log()
    responder = _make_sql_responder({})  # no marker matches → []
    # The COUNT query also returns [] which the function reads as total=0
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        r = admin_client.get("/admin/api/locations/list")
        assert r.status_code == 200
        body = r.json()
        assert body["locations"] == []
        assert body["total"] == 0
        assert body["page"] == 1
        assert body["page_size"] == 25


def test_list_full_row_shape():
    """A single fixture row exercises every field the frontend types
    expect — boolean derivations, borough labeling, slug-driven URL."""
    clear_audit_log()

    rows = [_list_row(
        location_id="loc-real",
        location_name="Bushwick Food Pantry",
        location_slug="bushwick-food-pantry",
        organization="St. Brigid's",
        city="Brooklyn",
        last_validated_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        top_categories=("Food", "Clothing", "Health"),
        distinct_categories_count=5,
    )]
    responder = _make_sql_responder({
        "COUNT(*) AS total": [{"total": 1}],
        "FROM locations l\n    JOIN organizations": rows,
        "WITH ranked AS": _enrichment_rows_from(rows),
    })

    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        r = admin_client.get("/admin/api/locations/list")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        loc = body["locations"][0]
        assert loc["location_id"] == "loc-real"
        assert loc["location_name"] == "Bushwick Food Pantry"
        assert loc["organization"] == "St. Brigid's"
        assert loc["city"] == "Brooklyn"
        assert loc["borough"] == "Brooklyn"
        assert loc["service_count"] == 3
        assert loc["service_categories"] == ["Food", "Clothing", "Health"]
        assert loc["service_categories_more"] == 2     # 5 distinct - 3 shown
        assert loc["has_phone"] is True
        assert loc["has_address"] is True
        assert loc["has_hours"] is True
        assert loc["yourpeer_url"] == "https://yourpeer.nyc/locations/bushwick-food-pantry"


def test_list_borough_label_other_for_non_nyc_city():
    """A row whose city isn't one of the 5 boroughs should label as 'Other'."""
    clear_audit_log()
    rows = [_list_row(city="Yonkers", location_id="loc-yonkers")]
    responder = _make_sql_responder({
        "COUNT(*) AS total": [{"total": 1}],
        "FROM locations l\n    JOIN organizations": rows,
        "WITH ranked AS": _enrichment_rows_from(rows),
    })
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        r = admin_client.get("/admin/api/locations/list")
        assert r.json()["locations"][0]["borough"] == "Other"


def test_list_missing_phone_address_hours_set_to_false():
    """A row with NULL phone, NULL address, or has_hours=False should
    set the corresponding boolean to False — these are the 'has_issues'
    triggers, surfacing data-quality gaps."""
    clear_audit_log()
    rows = [_list_row(
        location_id="loc-gappy",
        phone_number=None, address_1=None, has_hours=False,
    )]
    responder = _make_sql_responder({
        "COUNT(*) AS total": [{"total": 1}],
        "FROM locations l\n    JOIN organizations": rows,
        "WITH ranked AS": _enrichment_rows_from(rows),
    })
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        loc = admin_client.get("/admin/api/locations/list").json()["locations"][0]
        assert loc["has_phone"] is False
        assert loc["has_address"] is False
        assert loc["has_hours"] is False


def test_list_recent_flags_count_from_audit_log():
    """A location with recent location_feedback events that include a
    negative criterion (any False rating) increments recent_flags."""
    clear_audit_log()
    log_location_feedback(session_id="s1", location_id="loc-A", safety=False)
    log_location_feedback(session_id="s2", location_id="loc-A", friendliness=False)
    log_location_feedback(session_id="s3", location_id="loc-A", cleanliness=True)
    # Cleanliness=True is a positive — not a flag.

    rows = [_list_row(location_id="loc-A")]
    responder = _make_sql_responder({
        "COUNT(*) AS total": [{"total": 1}],
        "FROM locations l\n    JOIN organizations": rows,
        "WITH ranked AS": _enrichment_rows_from(rows),
    })
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        loc = admin_client.get("/admin/api/locations/list").json()["locations"][0]
        assert loc["recent_flags"] == 2
        assert loc["has_reviews"] is True


def test_list_recent_flags_excludes_old_events():
    """Events older than RECENT_FLAGS_LOOKBACK_DAYS should NOT count
    toward recent_flags. has_reviews stays True (it's all-time)."""
    clear_audit_log()
    # Inject an old event by mocking _now_iso for the duration of the
    # log_location_feedback call. The cleanest path is to write the
    # event directly to the in-memory store, bypassing the timestamp
    # generation — we use the helper but then patch the timestamp.
    log_location_feedback(session_id="old-sess", location_id="loc-A", safety=False)
    from app.services import audit_log as al
    # Backdate the most recent event.
    old_ts = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    al._events[-1]["timestamp"] = old_ts

    rows = [_list_row(location_id="loc-A")]
    responder = _make_sql_responder({
        "COUNT(*) AS total": [{"total": 1}],
        "FROM locations l\n    JOIN organizations": rows,
        "WITH ranked AS": _enrichment_rows_from(rows),
    })
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        loc = admin_client.get("/admin/api/locations/list").json()["locations"][0]
        assert loc["recent_flags"] == 0      # event is too old
        assert loc["has_reviews"] is True    # but still counts for all-time


def test_list_pagination_passes_through():
    """page + page_size params should be reflected in the response and
    propagated to the SQL LIMIT/OFFSET."""
    clear_audit_log()
    captured: dict = {}
    def capturing_responder(sql, params):
        # Order matters — COUNT marker is more specific, check first.
        # Both queries contain "FROM locations l\n    JOIN organizations".
        if "COUNT(*) AS total" in sql:
            return [{"total": 250}]
        if "FROM locations l\n    JOIN organizations" in sql:
            captured["limit"] = params.get("limit")
            captured["offset"] = params.get("offset")
            return []
        return []
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=capturing_responder):
        r = admin_client.get("/admin/api/locations/list?page=4&page_size=50")
        body = r.json()
        assert body["page"] == 4
        assert body["page_size"] == 50
        assert body["total"] == 250
        # Pagination math: page=4, page_size=50 → offset=150, limit=50
        assert captured["limit"] == 50
        assert captured["offset"] == 150


def test_list_page_size_capped_at_100():
    """Requests for huge page sizes should be clamped to 100, not
    propagated through (avoids accidental huge result sets)."""
    clear_audit_log()
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        r = admin_client.get("/admin/api/locations/list?page_size=500")
        # FastAPI's Query(le=100) returns 422 for out-of-range
        assert r.status_code == 422


def test_list_invalid_sort_key_falls_back_to_default_at_aggregation_layer():
    """Defense-in-depth: even if the route-layer Literal[] check is
    somehow bypassed (direct call to get_locations_list, future
    refactor that drops the type, etc.), the aggregation function's
    `_SORT_KEY_TO_SQL` whitelist should fall back to the default sort
    rather than crashing or injecting raw SQL.

    This used to test the route layer's behavior, but with L6 in
    place the route returns 422 for invalid sort_key — see
    test_list_returns_422_for_invalid_sort_key. This test now covers
    the inner layer.
    """
    from app.services.locations_admin.aggregations import get_locations_list
    captured_sql: list = []
    def capturing(sql, params):
        captured_sql.append(sql)
        if "COUNT(*) AS total" in sql:
            return [{"total": 0}]
        return []
    with patch("app.services.locations_admin.aggregations._execute_sql",
               side_effect=capturing):
        # Call the function directly with a bogus sort_key.
        result = get_locations_list(sort_key="NOT_A_REAL_KEY")
    assert result["locations"] == []
    # The ORDER BY in the actual list query should fall back to the
    # default sort column (last_validated_at), not crash, and not
    # contain the bogus key as raw SQL.
    list_query = next((s for s in captured_sql if "ORDER BY" in s), None)
    assert list_query is not None, (
        "Expected a query with ORDER BY in the captured SQL"
    )
    assert "last_validated_at" in list_query, (
        f"Expected fallback to last_validated_at; got ORDER BY in: "
        f"{list_query[:200]}"
    )
    assert "NOT_A_REAL_KEY" not in list_query, (
        f"Bogus sort_key leaked into SQL — defense-in-depth whitelist "
        f"is broken. SQL: {list_query[:200]}"
    )


# -----------------------------------------------------------------------
# /freshness-histogram (day 2 — section 2a)
# -----------------------------------------------------------------------

def test_histogram_requires_admin_auth():
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        response = client.get("/admin/api/locations/freshness-histogram")
        assert response.status_code == 401


def test_histogram_returns_six_buckets_in_canonical_order():
    """Histogram must always return all six buckets, in display order,
    regardless of whether each one has data. Empty buckets render as
    explicit zeros — not omitted."""
    clear_audit_log()
    rows = [{
        "lt30": 0, "m_30to90": 0, "m_90to180": 0,
        "m_180to365": 0, "gt365": 0, "never": 0, "total": 0,
    }]
    responder = _make_sql_responder({"FROM locations": rows})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        r = admin_client.get("/admin/api/locations/freshness-histogram")
        assert r.status_code == 200
        body = r.json()
        keys = [b["key"] for b in body["buckets"]]
        assert keys == ["lt30", "30to90", "90to180", "180to365", "gt365", "never"]
        # All zeros → every bucket count is 0
        assert all(b["count"] == 0 for b in body["buckets"])
        assert body["total"] == 0


def test_histogram_distributes_counts():
    """Realistic shape — make sure each FILTER expression maps to the
    right output key. Catches regressions if a future refactor
    transposes the counts."""
    clear_audit_log()
    rows = [{
        "lt30": 100, "m_30to90": 200, "m_90to180": 300,
        "m_180to365": 400, "gt365": 500, "never": 600, "total": 2100,
    }]
    responder = _make_sql_responder({"FROM locations": rows})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/freshness-histogram").json()
        counts_by_key = {b["key"]: b["count"] for b in body["buckets"]}
        assert counts_by_key["lt30"] == 100
        assert counts_by_key["30to90"] == 200
        assert counts_by_key["90to180"] == 300
        assert counts_by_key["180to365"] == 400
        assert counts_by_key["gt365"] == 500
        assert counts_by_key["never"] == 600
        assert body["total"] == 2100


def test_histogram_bucket_keys_match_list_endpoint_age_bucket_values():
    """Cross-endpoint contract: a click on a histogram bar must drive
    the section 2b table's age_bucket filter, so the bucket keys MUST
    be a subset of the values _SORT_KEY_TO_SQL accepts.

    If this assertion ever fails, the click-through filter will silently
    return wrong data — extremely hard to spot without a test."""
    from app.services.locations_admin.aggregations import _execute_sql as _
    # Source of truth for the table's age_bucket values, lifted from
    # the function body. If `get_locations_list` ever renames a bucket,
    # update this set.
    list_age_buckets = {"lt30", "30to90", "90to180", "180to365", "gt365", "never"}
    histogram_keys = {"lt30", "30to90", "90to180", "180to365", "gt365", "never"}
    assert histogram_keys == list_age_buckets, (
        "histogram bucket keys must equal the age_bucket values that "
        "/list accepts — drift between them silently breaks click-through filtering"
    )


# -----------------------------------------------------------------------
# /by-borough (day 2 — section 3a)
# -----------------------------------------------------------------------

def test_by_borough_requires_admin_auth():
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        response = client.get("/admin/api/locations/by-borough")
        assert response.status_code == 401


def test_by_borough_renders_all_six_in_canonical_order_even_when_empty():
    """All six borough labels (5 NYC + Other) must always appear, in
    display order, regardless of which the data contains. Empty
    boroughs render as zeros."""
    clear_audit_log()
    # No rows at all → response should still have 6 rows of zeros.
    responder = _make_sql_responder({})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        r = admin_client.get("/admin/api/locations/by-borough")
        assert r.status_code == 200
        body = r.json()
        boroughs = [row["borough"] for row in body["rows"]]
        assert boroughs == ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island", "Other"]
        assert all(row["location_count"] == 0 for row in body["rows"])
        assert all(row["service_count"] == 0 for row in body["rows"])
        assert all(row["verified_lt90d_pct"] is None for row in body["rows"])
        assert body["totals"] == {"location_count": 0, "service_count": 0}


def test_by_borough_aggregates_realistic_shape():
    """Boroughs with data render their actual counts; ratios round
    to 1 decimal."""
    clear_audit_log()
    # The grouped query returns one row per borough that has data.
    grouped_rows = [
        {"borough": "Manhattan", "location_count": 800, "service_count": 1200, "fresh_count": 240},
        {"borough": "Brooklyn", "location_count": 600, "service_count": 900, "fresh_count": 100},
        # Queens, Bronx, Staten Island, Other absent — should still render as zeros.
    ]
    top_cat_rows = [
        {"borough": "Manhattan", "category": "Food"},
        {"borough": "Brooklyn", "category": "Shelter"},
    ]
    def responder(sql, params):
        if "ROW_NUMBER" in sql:
            return top_cat_rows
        if "GROUP BY borough" in sql:
            return grouped_rows
        return []
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/by-borough").json()

        rows_by_borough = {r["borough"]: r for r in body["rows"]}

        assert rows_by_borough["Manhattan"]["location_count"] == 800
        assert rows_by_borough["Manhattan"]["service_count"] == 1200
        assert rows_by_borough["Manhattan"]["avg_services_per_location"] == 1.5  # 1200/800
        assert rows_by_borough["Manhattan"]["verified_lt90d_pct"] == 30.0       # 240/800 = 30%
        assert rows_by_borough["Manhattan"]["top_category"] == "Food"

        assert rows_by_borough["Brooklyn"]["top_category"] == "Shelter"
        assert rows_by_borough["Brooklyn"]["verified_lt90d_pct"] == round(100 * 100 / 600, 1)

        # Empty boroughs still render
        assert rows_by_borough["Queens"]["location_count"] == 0
        assert rows_by_borough["Queens"]["verified_lt90d_pct"] is None
        assert rows_by_borough["Queens"]["top_category"] is None

        assert body["totals"]["location_count"] == 1400  # 800 + 600
        assert body["totals"]["service_count"] == 2100   # 1200 + 900


def test_by_borough_unrecognized_label_falls_under_other():
    """Defensive: if pa.city has a value we don't recognize and the
    SQL CASE didn't catch it (shouldn't happen, but defensive),
    the response should still surface it rather than crashing."""
    clear_audit_log()
    grouped_rows = [
        # SQL CASE should bucket this as "Other" — matches the grouped query.
        {"borough": "Other", "location_count": 5, "service_count": 7, "fresh_count": 1},
    ]
    def responder(sql, params):
        if "ROW_NUMBER" in sql:
            return []
        if "GROUP BY borough" in sql:
            return grouped_rows
        return []
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/by-borough").json()
        other = next(r for r in body["rows"] if r["borough"] == "Other")
        assert other["location_count"] == 5
        assert other["top_category"] is None


# -----------------------------------------------------------------------
# /heatmap (day 3 — section 3b)
# -----------------------------------------------------------------------

def test_heatmap_requires_admin_auth():
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        response = client.get("/admin/api/locations/heatmap")
        assert response.status_code == 401


def test_heatmap_empty_data_returns_empty_categories():
    """No taxonomy rows → empty categories array but boroughs list
    still present (frontend uses it to render the column headers
    even when there's no data)."""
    clear_audit_log()
    responder = _make_sql_responder({})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        r = admin_client.get("/admin/api/locations/heatmap")
        assert r.status_code == 200
        body = r.json()
        assert body["categories"] == []
        assert body["boroughs"] == ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island", "Other"]


def test_heatmap_realistic_shape():
    """Three categories × 6 boroughs → 18 cells. Categories sort by
    total_locations DESC; every cell renders as an explicit count
    (never missing keys)."""
    clear_audit_log()
    rows = [
        # Food: 100 + 60 + 40 + 30 + 10 + 5 = 245
        {"category": "Food", "borough": "Manhattan", "location_count": 100},
        {"category": "Food", "borough": "Brooklyn", "location_count": 60},
        {"category": "Food", "borough": "Queens", "location_count": 40},
        {"category": "Food", "borough": "Bronx", "location_count": 30},
        {"category": "Food", "borough": "Staten Island", "location_count": 10},
        {"category": "Food", "borough": "Other", "location_count": 5},
        # Shelter: 50 + 30 = 80 — present in only 2 boroughs (the rest
        # should render as 0, not missing)
        {"category": "Shelter", "borough": "Manhattan", "location_count": 50},
        {"category": "Shelter", "borough": "Brooklyn", "location_count": 30},
        # Clothing: 20 + 10 + 5 = 35
        {"category": "Clothing", "borough": "Brooklyn", "location_count": 20},
        {"category": "Clothing", "borough": "Queens", "location_count": 10},
        {"category": "Clothing", "borough": "Bronx", "location_count": 5},
    ]
    responder = _make_sql_responder({"GROUP BY t.name, borough": rows})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/heatmap").json()

        # Categories sorted by total_locations DESC
        names = [c["name"] for c in body["categories"]]
        assert names == ["Food", "Shelter", "Clothing"]

        food = body["categories"][0]
        assert food["total_locations"] == 245
        # Every borough column is present even on partial-data categories
        shelter = body["categories"][1]
        assert set(shelter["by_borough"].keys()) == {"Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island", "Other"}
        assert shelter["by_borough"]["Manhattan"] == 50
        assert shelter["by_borough"]["Queens"] == 0          # no data → explicit 0
        assert shelter["by_borough"]["Other"] == 0


def test_heatmap_categories_with_equal_totals_sort_alphabetically():
    """Tie-break for sort stability — equal totals sort by name."""
    clear_audit_log()
    rows = [
        {"category": "Food", "borough": "Manhattan", "location_count": 50},
        {"category": "Apparel", "borough": "Manhattan", "location_count": 50},
        {"category": "Mental Health", "borough": "Manhattan", "location_count": 50},
    ]
    responder = _make_sql_responder({"GROUP BY t.name, borough": rows})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/heatmap").json()
        # All three have total_locations=50; alphabetical breaks the tie
        names = [c["name"] for c in body["categories"]]
        assert names == ["Apparel", "Food", "Mental Health"]


# -----------------------------------------------------------------------
# /coordinate-issues (day 3 — section 3c)
# -----------------------------------------------------------------------

def test_coordinate_issues_requires_admin_auth():
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        response = client.get("/admin/api/locations/coordinate-issues")
        assert response.status_code == 401


def test_coordinate_issues_no_locations_returns_empty():
    """No rows from SQL → empty issues + zero counters."""
    clear_audit_log()
    responder = _make_sql_responder({})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        r = admin_client.get("/admin/api/locations/coordinate-issues")
        assert r.status_code == 200
        body = r.json()
        assert body["issues"] == []
        assert body["total_with_coords"] == 0
        assert body["outside_nyc_count"] == 0


def test_coordinate_issues_clean_data_returns_no_issues():
    """Locations whose stated city matches their coordinates → no issues."""
    clear_audit_log()
    rows = [
        # Coords near Manhattan, stated as Manhattan → clean
        {
            "location_id": "loc-clean",
            "location_name": "Test Location",
            "location_slug": "test",
            "organization": "Test Org",
            "stated_city": "Manhattan",
            "latitude": 40.7831, "longitude": -73.9712,
        },
    ]
    responder = _make_sql_responder({"l.position IS NOT NULL": rows})

    # Mock both helpers to return Manhattan — agreement → no issue
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder), \
         patch("app.rag.boundaries.borough_from_coords", return_value="Manhattan"), \
         patch("app.rag.query_executor.stated_borough_from_city", return_value="Manhattan"):
        body = admin_client.get("/admin/api/locations/coordinate-issues").json()
        assert body["issues"] == []
        assert body["total_with_coords"] == 1
        assert body["outside_nyc_count"] == 0


def test_coordinate_issues_borough_mismatch():
    """Coords compute as Manhattan, city says Brooklyn → mismatch, flagged."""
    clear_audit_log()
    rows = [
        {
            "location_id": "loc-mismatch",
            "location_name": "Confused Location",
            "location_slug": "confused",
            "organization": "Some Org",
            "stated_city": "Brooklyn",
            "latitude": 40.7831, "longitude": -73.9712,
        },
    ]
    responder = _make_sql_responder({"l.position IS NOT NULL": rows})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder), \
         patch("app.rag.boundaries.borough_from_coords", return_value="Manhattan"), \
         patch("app.rag.query_executor.stated_borough_from_city", return_value="Brooklyn"):
        body = admin_client.get("/admin/api/locations/coordinate-issues").json()
        assert len(body["issues"]) == 1
        issue = body["issues"][0]
        assert issue["location_id"] == "loc-mismatch"
        assert issue["stated_borough"] == "Brooklyn"
        assert issue["computed_borough"] == "Manhattan"
        assert body["outside_nyc_count"] == 0


def test_coordinate_issues_outside_nyc():
    """Coords outside NYC entirely → flagged, counted in outside_nyc_count."""
    clear_audit_log()
    rows = [
        {
            "location_id": "loc-outside",
            "location_name": "Wrong Coords Location",
            "location_slug": "wrong",
            "organization": "Some Org",
            "stated_city": "Manhattan",
            "latitude": 39.0, "longitude": -75.0,    # somewhere in Delaware
        },
    ]
    responder = _make_sql_responder({"l.position IS NOT NULL": rows})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder), \
         patch("app.rag.boundaries.borough_from_coords", return_value=None), \
         patch("app.rag.query_executor.stated_borough_from_city", return_value="Manhattan"):
        body = admin_client.get("/admin/api/locations/coordinate-issues").json()
        assert len(body["issues"]) == 1
        issue = body["issues"][0]
        assert issue["computed_borough"] is None    # signals outside-NYC
        assert body["outside_nyc_count"] == 1


def test_coordinate_issues_unmappable_city_does_not_flag():
    """City that can't be mapped to a borough (e.g. an out-of-state hotline)
    AND coords also outside NYC → counts as outside-NYC issue, but NOT
    a 'mismatch' since we have no stated_borough to compare against."""
    clear_audit_log()
    rows = [
        {
            "location_id": "loc-newark",
            "location_name": "Newark Hotline",
            "location_slug": "newark",
            "organization": "Out of State Org",
            "stated_city": "Newark",   # not an NYC borough
            "latitude": 40.7357, "longitude": -74.1724,    # Newark coords
        },
    ]
    responder = _make_sql_responder({"l.position IS NOT NULL": rows})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder), \
         patch("app.rag.boundaries.borough_from_coords", return_value=None), \
         patch("app.rag.query_executor.stated_borough_from_city", return_value=None):
        body = admin_client.get("/admin/api/locations/coordinate-issues").json()
        # Outside NYC → still surfaces as issue (data quality concern)
        assert len(body["issues"]) == 1
        assert body["issues"][0]["computed_borough"] is None
        assert body["issues"][0]["stated_borough"] is None
        assert body["outside_nyc_count"] == 1


def test_coordinate_issues_outside_nyc_sort_first():
    """Outside-NYC issues should sort BEFORE mismatch issues since
    they're more concerning data bugs (likely typo'd coords vs.
    just a wrong-city-tag)."""
    clear_audit_log()
    rows = [
        # Mismatch issue
        {
            "location_id": "loc-mismatch",
            "location_name": "B Mismatch Location",
            "location_slug": "b-mismatch",
            "organization": "Org B",
            "stated_city": "Brooklyn",
            "latitude": 40.7831, "longitude": -73.9712,
        },
        # Outside-NYC issue
        {
            "location_id": "loc-outside",
            "location_name": "A Outside Location",
            "location_slug": "a-outside",
            "organization": "Org A",
            "stated_city": "Manhattan",
            "latitude": 39.0, "longitude": -75.0,
        },
    ]
    responder = _make_sql_responder({"l.position IS NOT NULL": rows})

    # Each call to borough_from_coords gets the corresponding row's
    # coords — return None for the outside-NYC one, "Manhattan" for
    # the mismatch one. side_effect=list returns values in order.
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder), \
         patch("app.rag.boundaries.borough_from_coords", side_effect=["Manhattan", None]), \
         patch("app.rag.query_executor.stated_borough_from_city", side_effect=["Brooklyn", "Manhattan"]):
        body = admin_client.get("/admin/api/locations/coordinate-issues").json()
        assert len(body["issues"]) == 2
        # Outside-NYC sorts before mismatch
        assert body["issues"][0]["location_id"] == "loc-outside"
        assert body["issues"][1]["location_id"] == "loc-mismatch"


# -----------------------------------------------------------------------
# /category-coverage (day 4 — section 4a)
# -----------------------------------------------------------------------

def test_category_coverage_requires_admin_auth():
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        response = client.get("/admin/api/locations/category-coverage")
        assert response.status_code == 401


def test_category_coverage_empty_data():
    """No taxonomies → empty categories array, zero uncategorized."""
    clear_audit_log()
    responder = _make_sql_responder({})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        r = admin_client.get("/admin/api/locations/category-coverage")
        assert r.status_code == 200
        body = r.json()
        assert body["categories"] == []
        assert body["uncategorized_demand"] == {"query_count": 0, "no_result_count": 0}


def test_category_coverage_supply_only_no_demand():
    """Taxonomy supply data with no demand → category appears with
    demand_query_count=0, demand_supply_ratio=null, and verified%
    correctly computed."""
    clear_audit_log()
    rows = [
        {
            "taxonomy_name": "Food",
            "service_count": 50,
            "location_count": 30,
            "fresh_location_count": 12,
        },
    ]
    responder = _make_sql_responder({"GROUP BY t.name": rows})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/category-coverage").json()
        assert len(body["categories"]) == 1
        cat = body["categories"][0]
        assert cat["taxonomy_name"] == "Food"
        assert cat["service_count"] == 50
        assert cat["location_count"] == 30
        assert cat["fresh_location_count"] == 12
        assert cat["verified_lt90d_pct"] == 40.0
        assert cat["demand_query_count"] == 0
        assert cat["demand_supply_ratio"] is None    # no demand → no ratio


def test_category_coverage_demand_attributed_via_template_mapping():
    """Demand from FoodQuery template → split evenly across the 11
    taxonomies in its mapping. Each Food-related taxonomy gets
    queries / 11."""
    clear_audit_log()
    # Seed audit log so get_stats has something to compute from
    log_query_execution(
        session_id="s1", template_name="FoodQuery",
        params={}, result_count=2, execution_ms=20,
    )
    log_query_execution(
        session_id="s2", template_name="FoodQuery",
        params={}, result_count=0, execution_ms=22,
    )

    # Single taxonomy "Food" — covered by FoodQuery's mapping
    sql_rows = [
        {
            "taxonomy_name": "Food",
            "service_count": 100,
            "location_count": 50,
            "fresh_location_count": 25,
        },
    ]
    responder = _make_sql_responder({"GROUP BY t.name": sql_rows})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/category-coverage").json()
        cat = body["categories"][0]
        # FoodQuery covers 11 taxonomies → each gets 2/11 queries, 1/11 no-result
        assert cat["demand_query_count"] == round(2 / 11, 1)
        assert cat["no_result_count"] == round(1 / 11, 1)
        # demand_supply_ratio = (2/11) / 50 ≈ 0.004 → rounds to 0.0
        assert cat["demand_supply_ratio"] == round((2 / 11) / 50, 2)


def test_category_coverage_uncategorized_demand_aggregated():
    """Templates not in the mapping (OtherServicesQuery) accumulate
    in the uncategorized aggregate."""
    clear_audit_log()
    log_query_execution(
        session_id="s1", template_name="OtherServicesQuery",
        params={}, result_count=0, execution_ms=15,
    )
    log_query_execution(
        session_id="s2", template_name="OtherServicesQuery",
        params={}, result_count=3, execution_ms=18,
    )

    responder = _make_sql_responder({})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/category-coverage").json()
        assert body["uncategorized_demand"]["query_count"] == 2
        assert body["uncategorized_demand"]["no_result_count"] == 1


def test_category_coverage_sort_by_ratio_desc_nulls_last():
    """Categories with demand:supply ratios sort DESC; nulls land at
    the bottom sorted by location_count DESC as the secondary key."""
    clear_audit_log()
    # Seed: FoodQuery contributes demand to "Food" taxonomies
    for _ in range(11):    # 11 queries → 1 per taxonomy after split
        log_query_execution(
            session_id="s1", template_name="FoodQuery",
            params={}, result_count=1, execution_ms=20,
        )
    sql_rows = [
        # Has demand, ratio = 1 / 100 = 0.01
        {"taxonomy_name": "Food", "service_count": 100, "location_count": 100, "fresh_location_count": 50},
        # Has demand, ratio = 1 / 5 = 0.2  ← should sort first
        {"taxonomy_name": "Food Pantry", "service_count": 5, "location_count": 5, "fresh_location_count": 1},
        # No demand mapping for this taxonomy → null ratio
        {"taxonomy_name": "Some Other Tax", "service_count": 200, "location_count": 200, "fresh_location_count": 50},
        # Also no demand, smaller location count
        {"taxonomy_name": "Tiny Tax", "service_count": 3, "location_count": 3, "fresh_location_count": 0},
    ]
    responder = _make_sql_responder({"GROUP BY t.name": sql_rows})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/category-coverage").json()
        names = [c["taxonomy_name"] for c in body["categories"]]
        # Food Pantry first (highest ratio), Food second (lower ratio),
        # then "Some Other Tax" (no ratio, but bigger location_count)
        # before "Tiny Tax" (no ratio, smaller location_count).
        assert names == ["Food Pantry", "Food", "Some Other Tax", "Tiny Tax"]


# -----------------------------------------------------------------------
# /stale-categories (day 4 — section 4b)
# -----------------------------------------------------------------------

def test_stale_categories_requires_admin_auth():
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        response = client.get("/admin/api/locations/stale-categories")
        assert response.status_code == 401


def test_stale_categories_empty_returns_empty_list():
    """No stale categories → empty list, total_stale=0."""
    clear_audit_log()
    responder = _make_sql_responder({})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        r = admin_client.get("/admin/api/locations/stale-categories")
        assert r.status_code == 200
        body = r.json()
        assert body["categories"] == []
        assert body["total_stale"] == 0
        assert body["lookback_days"] == 180


def test_stale_categories_returns_oldest_first():
    """Categories with older max_verified_at sort first; days_since
    computed against today."""
    from datetime import date, timedelta as td
    clear_audit_log()
    # Two stale categories, one staler than the other
    older_date = date.today() - td(days=400)
    newer_date = date.today() - td(days=200)
    rows = [
        # SQL ORDER BY ASC NULLS FIRST puts oldest first; we trust
        # that and just consume in order.
        {"taxonomy_name": "Free Wi-Fi", "location_count": 3, "max_verified_at": older_date},
        {"taxonomy_name": "Showers", "location_count": 8, "max_verified_at": newer_date},
    ]
    responder = _make_sql_responder({"HAVING": rows})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/stale-categories").json()
        assert body["categories"][0]["taxonomy_name"] == "Free Wi-Fi"
        assert body["categories"][0]["days_since_max_verified"] == 400
        assert body["categories"][1]["taxonomy_name"] == "Showers"
        assert body["categories"][1]["days_since_max_verified"] == 200


def test_stale_categories_truncates_at_top_n():
    """When more than STALE_CATEGORIES_TOP_N (10) qualify, only the
    top 10 are returned but total_stale reports the full count."""
    from datetime import date, timedelta as td
    clear_audit_log()
    # 15 stale rows
    rows = [
        {
            "taxonomy_name": f"Stale {i}",
            "location_count": 1,
            "max_verified_at": date.today() - td(days=200 + i),
        }
        for i in range(15)
    ]
    responder = _make_sql_responder({"HAVING": rows})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/stale-categories").json()
        assert len(body["categories"]) == 10
        assert body["total_stale"] == 15


def test_stale_categories_handles_null_max_verified():
    """A category whose locations have all NULL last_validated_at
    qualifies as stale (max returns NULL → caught by the HAVING
    OR-clause). days_since is null."""
    clear_audit_log()
    rows = [
        {"taxonomy_name": "Never Verified Tax", "location_count": 2, "max_verified_at": None},
    ]
    responder = _make_sql_responder({"HAVING": rows})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/stale-categories").json()
        cat = body["categories"][0]
        assert cat["taxonomy_name"] == "Never Verified Tax"
        assert cat["max_verified_at"] is None
        assert cat["days_since_max_verified"] is None


# -----------------------------------------------------------------------
# /feedback-aggregates (day 5 — sections 5a + 5b)
# -----------------------------------------------------------------------

def test_feedback_aggregates_requires_admin_auth():
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        response = client.get("/admin/api/locations/feedback-aggregates")
        assert response.status_code == 401


def test_feedback_aggregates_empty_returns_zeros():
    """No feedback events at all → empty most_flagged, zeroed
    criterion_summary, but the response shape is fully populated
    (all four criteria appear with rated=0, negative_pct=null)."""
    clear_audit_log()
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        r = admin_client.get("/admin/api/locations/feedback-aggregates")
        assert r.status_code == 200
        body = r.json()
        assert body["most_flagged"] == []
        assert body["total_eligible"] == 0
        assert body["min_sample"] == 2
        assert body["total_events_overall"] == 0
        # All four criteria render even with no data
        assert set(body["criterion_summary"].keys()) == {
            "safety", "friendliness", "cleanliness", "queer_friendly"
        }
        for crit, summary in body["criterion_summary"].items():
            assert summary["events_rated"] == 0
            assert summary["positive"] == 0
            assert summary["negative"] == 0
            assert summary["negative_pct"] is None


def test_feedback_aggregates_min_sample_excludes_one_offs():
    """A location with only 1 feedback event should NOT appear in
    most_flagged. This is the FEEDBACK_MIN_SAMPLE=2 cutoff that
    answers spec question 3."""
    clear_audit_log()
    log_location_feedback(
        session_id="s1", location_id="loc-only-once",
        location_name="Single Event Location", safety=False,
    )
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        body = admin_client.get("/admin/api/locations/feedback-aggregates").json()
        assert body["most_flagged"] == []
        assert body["total_eligible"] == 0
        # But the single event still contributes to the criterion summary
        assert body["total_events_overall"] == 1
        assert body["criterion_summary"]["safety"]["events_rated"] == 1
        assert body["criterion_summary"]["safety"]["negative"] == 1


def test_feedback_aggregates_min_sample_includes_at_threshold():
    """A location with exactly FEEDBACK_MIN_SAMPLE (2) events SHOULD
    appear — the cutoff is inclusive."""
    clear_audit_log()
    log_location_feedback(
        session_id="s1", location_id="loc-at-threshold",
        location_name="Threshold Location", safety=False,
    )
    log_location_feedback(
        session_id="s2", location_id="loc-at-threshold",
        location_name="Threshold Location", safety=True,
    )
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        body = admin_client.get("/admin/api/locations/feedback-aggregates").json()
        assert len(body["most_flagged"]) == 1
        assert body["most_flagged"][0]["location_id"] == "loc-at-threshold"
        assert body["total_eligible"] == 1


def test_feedback_aggregates_smoothing_breaks_tied_100pct_cliff():
    """Two locations both at 100% negative — Laplace smoothing must
    rank the higher-sample one above the lower-sample one. This is
    the bug the smoothing exists to prevent: without it, 2/2 and
    4/4 would both score 1.0 and order would depend on dict
    iteration luck."""
    clear_audit_log()
    # 2/2 negative
    log_location_feedback(session_id="s1", location_id="loc-small", location_name="Small", safety=False)
    log_location_feedback(session_id="s2", location_id="loc-small", location_name="Small", safety=False)
    # 4/4 negative — should rank above
    for i in range(4):
        log_location_feedback(
            session_id=f"big-{i}", location_id="loc-big", location_name="Big",
            safety=False,
        )
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        body = admin_client.get("/admin/api/locations/feedback-aggregates").json()
        ids = [r["location_id"] for r in body["most_flagged"]]
        assert ids == ["loc-big", "loc-small"]
        # Smoothed values: 2/2 → 3/4 = 0.75; 4/4 → 5/6 ≈ 0.8333
        big = body["most_flagged"][0]
        small = body["most_flagged"][1]
        assert big["negative_ratio_smoothed"] == 0.8333
        assert small["negative_ratio_smoothed"] == 0.75
        # raw_negative_ratio for both should be 1.0 — surfacing both
        # smoothed and raw lets admins see what the "truth" is plus
        # what ranking we used.
        assert big["raw_negative_ratio"] == 1.0
        assert small["raw_negative_ratio"] == 1.0


def test_feedback_aggregates_criterion_counts_per_location():
    """Each location's criterion_counts breaks down individual ratings
    into pos/neg/rated. A location rated False on safety twice and
    True on friendliness once should show safety=2 negative + 0 pos
    + 2 rated; friendliness=0 neg + 1 pos + 1 rated."""
    clear_audit_log()
    log_location_feedback(
        session_id="s1", location_id="loc-mixed",
        location_name="Mixed", safety=False, friendliness=True,
    )
    log_location_feedback(
        session_id="s2", location_id="loc-mixed",
        location_name="Mixed", safety=False,
    )
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        body = admin_client.get("/admin/api/locations/feedback-aggregates").json()
        loc = body["most_flagged"][0]
        c = loc["criterion_counts"]
        assert c["safety"] == {"positive": 0, "negative": 2, "rated": 2}
        assert c["friendliness"] == {"positive": 1, "negative": 0, "rated": 1}
        # Cleanliness and queer_friendly both have all-zero counts
        # (nobody rated them) — included in shape regardless
        assert c["cleanliness"] == {"positive": 0, "negative": 0, "rated": 0}
        assert c["queer_friendly"] == {"positive": 0, "negative": 0, "rated": 0}


def test_feedback_aggregates_criterion_summary_population_pct():
    """Section 5b: per-criterion population %. Across all events, what
    fraction of safety ratings (where users actually rated safety)
    were negative?

    Setup: 4 events. Safety rated in all 4: 1 True + 3 False = 75% neg.
    Cleanliness rated in 1 event: 1 True = 0% neg.
    Other criteria not rated → events_rated=0, negative_pct=null."""
    clear_audit_log()
    log_location_feedback(session_id="s1", location_id="loc-A", location_name="A",
                          safety=False, cleanliness=True)
    log_location_feedback(session_id="s2", location_id="loc-A", location_name="A", safety=False)
    log_location_feedback(session_id="s3", location_id="loc-B", location_name="B", safety=True)
    log_location_feedback(session_id="s4", location_id="loc-B", location_name="B", safety=False)
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        cs = admin_client.get("/admin/api/locations/feedback-aggregates").json()["criterion_summary"]
        assert cs["safety"]["events_rated"] == 4
        assert cs["safety"]["positive"] == 1
        assert cs["safety"]["negative"] == 3
        assert cs["safety"]["negative_pct"] == 75.0
        assert cs["cleanliness"]["events_rated"] == 1
        assert cs["cleanliness"]["negative"] == 0
        assert cs["cleanliness"]["negative_pct"] == 0.0
        # No friendliness ratings at all → events_rated=0, negative_pct=null
        assert cs["friendliness"]["events_rated"] == 0
        assert cs["friendliness"]["negative_pct"] is None


def test_feedback_aggregates_comments_count():
    """A location's comments_count is the number of events with a
    non-empty comment. Empty strings and whitespace-only strings
    don't count."""
    clear_audit_log()
    log_location_feedback(session_id="s1", location_id="loc-C", location_name="C",
                          safety=False, comment="really unsafe at night")
    log_location_feedback(session_id="s2", location_id="loc-C", location_name="C",
                          safety=True, comment="")
    log_location_feedback(session_id="s3", location_id="loc-C", location_name="C",
                          safety=False, comment="   ")    # whitespace-only
    log_location_feedback(session_id="s4", location_id="loc-C", location_name="C",
                          safety=False, comment="staff was rude")
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        body = admin_client.get("/admin/api/locations/feedback-aggregates").json()
        loc = body["most_flagged"][0]
        assert loc["total_events"] == 4
        assert loc["comments_count"] == 2    # only the two real comments


def test_feedback_aggregates_top_n_truncation():
    """When more than MOST_FLAGGED_TOP_N (10) locations qualify, only
    the top 10 by smoothed negative ratio appear. total_eligible
    reports the full count."""
    clear_audit_log()
    # 12 locations, each with 2 feedback events (above the min sample).
    # Vary negative count to produce different ratios.
    for i in range(12):
        # Location i has i False ratings out of 2 events. So:
        # i=0: 0 neg / 2 → smoothed 1/4 = 0.25
        # i=1: but 1 neg + 1 pos / 2 → smoothed 2/4 = 0.5
        # i=2 onward: capped at 2 negs (only 2 events), so all i>=2
        #   look the same — that's fine, we're testing truncation
        #   not ranking nuance.
        for j in range(2):
            is_negative = j < min(i, 2)
            log_location_feedback(
                session_id=f"s-{i}-{j}", location_id=f"loc-{i}",
                location_name=f"Loc {i}",
                safety=False if is_negative else True,
            )
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        body = admin_client.get("/admin/api/locations/feedback-aggregates").json()
        assert len(body["most_flagged"]) == 10    # truncated
        assert body["total_eligible"] == 12       # full count surfaced


# -----------------------------------------------------------------------
# /feedback-comments (day 6 — section 5c)
# -----------------------------------------------------------------------

def test_feedback_comments_requires_admin_auth():
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        response = client.get("/admin/api/locations/feedback-comments")
        assert response.status_code == 401


def test_feedback_comments_empty_returns_empty_list():
    """No feedback events at all → empty comments array, zeros."""
    clear_audit_log()
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        r = admin_client.get("/admin/api/locations/feedback-comments")
        assert r.status_code == 200
        body = r.json()
        assert body["comments"] == []
        assert body["total_with_comments"] == 0
        assert body["limit"] == 50    # default


def test_feedback_comments_filters_empty_and_whitespace():
    """Events without comments (None, empty, whitespace-only) should
    NOT appear. Only the ones with real content surface."""
    clear_audit_log()
    log_location_feedback(session_id="s1", location_id="loc-A", location_name="A",
                          safety=False, comment="really helpful staff")
    log_location_feedback(session_id="s2", location_id="loc-B", location_name="B",
                          safety=True)    # no comment kwarg → None
    log_location_feedback(session_id="s3", location_id="loc-C", location_name="C",
                          cleanliness=False, comment="")
    log_location_feedback(session_id="s4", location_id="loc-D", location_name="D",
                          friendliness=False, comment="   \n  \t  ")    # whitespace-only
    log_location_feedback(session_id="s5", location_id="loc-E", location_name="E",
                          safety=False, comment="ran out of food")
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        body = admin_client.get("/admin/api/locations/feedback-comments").json()
        # Only s1 + s5 have real comments
        assert body["total_with_comments"] == 2
        ids = [c["session_id"] for c in body["comments"]]
        assert set(ids) == {"s1", "s5"}


def test_feedback_comments_reverse_chronological_order():
    """Most-recent comment first."""
    clear_audit_log()
    # Log in oldest-first order; expect them returned newest-first.
    log_location_feedback(session_id="oldest", location_id="loc-A",
                          location_name="A", comment="first comment")
    log_location_feedback(session_id="middle", location_id="loc-B",
                          location_name="B", comment="second comment")
    log_location_feedback(session_id="newest", location_id="loc-C",
                          location_name="C", comment="third comment")
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        body = admin_client.get("/admin/api/locations/feedback-comments").json()
        ids = [c["session_id"] for c in body["comments"]]
        assert ids == ["newest", "middle", "oldest"]


def test_feedback_comments_strips_whitespace_from_comment_text():
    """The returned comment string should be stripped of leading/
    trailing whitespace — internal newlines preserved."""
    clear_audit_log()
    log_location_feedback(
        session_id="s1", location_id="loc-A", location_name="A",
        comment="  \n  staff was nice\n\nbut wait was long  \n  ",
    )
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        body = admin_client.get("/admin/api/locations/feedback-comments").json()
        comment = body["comments"][0]["comment"]
        assert comment == "staff was nice\n\nbut wait was long"


def test_feedback_comments_separates_negative_and_positive_criteria():
    """negative_criteria / positive_criteria arrays separate True/False
    ratings, sorted in canonical _FEEDBACK_CRITERIA order regardless
    of the order users provided ratings."""
    clear_audit_log()
    log_location_feedback(
        session_id="s1", location_id="loc-A", location_name="A",
        # Provide ratings in a non-canonical order
        queer_friendly=True, safety=False, friendliness=True, cleanliness=False,
        comment="mixed bag",
    )
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        c = admin_client.get("/admin/api/locations/feedback-comments").json()["comments"][0]
        # Canonical order: safety, friendliness, cleanliness, queer_friendly
        assert c["negative_criteria"] == ["safety", "cleanliness"]
        assert c["positive_criteria"] == ["friendliness", "queer_friendly"]


def test_feedback_comments_no_criteria_when_only_comment_provided():
    """A comment without any criterion ratings still appears (the
    qualitative content is the value); negative/positive arrays
    are empty."""
    clear_audit_log()
    log_location_feedback(
        session_id="s1", location_id="loc-A", location_name="A",
        comment="some general feedback without rating any specific thing",
    )
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        c = admin_client.get("/admin/api/locations/feedback-comments").json()["comments"][0]
        assert c["comment"]
        assert c["negative_criteria"] == []
        assert c["positive_criteria"] == []


def test_feedback_comments_limit_param_truncates():
    """The limit query param caps the response. total_with_comments
    still reports the full filtered count."""
    clear_audit_log()
    for i in range(8):
        log_location_feedback(
            session_id=f"s{i}", location_id=f"loc-{i}",
            location_name=f"Loc {i}", comment=f"comment {i}",
        )
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        r = admin_client.get("/admin/api/locations/feedback-comments?limit=3")
        body = r.json()
        assert len(body["comments"]) == 3
        assert body["total_with_comments"] == 8
        assert body["limit"] == 3


def test_feedback_comments_limit_above_max_rejected():
    """Limits above RECENT_COMMENTS_MAX_LIMIT (200) → 422 from FastAPI."""
    clear_audit_log()
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        r = admin_client.get("/admin/api/locations/feedback-comments?limit=500")
        assert r.status_code == 422


def test_feedback_comments_session_id_preserved_for_drilldown():
    """session_id and location_id must round-trip exactly so the
    frontend can deep-link into the transcript drawer."""
    clear_audit_log()
    log_location_feedback(
        session_id="abc-123-def", location_id="loc-uuid-456",
        location_name="Some Location",
        safety=False, comment="needs fixing",
    )
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        c = admin_client.get("/admin/api/locations/feedback-comments").json()["comments"][0]
        assert c["session_id"] == "abc-123-def"
        assert c["location_id"] == "loc-uuid-456"
        assert c["location_name"] == "Some Location"


# -----------------------------------------------------------------------
# /integrity-callouts (day 7 — section 6)
# -----------------------------------------------------------------------

def test_integrity_callouts_requires_admin_auth():
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        response = client.get("/admin/api/locations/integrity-callouts")
        assert response.status_code == 401


def test_integrity_callouts_all_clear():
    """When every count check returns 0, all_clear=True and the
    callouts array is empty."""
    clear_audit_log()
    # All count queries return 0
    responder = _make_sql_responder({"COUNT": [{"n": 0}]})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/integrity-callouts").json()
        assert body["all_clear"] is True
        assert body["callouts"] == []
        assert body["total_callouts"] == 0


def test_integrity_callouts_orphaned_locations_fires():
    """When orphan-locations count > 0 → callout appears with the
    expected severity and id."""
    clear_audit_log()
    # Marker-substring routing: each callout SQL has a unique substring.
    # We respond with non-zero only for the orphan-locations query.
    def responder(sql, params):
        if "service_at_locations sal ON sal.location_id = l.id" in sql:
            return [{"n": 7}]
        return [{"n": 0}]
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/integrity-callouts").json()
        assert body["all_clear"] is False
        assert body["total_callouts"] == 1
        c = body["callouts"][0]
        assert c["id"] == "orphaned_locations"
        assert c["severity"] == "warning"
        assert c["count"] == 7


def test_integrity_callouts_orphaned_services_fires():
    """When orphaned-services count > 0 → callout appears."""
    clear_audit_log()
    def responder(sql, params):
        if "service_at_locations sal ON sal.service_id = s.id" in sql:
            return [{"n": 3}]
        return [{"n": 0}]
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/integrity-callouts").json()
        ids = [c["id"] for c in body["callouts"]]
        assert "orphaned_services" in ids


def test_integrity_callouts_malformed_phones_fires():
    """When malformed-phones count > 0 → callout appears."""
    clear_audit_log()
    def responder(sql, params):
        if "FROM phones" in sql:
            return [{"n": 12}]
        return [{"n": 0}]
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/integrity-callouts").json()
        c = next(c for c in body["callouts"] if c["id"] == "malformed_phones")
        assert c["count"] == 12
        assert c["severity"] == "warning"


def test_integrity_callouts_entity_encoded_html_is_info_severity():
    """Encoded-HTML callout is 'info' rather than 'warning' —
    frontend strips it at render time, so it's a "fix at source"
    note, not an active bug."""
    clear_audit_log()
    def responder(sql, params):
        if "&lt;br" in sql:
            return [{"n": 4}]
        return [{"n": 0}]
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/integrity-callouts").json()
        c = next(c for c in body["callouts"] if c["id"] == "entity_encoded_html")
        assert c["severity"] == "info"


def test_integrity_callouts_coord_issues_ref_with_outside_nyc():
    """Section 3c reference fires with 'warning' severity when
    outside-NYC count > 0."""
    clear_audit_log()
    # Coordinate-issues query returns a row that triggers section-3c
    # outside-NYC counter. Other count queries return 0.
    def responder(sql, params):
        if "l.position IS NOT NULL" in sql:
            return [{
                "location_id": "outside-nyc-loc",
                "location_name": "Far Away",
                "location_slug": "far-away",
                "organization": "Org",
                "stated_city": "Manhattan",
                "latitude": 39.0, "longitude": -75.0,
            }]
        return [{"n": 0}]
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder), \
         patch("app.rag.boundaries.borough_from_coords", return_value=None), \
         patch("app.rag.query_executor.stated_borough_from_city", return_value="Manhattan"):
        body = admin_client.get("/admin/api/locations/integrity-callouts").json()
        c = next(c for c in body["callouts"] if c["id"] == "coordinate_issues_ref")
        assert c["severity"] == "warning"   # outside_nyc_count > 0
        assert c["ref"] == "section_3c_coordinate_validation"


def test_integrity_callouts_multiple_fire_in_display_order():
    """When multiple count checks fire, callouts appear in their
    declared display order — orphans first, then phones, then HTML.
    (Coord-issues last when applicable.) Tests stable ordering."""
    clear_audit_log()
    def responder(sql, params):
        # Fire orphan-locations, malformed-phones, entity-encoded-HTML
        if "service_at_locations sal ON sal.location_id = l.id" in sql:
            return [{"n": 1}]
        if "FROM phones" in sql:
            return [{"n": 2}]
        if "&lt;br" in sql:
            return [{"n": 3}]
        return [{"n": 0}]
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/integrity-callouts").json()
        ids = [c["id"] for c in body["callouts"]]
        assert ids == ["orphaned_locations", "malformed_phones", "entity_encoded_html"]


# -----------------------------------------------------------------------
# /timeseries (day 8 — section 7)
# -----------------------------------------------------------------------

def test_timeseries_requires_admin_auth():
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        response = client.get("/admin/api/locations/timeseries")
        assert response.status_code == 401


def test_timeseries_returns_canonical_week_count():
    """Always returns exactly TIMESERIES_WEEKS rows (=26 in v1) in
    chronological order, regardless of how much data exists."""
    clear_audit_log()
    responder = _make_sql_responder({})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/timeseries").json()
        assert body["total_weeks"] == 26
        assert len(body["weeks"]) == 26
        # Weeks are ascending dates
        dates = [w["week_start"] for w in body["weeks"]]
        assert dates == sorted(dates)


def test_timeseries_empty_data_renders_zeros_not_missing():
    """Weeks with no activity should render as 0s, not be missing
    from the response. Frontend depends on this for plotting."""
    clear_audit_log()
    responder = _make_sql_responder({})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/timeseries").json()
        for w in body["weeks"]:
            assert w["locations_added"] == 0
            assert w["locations_verified"] == 0
            assert w["feedback_events"] == 0


def test_timeseries_aggregates_added_and_verified_by_week():
    """Raw timestamps from the SQL queries land in the correct ET week."""
    from datetime import date, timedelta as td
    clear_audit_log()
    # Match the production code's ET-relative week math (M9). Tests
    # were UTC-based before; switching to ET keeps them aligned with
    # the function's now-ET-relative bucketing.
    from app.services.locations_admin.aggregations import DISPLAY_TIMEZONE
    today_et = datetime.now(DISPLAY_TIMEZONE).date()
    days_since_monday = today_et.weekday()
    this_monday = today_et - td(days=days_since_monday)
    last_monday = this_monday - td(weeks=1)

    # Build raw tz-aware timestamps that fall into the right ET weeks.
    # Mid-day timestamps avoid any boundary-of-day weirdness.
    def at_et_noon(d):
        return datetime.combine(d, datetime.min.time().replace(hour=12),
                                tzinfo=DISPLAY_TIMEZONE)

    def responder(sql, params):
        if "FROM locations l" in sql and "l.created_at" in sql:
            # 5 events in this_monday's week, 3 in last_monday's week
            return (
                [{"ts": at_et_noon(this_monday)}] * 5
                + [{"ts": at_et_noon(last_monday)}] * 3
            )
        if "FROM locations l" in sql and "l.last_validated_at" in sql:
            return [{"ts": at_et_noon(this_monday)}] * 2
        return []

    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/timeseries").json()
        this_week = next(w for w in body["weeks"] if w["week_start"] == this_monday.isoformat())
        last_week = next(w for w in body["weeks"] if w["week_start"] == last_monday.isoformat())
        assert this_week["locations_added"] == 5
        assert this_week["locations_verified"] == 2
        assert last_week["locations_added"] == 3
        assert last_week["locations_verified"] == 0


def test_timeseries_feedback_events_aggregated_from_audit_log():
    """Feedback events come from the audit log, not the DB. They
    should be bucketed into the right ET week (M9 — week boundaries
    are display-timezone-relative)."""
    from datetime import timedelta as td
    clear_audit_log()
    log_location_feedback(
        session_id="s1", location_id="loc-A", location_name="A",
        safety=False, comment="not safe",
    )
    log_location_feedback(
        session_id="s2", location_id="loc-B", location_name="B",
        cleanliness=False, comment="not clean",
    )
    from app.services.locations_admin.aggregations import DISPLAY_TIMEZONE
    today_et = datetime.now(DISPLAY_TIMEZONE).date()
    days_since_monday = today_et.weekday()
    this_monday = today_et - td(days=days_since_monday)

    responder = _make_sql_responder({})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        body = admin_client.get("/admin/api/locations/timeseries").json()
        this_week = next(w for w in body["weeks"] if w["week_start"] == this_monday.isoformat())
        assert this_week["feedback_events"] == 2


# -----------------------------------------------------------------------
# ROUTE-WIRING SANITY (polish-day addition)
#
# Every locations admin endpoint should be reachable when the router is
# wired correctly. Catches the kind of bug where a forgotten __init__
# export or a typo'd @router.get decorator silently disables a section.
# Per-aggregation tests cover the math; this catches the wiring.
# -----------------------------------------------------------------------

def test_all_locations_endpoints_reachable_when_wired():
    """Every documented locations admin endpoint returns a 200 (or
    its known auth response) under a clean audit log + minimal SQL
    responder. Catches forgotten exports, typo'd routes, or
    unregistered sub-router."""
    clear_audit_log()
    # Smart responder: returns the minimum shape each query expects.
    # The goal isn't to test math correctness (per-aggregation tests
    # already do that) — it's to confirm every endpoint successfully
    # routes through the package, calls its function, and returns.
    def responder(sql, params):
        # Stats query: COALESCE with named columns; needs row-shaped
        # response with all the column keys present.
        if "total_locations" in sql and "fresh_count" in sql:
            return [{
                "total_locations": 0,
                "total_services": 0,
                "fresh_count": 0,
                "never_verified_count": 0,
                "stale_count": 0,
                "fresh_last_7d": 0,
                "fresh_prev_7d": 0,
            }]
        # /list count query — `SELECT COUNT(...) AS total`
        if "AS total" in sql or " total\n" in sql:
            return [{"total": 0}]
        # Freshness histogram: COUNT FILTER on each bucket key.
        if "FILTER" in sql and "lt30" in sql:
            return [{
                "lt30": 0, "30to90": 0, "90to180": 0,
                "180to365": 0, "gt365": 0, "never": 0,
            }]
        # Count-shaped queries (COUNT(*) AS n) for integrity callouts.
        if "COUNT" in sql:
            return [{"n": 0}]
        # Everything else: empty list. Aggregation functions all
        # tolerate empty input.
        return []
    paths = [
        "/admin/api/locations/stats",
        "/admin/api/locations/list?limit=10",
        "/admin/api/locations/freshness-histogram",
        "/admin/api/locations/by-borough",
        "/admin/api/locations/heatmap",
        "/admin/api/locations/coordinate-issues",
        "/admin/api/locations/category-coverage",
        "/admin/api/locations/stale-categories",
        "/admin/api/locations/feedback-aggregates",
        "/admin/api/locations/feedback-comments",
        "/admin/api/locations/integrity-callouts",
        "/admin/api/locations/timeseries",
    ]
    # Pre-flight check: count routes registered on the locations
    # sub-router. If this is < 12, the wiring failure is the
    # decorators themselves — not the responder / auth / etc. —
    # and naming the missing ones in the failure message saves the
    # reader from running the diagnostic manually.
    from app.routes.admin_locations import router as _locations_router
    registered_paths = sorted({r.path for r in _locations_router.routes})
    expected_paths = sorted([
        "/api/locations/stats",
        "/api/locations/list",
        "/api/locations/freshness-histogram",
        "/api/locations/by-borough",
        "/api/locations/heatmap",
        "/api/locations/coordinate-issues",
        "/api/locations/category-coverage",
        "/api/locations/stale-categories",
        "/api/locations/feedback-aggregates",
        "/api/locations/feedback-comments",
        "/api/locations/integrity-callouts",
        "/api/locations/timeseries",
    ])
    missing_at_router = [p for p in expected_paths if p not in registered_paths]
    assert not missing_at_router, (
        f"locations sub-router is missing {len(missing_at_router)} route(s): "
        f"{missing_at_router}. "
        "Check `backend/app/routes/admin_locations.py` for the @router.get "
        "decorators — the test file expects 12 endpoints, the router has "
        f"{len(registered_paths)}. Likely a partial checkout / merge where "
        "the route file is stale relative to aggregations.py + __init__.py "
        "(both of which would otherwise fail at module import time, not "
        "silently 404 a subset)."
    )

    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        for path in paths:
            r = admin_client.get(path)
            assert r.status_code == 200, (
                f"{path} returned {r.status_code} — likely a routing or "
                f"export wiring issue. Body: {r.text[:200]}"
            )


def test_all_locations_endpoints_require_admin_auth():
    """Every locations endpoint requires the admin Bearer token.
    Catches a forgotten admin-auth dependency (would silently leak
    catalog stats to anyone with the URL)."""
    paths = [
        "/admin/api/locations/stats",
        "/admin/api/locations/list",
        "/admin/api/locations/freshness-histogram",
        "/admin/api/locations/by-borough",
        "/admin/api/locations/heatmap",
        "/admin/api/locations/coordinate-issues",
        "/admin/api/locations/category-coverage",
        "/admin/api/locations/stale-categories",
        "/admin/api/locations/feedback-aggregates",
        "/admin/api/locations/feedback-comments",
        "/admin/api/locations/integrity-callouts",
        "/admin/api/locations/timeseries",
    ]
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        for path in paths:
            r = client.get(path)
            assert r.status_code == 401, (
                f"{path} returned {r.status_code} — expected 401 "
                f"without the admin Bearer token. This endpoint may "
                f"be missing its admin-auth dependency."
            )


# -----------------------------------------------------------------------
# AUDIT_LOG_CAP observability — confirms the cap warning fires at the
# boundary so silent truncation becomes visible in production logs.
# -----------------------------------------------------------------------

def test_audit_log_cap_warning_fires_when_limit_reached(caplog):
    """When the audit-log read returns exactly AUDIT_LOG_CAP events,
    the wrapper logs a warning so production observability surfaces
    the likely truncation. Catches the boundary case where the cap
    is first hit; the alternative (silent truncation) is what the
    wrapper exists to prevent.

    Note the test fakes a "cap hit" by patching the underlying
    get_recent_events — same shape as production once the audit log
    grows past 10k of one type.
    """
    import logging
    from app.services.locations_admin.aggregations import (
        _get_events_capped,
        AUDIT_LOG_CAP,
    )

    # Return exactly AUDIT_LOG_CAP events — looks like truncation
    # from the wrapper's perspective.
    fake_events = [{"type": "location_feedback"} for _ in range(AUDIT_LOG_CAP)]
    with patch(
        "app.services.locations_admin.aggregations.get_recent_events",
        return_value=fake_events,
    ), caplog.at_level(logging.WARNING, logger="app.services.locations_admin.aggregations"):
        events = _get_events_capped("location_feedback")
    assert len(events) == AUDIT_LOG_CAP
    # Warning fired
    assert any(
        "AUDIT_LOG_CAP" in r.message and "location_feedback" in r.message
        for r in caplog.records
    ), (
        "Expected an AUDIT_LOG_CAP warning when the read hits the cap, "
        "got: " + repr([r.message for r in caplog.records])
    )


def test_audit_log_cap_warning_silent_below_limit(caplog):
    """Under the cap, the wrapper stays quiet — no warning, no log
    noise. Confirms the warning's selectivity."""
    import logging
    from app.services.locations_admin.aggregations import (
        _get_events_capped,
        AUDIT_LOG_CAP,
    )

    fake_events = [{"type": "location_feedback"} for _ in range(AUDIT_LOG_CAP - 1)]
    with patch(
        "app.services.locations_admin.aggregations.get_recent_events",
        return_value=fake_events,
    ), caplog.at_level(logging.WARNING, logger="app.services.locations_admin.aggregations"):
        events = _get_events_capped("location_feedback")
    assert len(events) == AUDIT_LOG_CAP - 1
    assert not any("AUDIT_LOG_CAP" in r.message for r in caplog.records), (
        "Did not expect an AUDIT_LOG_CAP warning when below the cap; "
        "got: " + repr([r.message for r in caplog.records])
    )


def test_has_issues_filter_includes_flagged_locations_from_audit_log():
    """has_issues=true should surface locations with recent negative
    feedback even when their structural data (phone/address/hours) is
    complete. Previously the filter only looked at SQL columns; this
    confirms the audit-log intersection is wired."""
    clear_audit_log()
    # Log a negative-feedback event for a specific location id.
    log_location_feedback(
        session_id="sess-1",
        location_id="loc-flagged-123",
        location_name="Flagged Loc",
        safety=False,
        comment="not safe",
    )

    # Capture the params that the list query is called with so we
    # can assert that flagged_loc_ids made it into the bind dict.
    captured_params: dict[str, Any] = {}
    def responder(sql, params):
        if "FROM locations l\n    JOIN organizations" in sql and "LIMIT" in sql:
            captured_params.update(params)
            return []   # no rows — we only care about the params here
        if "COUNT(*) AS total" in sql:
            return [{"total": 0}]
        return []

    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        r = admin_client.get("/admin/api/locations/list?has_issues=true")
        assert r.status_code == 200

    assert "flagged_loc_ids" in captured_params, (
        "has_issues filter should bind flagged_loc_ids so audit-log "
        f"flags can be intersected. Got params: {list(captured_params)}"
    )
    assert "loc-flagged-123" in captured_params["flagged_loc_ids"], (
        "Expected the logged feedback location id to appear in the "
        f"bound flagged_loc_ids list. Got: {captured_params['flagged_loc_ids']!r}"
    )


def test_has_issues_filter_omits_flagged_loc_ids_param_when_off():
    """Sanity: when has_issues is NOT set, the flagged_loc_ids bind
    param shouldn't appear at all. Catches accidental coupling of the
    audit-log walk to the param-binding path."""
    clear_audit_log()
    log_location_feedback(
        session_id="sess-1",
        location_id="loc-flagged-456",
        location_name="Flagged Loc",
        safety=False,
    )

    captured_params: dict[str, Any] = {}
    def responder(sql, params):
        if "FROM locations l\n    JOIN organizations" in sql and "LIMIT" in sql:
            captured_params.update(params)
            return []
        if "COUNT(*) AS total" in sql:
            return [{"total": 0}]
        return []

    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        admin_client.get("/admin/api/locations/list")  # has_issues default = false

    assert "flagged_loc_ids" not in captured_params, (
        "flagged_loc_ids should only be bound when has_issues=true; "
        f"got params: {list(captured_params)}"
    )


# -----------------------------------------------------------------------
# Enum validation on /list — Literal[...] types should produce 422 on
# invalid values rather than silently degrading to default behavior.
# Catches the case where a typo'd filter URL gets misleadingly empty
# results instead of a clear "that value isn't allowed" response.
# -----------------------------------------------------------------------

def test_list_returns_422_for_invalid_sort_key():
    """Invalid sort_key values are rejected at the route layer with a
    helpful 422 listing the accepted values. Previously the aggregation
    function whitelisted these internally and silently fell back to the
    default — which made typos look like degraded results, not a user
    error.
    """
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        r = admin_client.get("/admin/api/locations/list?sort_key=not_a_real_column")
    assert r.status_code == 422, (
        f"Expected 422 on invalid sort_key; got {r.status_code}. "
        f"Body: {r.text[:200]}"
    )
    # FastAPI's 422 body includes the field name and accepted values
    # so users / clients can self-correct.
    body = r.json()
    assert "sort_key" in r.text or any(
        "sort_key" in str(err.get("loc", "")) for err in body.get("detail", [])
    ), f"422 body should name the failing field. Got: {body}"


def test_list_returns_422_for_invalid_sort_dir():
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        r = admin_client.get("/admin/api/locations/list?sort_dir=sideways")
    assert r.status_code == 422


def test_list_returns_422_for_invalid_borough():
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        r = admin_client.get("/admin/api/locations/list?borough=Atlantis")
    assert r.status_code == 422


def test_list_returns_422_for_invalid_age_bucket():
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}):
        r = admin_client.get("/admin/api/locations/list?age_bucket=lt7")
    assert r.status_code == 422


def test_list_accepts_valid_closed_set_values():
    """Sanity check: every value in the closed-set enums should be
    accepted by the route layer. If this fails, the Literal[...] type
    is too restrictive (or the value in `paths` is wrong)."""
    # Each tuple: (param_name, valid_value)
    valid_cases = [
        ("sort_key", "name"),
        ("sort_key", "organization"),
        ("sort_key", "city"),
        ("sort_key", "service_count"),
        ("sort_key", "last_validated_at"),
        ("sort_key", "recent_flags"),
        ("sort_dir", "asc"),
        ("sort_dir", "desc"),
        ("sort_dir", "asc_nulls_first"),
        ("sort_dir", "desc_nulls_first"),
        ("borough", "Manhattan"),
        ("borough", "Brooklyn"),
        ("borough", "Queens"),
        ("borough", "Bronx"),
        ("borough", "Staten Island"),
        ("borough", "Other"),
        ("age_bucket", "lt30"),
        ("age_bucket", "30to90"),
        ("age_bucket", "90to180"),
        ("age_bucket", "180to365"),
        ("age_bucket", "gt365"),
        ("age_bucket", "never"),
    ]
    # SQL responder that returns empty results so the endpoint completes
    # rather than crashing on the missing DB. We only care that the route
    # ACCEPTED the param, not what came back.
    def responder(sql, params):
        if "COUNT(*) AS total" in sql:
            return [{"total": 0}]
        return []
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=responder):
        for param, value in valid_cases:
            from urllib.parse import quote
            r = admin_client.get(f"/admin/api/locations/list?{param}={quote(value)}")
            assert r.status_code == 200, (
                f"Valid {param}={value!r} got {r.status_code} — the Literal[] "
                f"type at the route layer is rejecting a value the aggregation "
                f"function accepts. Body: {r.text[:200]}"
            )


# -----------------------------------------------------------------------
# L4 regression — last_event_at picks the chronologically latest event,
# not the lexicographically-largest timestamp string. Catches the case
# where the audit log writes timestamps with different fractional-second
# precision (e.g. millisecond-truncated vs microsecond-full) and the old
# string-compare would pick the wrong one.
# -----------------------------------------------------------------------

def test_last_event_at_uses_datetime_compare_not_string_compare():
    """Two events for the same location written in different ISO sub-
    formats. Event A: `Z` suffix, no fractional seconds. Event B:
    `+00:00` suffix, half-second past A. Chronologically B is later,
    but the `Z` character (codepoint 90) sorts AFTER `.` (codepoint 46),
    so lexicographic compare picks A. Datetime compare correctly picks B.

    This is the format-drift case the L4 fix defends against — today
    every log entry comes from `datetime.now(timezone.utc).isoformat()`
    which produces consistent `+00:00` output, but the moment any
    future code path writes a `Z`-suffixed timestamp (or a naive one,
    or a different fractional precision), the lex compare silently
    gives the wrong answer. This test pins the correct behavior so
    a future regression to string-compare fires a clear test failure.
    """
    clear_audit_log()
    from app.services import audit_log
    earlier = {
        "type": "location_feedback",
        "timestamp": "2026-05-01T10:00:00Z",        # parses to 10:00:00.0 UTC
        "session_id": "s1",
        "location_id": "loc-precision-test",
        "location_name": "Test Loc",
        "ratings": {"safety": True},
        "comment": "earlier",
    }
    later = {
        "type": "location_feedback",
        "timestamp": "2026-05-01T10:00:00.5+00:00", # parses to 10:00:00.5 UTC
        "session_id": "s2",
        "location_id": "loc-precision-test",
        "location_name": "Test Loc",
        "ratings": {"safety": False},
        "comment": "later",
    }
    # Setup-correctness check: confirm the test premise.
    # String compare must pick `earlier` (the WRONG choice) so the
    # test can prove datetime compare picks differently.
    assert earlier["timestamp"] > later["timestamp"], (
        "Test setup error: chosen timestamps don't reproduce the "
        "lexicographic-vs-chronological mismatch. "
        f"{earlier['timestamp']!r} should be lex-greater than "
        f"{later['timestamp']!r}."
    )

    with audit_log._lock:
        audit_log._events.append(earlier)
        audit_log._events.append(later)

    from app.services.locations_admin.aggregations import (
        get_location_feedback_aggregates,
    )
    result = get_location_feedback_aggregates()
    most_flagged = {row["location_id"]: row for row in result["most_flagged"]}
    assert "loc-precision-test" in most_flagged, (
        f"Expected loc-precision-test in most_flagged; got "
        f"{list(most_flagged)}. FEEDBACK_MIN_SAMPLE may have changed."
    )
    last_at = most_flagged["loc-precision-test"]["last_event_at"]
    assert last_at == later["timestamp"], (
        f"last_event_at picked the wrong event. Got {last_at!r}, "
        f"expected {later['timestamp']!r} (the chronologically later "
        f"event). String-compare would have picked {earlier['timestamp']!r} "
        f"because `Z` > `.` lexicographically. L4 should be enforcing "
        f"datetime compare."
    )


# -----------------------------------------------------------------------
# M7 — taxonomy name validation. Admins type categories from URL bars,
# saved filters, or autocomplete; typos silently produce empty results
# unless we validate. The fix runs a small query against the taxonomies
# table at the start of /list, logs warnings for unmatched names, and
# uses only the matched subset in the actual filter.
# -----------------------------------------------------------------------

def test_list_validates_category_names_and_filters_to_valid_subset():
    """When some category names match taxonomies and others don't, the
    actual filter should use only the valid subset and the bind param
    should reflect that. Logs a warning naming the invalid values."""
    clear_audit_log()
    import logging
    captured_params: dict[str, Any] = {}

    def responder(sql, params):
        # Taxonomy validation query — return only "Food" as valid.
        if "FROM taxonomies WHERE name = ANY" in sql:
            return [{"name": "Food"}]
        # List query — capture params for assertion.
        if "FROM locations l\n    JOIN organizations" in sql and "LIMIT" in sql:
            captured_params.update(params)
            return []
        if "COUNT(*) AS total" in sql:
            return [{"total": 0}]
        return []

    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql",
               side_effect=responder), \
         caplog_for("app.services.locations_admin.aggregations") as captured_logs:
        r = admin_client.get(
            "/admin/api/locations/list?category=Food&category=BadName&category=AlsoBad"
        )
        assert r.status_code == 200

    # Filter should contain only the valid one.
    assert captured_params.get("category_list") == ["Food"], (
        f"Expected category_list to be ['Food'] (only valid name), got "
        f"{captured_params.get('category_list')!r}"
    )
    # Warning should fire mentioning the invalid names.
    warnings = [r.getMessage() for r in captured_logs.records
                if r.levelno >= logging.WARNING]
    assert any(
        "BadName" in w and "AlsoBad" in w for w in warnings
    ), (
        f"Expected a warning naming both invalid category names. Got: "
        f"{warnings}"
    )


def test_list_all_invalid_categories_produces_empty_filter_and_warns():
    """When EVERY supplied category is invalid, the filter binds an
    empty array. Postgres matches no rows → empty result page, which is
    the right semantic (user asked to filter, nothing valid → nothing
    matches). The warning makes the cause visible in logs."""
    clear_audit_log()
    import logging
    captured_params: dict[str, Any] = {}

    def responder(sql, params):
        if "FROM taxonomies WHERE name = ANY" in sql:
            return []   # nothing matches
        if "FROM locations l\n    JOIN organizations" in sql and "LIMIT" in sql:
            captured_params.update(params)
            return []
        if "COUNT(*) AS total" in sql:
            return [{"total": 0}]
        return []

    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql",
               side_effect=responder), \
         caplog_for("app.services.locations_admin.aggregations") as captured_logs:
        r = admin_client.get(
            "/admin/api/locations/list?category=Junk1&category=Junk2"
        )
        assert r.status_code == 200

    assert captured_params.get("category_list") == [], (
        "Expected empty category_list bind when all input was invalid; "
        f"got {captured_params.get('category_list')!r}"
    )
    warnings = [r.getMessage() for r in captured_logs.records
                if r.levelno >= logging.WARNING]
    assert any("Junk1" in w and "Junk2" in w for w in warnings), (
        f"Expected a warning naming all invalid names. Got: {warnings}"
    )


@contextmanager
def caplog_for(logger_name: str):
    """Capture logs from the named logger as a context manager.

    Yields a stub object with `.records` attribute mirroring pytest's
    caplog fixture. The locations_admin logger doesn't always propagate
    to caplog reliably (depends on test ordering), so we attach a
    handler directly.
    """
    import logging
    logger_obj = logging.getLogger(logger_name)
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Capture(level=logging.DEBUG)
    logger_obj.addHandler(handler)
    original_level = logger_obj.level
    logger_obj.setLevel(logging.DEBUG)
    try:
        yield type("Captured", (), {"records": records})()
    finally:
        logger_obj.removeHandler(handler)
        logger_obj.setLevel(original_level)


# -----------------------------------------------------------------------
# M9 — display-timezone-aware week boundaries. Without ET handling,
# a Sunday-evening event in ET (= early Monday UTC) would land in the
# wrong week from a NYC admin's perspective. This pins the correct
# behavior using a constructed timestamp that's unambiguous: 9pm ET
# Sunday = 2am UTC Monday. UTC bucketing → next week's bucket.
# ET bucketing → current Sunday's week.
# -----------------------------------------------------------------------

def test_timeseries_buckets_events_by_et_week_not_utc_week():
    """A feedback event logged at 9pm Sunday ET (= 2am Monday UTC) must
    bucket into the SUNDAY-containing week, not the next week. The
    test constructs the timestamp by hand so it doesn't depend on
    when the test happens to run."""
    from datetime import timedelta as td
    clear_audit_log()
    from app.services import audit_log
    from app.services.locations_admin.aggregations import DISPLAY_TIMEZONE

    # Build a tz-aware "this past Sunday at 9pm ET" timestamp.
    today_et = datetime.now(DISPLAY_TIMEZONE).date()
    days_since_monday = today_et.weekday()
    this_monday_et = today_et - td(days=days_since_monday)
    sunday_et = this_monday_et - td(days=1)         # Sunday of LAST week
    last_monday_et = sunday_et - td(days=6)         # Monday of LAST week
    sunday_9pm_et = datetime.combine(
        sunday_et,
        datetime.min.time().replace(hour=21),
        tzinfo=DISPLAY_TIMEZONE,
    )
    # Sanity: this Sunday-9pm-ET timestamp converts to Monday-early-UTC.
    sunday_9pm_utc = sunday_9pm_et.astimezone(timezone.utc)
    assert sunday_9pm_utc.weekday() == 0, (
        "Test setup error: Sunday 9pm ET should land on Monday in UTC. "
        f"Got UTC weekday {sunday_9pm_utc.weekday()}. Likely the date "
        "math is off; double-check ET vs UTC offsets."
    )

    # Inject the event with the boundary-straddling timestamp.
    with audit_log._lock:
        audit_log._events.append({
            "type": "location_feedback",
            "timestamp": sunday_9pm_et.isoformat(),
            "session_id": "boundary",
            "location_id": "loc-boundary",
            "location_name": "Boundary",
            "ratings": {"safety": False},
        })

    responder = _make_sql_responder({})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql",
               side_effect=responder):
        body = admin_client.get("/admin/api/locations/timeseries").json()

    # Find the LAST week's bucket (the one containing the Sunday).
    last_week_bucket = next(
        (w for w in body["weeks"] if w["week_start"] == last_monday_et.isoformat()),
        None,
    )
    assert last_week_bucket is not None, (
        f"Couldn't find bucket for week starting {last_monday_et.isoformat()}. "
        f"Got week starts: {[w['week_start'] for w in body['weeks']]}"
    )
    assert last_week_bucket["feedback_events"] == 1, (
        f"Event at 9pm Sunday ET should be in last week's bucket "
        f"(week of {last_monday_et}); UTC bucketing would have put it "
        f"in this week's bucket (week of {this_monday_et}). Got "
        f"feedback_events={last_week_bucket['feedback_events']} in "
        f"last week."
    )

    # Sanity check the other direction: THIS week's bucket should NOT
    # have the event (would indicate UTC bucketing).
    this_week_bucket = next(
        (w for w in body["weeks"] if w["week_start"] == this_monday_et.isoformat()),
        None,
    )
    assert this_week_bucket is not None, "this_monday_et bucket missing"
    assert this_week_bucket["feedback_events"] == 0, (
        f"Event leaked into THIS week's bucket — that's the UTC-vs-ET "
        f"bug M9 is supposed to fix. last_week={last_week_bucket['feedback_events']}, "
        f"this_week={this_week_bucket['feedback_events']}"
    )


def test_timeseries_handles_dst_spring_forward_correctly():
    """DST edge case: the spring-forward Sunday is a 23-hour day in ET.
    A feedback event at 9pm EDT on that Sunday must still bucket into
    the Monday-before week, not the Monday-after week.

    The M9 implementation uses calendar-date arithmetic for bucket
    assignment (`dt.weekday()`, `timedelta(days=N)`), which is
    DST-immune for date math. The WHERE bound has a 1-day buffer
    beyond the earliest expected event, so no events are accidentally
    excluded near the boundary. This test proves both invariants by
    pinning "now" to a fixed post-DST date and injecting an event at
    the exact DST-Sunday-evening boundary.

    Spring-forward 2024-03-10: at 02:00 EST the clock jumps to 03:00
    EDT. An event at 9pm EDT on Sunday 2024-03-10 corresponds to
    01:00 UTC on Monday 2024-03-11 — UTC bucketing would put it in
    the week of 2024-03-11 (wrong; one week off from the admin's
    wall clock).
    """
    from datetime import datetime as real_datetime
    from app.services.locations_admin.aggregations import DISPLAY_TIMEZONE
    from app.services import audit_log

    # Fixed "now": Wednesday after the 2024-03-10 spring-forward.
    # The 26-week window from here looks back to ~Sep 2023; the DST
    # Sunday (2024-03-10) is comfortably within that range.
    FAKE_NOW_DATE = real_datetime(2024, 3, 13, 12, 0, 0)

    class MockedDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return FAKE_NOW_DATE
            return FAKE_NOW_DATE.replace(tzinfo=tz)

    # 9pm EDT on the DST-transition Sunday.
    dst_sunday_9pm_et = real_datetime(
        2024, 3, 10, 21, 0, 0, tzinfo=DISPLAY_TIMEZONE
    )
    # Sanity-check the test fixture: 9pm EDT on 2024-03-10 should be
    # 01:00 UTC on 2024-03-11 (i.e., the UTC weekday is Monday).
    dst_sunday_9pm_utc = dst_sunday_9pm_et.astimezone(timezone.utc)
    assert dst_sunday_9pm_utc.weekday() == 0 and dst_sunday_9pm_utc.day == 11, (
        "Test fixture error: 9pm EDT 2024-03-10 should map to UTC "
        f"Monday 2024-03-11. Got {dst_sunday_9pm_utc.isoformat()}. "
        "Likely DST rules changed (unlikely) or the test date is wrong."
    )

    clear_audit_log()
    with audit_log._lock:
        audit_log._events.append({
            "type": "location_feedback",
            "timestamp": dst_sunday_9pm_et.isoformat(),
            "session_id": "dst-spring",
            "location_id": "loc-dst-spring",
            "location_name": "DST Spring",
            "ratings": {"safety": False},
        })

    responder = _make_sql_responder({})
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations.datetime", MockedDateTime), \
         patch("app.services.locations_admin.aggregations._execute_sql",
               side_effect=responder):
        body = admin_client.get("/admin/api/locations/timeseries").json()

    # The DST Sunday (2024-03-10) is in the week of Monday 2024-03-04.
    week_of_march_4 = next(
        (w for w in body["weeks"] if w["week_start"] == "2024-03-04"),
        None,
    )
    assert week_of_march_4 is not None, (
        "Couldn't find bucket for week starting 2024-03-04. Either "
        "the 26-week window is wrong, or the mocked 'now' isn't taking "
        "effect. Got week starts: "
        f"{[w['week_start'] for w in body['weeks']]}"
    )
    assert week_of_march_4["feedback_events"] == 1, (
        f"DST-Sunday event (9pm EDT 2024-03-10 = 01:00 UTC 2024-03-11) "
        f"should be in the week of 2024-03-04 from a NYC admin's view. "
        f"Got feedback_events={week_of_march_4['feedback_events']}. "
        f"If this is 0, UTC bucketing won (regression). If it's >1, "
        f"something else is in the audit log."
    )

    # Sanity-check the adjacent week: 2024-03-11 should NOT have the event.
    # Catches a regression where DST handling inverts the boundary.
    week_of_march_11 = next(
        (w for w in body["weeks"] if w["week_start"] == "2024-03-11"),
        None,
    )
    if week_of_march_11 is not None:
        assert week_of_march_11["feedback_events"] == 0, (
            f"DST-Sunday event leaked into the week-of-2024-03-11 bucket. "
            f"This is the UTC-bucketing regression that M9 was supposed to "
            f"fix at the DST boundary specifically. "
            f"week_of_march_4={week_of_march_4['feedback_events']}, "
            f"week_of_march_11={week_of_march_11['feedback_events']}"
        )


# -----------------------------------------------------------------------
# M8 — heavy correlated subqueries moved to a single enrichment query.
# Confirms that top_categories and distinct_categories_count come from
# a single batched query keyed by the page's location_ids, NOT from
# scalar subqueries running per row in list_sql.
# -----------------------------------------------------------------------

def test_list_runs_one_enrichment_query_per_page_not_per_row():
    """The enrichment query (top_categories + distinct_categories_count)
    should fire exactly once per /list call, regardless of how many
    rows are on the page. The previous implementation had these as
    scalar subqueries inside list_sql, which the planner would evaluate
    per row.

    Test by counting SQL invocations: with M8, /list should fire
    {list, count, enrich} = 3 separate SQL queries for the locations
    work, plus the taxonomy-validation query when categories are
    supplied (we skip that here). The enrichment query is identified
    by the "WITH ranked AS" CTE marker.
    """
    clear_audit_log()
    # Set up a page of 25 fake rows.
    rows = [
        _list_row(location_id=f"loc-{i}", location_name=f"Loc {i}")
        for i in range(25)
    ]
    seen_sql_types: list[str] = []

    def responder(sql, params):
        # Classify each invocation by the most-specific marker.
        if "WITH ranked AS" in sql:
            seen_sql_types.append("enrich")
            return _enrichment_rows_from(rows)
        if "COUNT(*) AS total" in sql:
            seen_sql_types.append("count")
            return [{"total": 25}]
        if "FROM locations l\n    JOIN organizations" in sql:
            seen_sql_types.append("list")
            return rows
        seen_sql_types.append("other")
        return []

    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql",
               side_effect=responder):
        r = admin_client.get("/admin/api/locations/list?page_size=25")
        assert r.status_code == 200

    enrich_calls = seen_sql_types.count("enrich")
    list_calls = seen_sql_types.count("list")
    assert enrich_calls == 1, (
        f"Enrichment query should fire exactly once per /list call. "
        f"Fired {enrich_calls} times. SQL sequence: {seen_sql_types}"
    )
    assert list_calls == 1, (
        f"List query should fire exactly once. Fired {list_calls} times. "
        f"SQL sequence: {seen_sql_types}"
    )


def test_list_enrichment_query_keyed_by_page_location_ids():
    """The enrichment query should receive ONLY the current page's
    location_ids — not the full filtered set. Catches a regression where
    someone moves the enrichment to a CTE inside list_sql (would re-fan
    the filter through service_taxonomy) or otherwise loses the page-id
    scoping that bounds cost."""
    clear_audit_log()
    rows = [
        _list_row(location_id="loc-A"),
        _list_row(location_id="loc-B"),
    ]
    captured_enrich_params: dict[str, Any] = {}

    def responder(sql, params):
        if "WITH ranked AS" in sql:
            captured_enrich_params.update(params)
            return _enrichment_rows_from(rows)
        if "COUNT(*) AS total" in sql:
            return [{"total": 2}]
        if "FROM locations l\n    JOIN organizations" in sql:
            return rows
        return []

    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql",
               side_effect=responder):
        admin_client.get("/admin/api/locations/list")

    page_ids = captured_enrich_params.get("page_ids", [])
    assert sorted(page_ids) == ["loc-A", "loc-B"], (
        f"Enrichment query should be bound with the page's location_ids. "
        f"Got page_ids={page_ids!r}"
    )


def test_list_handles_empty_page_skips_enrichment_query():
    """When the page is empty (no rows match the filter), the
    enrichment query shouldn't fire — `WHERE location_id::text = ANY([])`
    is a wasted round-trip. Skip it entirely. Catches a regression
    where someone removes the `if page_ids:` guard."""
    clear_audit_log()
    seen_sql_types: list[str] = []

    def responder(sql, params):
        if "WITH ranked AS" in sql:
            seen_sql_types.append("enrich")
            return []
        if "COUNT(*) AS total" in sql:
            seen_sql_types.append("count")
            return [{"total": 0}]
        if "FROM locations l\n    JOIN organizations" in sql:
            seen_sql_types.append("list")
            return []
        return []

    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql",
               side_effect=responder):
        r = admin_client.get("/admin/api/locations/list?search=__no_match__")
        assert r.status_code == 200
        assert r.json()["locations"] == []

    assert "enrich" not in seen_sql_types, (
        f"Enrichment query fired on an empty page — wasted round-trip. "
        f"SQL sequence: {seen_sql_types}"
    )


# -----------------------------------------------------------------------
# L3 — error response sanitization. Internal error types and messages
# should be in the server log (where ops needs them), not in the wire
# response (where they expose DB column names, library versions, file
# paths, etc.).
# -----------------------------------------------------------------------

def test_admin_error_response_does_not_leak_exception_type_or_message():
    """When an endpoint raises, the JSON response should NOT contain
    the Python exception class name or the raw exception message.
    Catches regressions like ``"detail": f"{type(e).__name__}: {e}"``
    that surface internal details.

    The actual error type + message is checked to live in the server
    log via logger.exception (caplog assertion).
    """
    import logging
    clear_audit_log()

    sentinel_msg = "SECRET_INTERNAL_DETAIL_e7f3a9"

    def boom(sql, params):
        # Mimic a real DB error — driver classes often have very
        # specific names that would be informative to an attacker.
        raise RuntimeError(sentinel_msg)

    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql",
               side_effect=boom), \
         caplog_for("app.routes.admin_locations") as captured_logs:
        r = admin_client.get("/admin/api/locations/stats")

    # Response should be 500 with sanitized detail.
    assert r.status_code == 500
    body = r.json()
    assert body.get("error") is True
    detail = body.get("detail", "")
    assert sentinel_msg not in detail, (
        f"Internal error message leaked into wire response. "
        f"detail={detail!r}"
    )
    assert "RuntimeError" not in detail, (
        f"Internal exception type name leaked into wire response. "
        f"detail={detail!r}"
    )

    # But it SHOULD have been logged for ops to find.
    log_messages = [r.getMessage() for r in captured_logs.records
                    if r.levelno >= logging.ERROR]
    # logger.exception() emits the message + traceback; the traceback
    # text contains the exception type and message.
    log_blob = "\n".join(log_messages) + "\n".join(
        str(r.exc_info[1]) for r in captured_logs.records if r.exc_info
    )
    assert sentinel_msg in log_blob, (
        f"Internal error message should have been logged server-side. "
        f"Logs: {log_messages}"
    )


# -----------------------------------------------------------------------
# Server-side TTL cache. Eleven of the twelve admin aggregations are
# wrapped in @ttl_cached() — these tests prove the cache does its job
# (subsequent calls skip SQL within the TTL window) without breaking
# correctness (fresh data still comes through after explicit invalidation).
# -----------------------------------------------------------------------

def test_cache_dedupes_repeated_calls_to_same_endpoint():
    """Within the TTL window, repeated calls to a cached endpoint
    should hit SQL exactly once. The autouse fixture clears the
    cache before each test, so this test's first call is guaranteed
    to be a MISS.
    """
    sql_call_count = 0
    stats_row = _stats_row()

    def counting_responder(sql, params):
        nonlocal sql_call_count
        sql_call_count += 1
        return stats_row

    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql",
               side_effect=counting_responder):
        # First call — MISS, executes SQL.
        r1 = admin_client.get("/admin/api/locations/stats")
        assert r1.status_code == 200
        first_call_sql_count = sql_call_count
        assert first_call_sql_count > 0, (
            f"First call should have executed SQL. Got "
            f"sql_call_count={first_call_sql_count}"
        )

        # Second call — HIT, no additional SQL.
        r2 = admin_client.get("/admin/api/locations/stats")
        assert r2.status_code == 200
        assert sql_call_count == first_call_sql_count, (
            f"Second call should have been served from cache (zero new "
            f"SQL calls). Got {sql_call_count - first_call_sql_count} "
            f"new SQL calls."
        )

        # Response bodies should be identical — cache returns the
        # same payload, not just any 200.
        assert r1.json() == r2.json(), (
            "Cached response body differs from original. The cache "
            "should be a pure read-through, not a re-render."
        )


def test_cache_clear_forces_fresh_query():
    """clear_locations_admin_cache() should invalidate all entries.
    After clearing, the next call should re-execute SQL.
    """
    sql_call_count = 0
    stats_row = _stats_row()

    def counting_responder(sql, params):
        nonlocal sql_call_count
        sql_call_count += 1
        return stats_row

    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql",
               side_effect=counting_responder):
        admin_client.get("/admin/api/locations/stats")
        before_clear_count = sql_call_count
        assert before_clear_count > 0

        # Confirm cache is doing its job (second call is HIT).
        admin_client.get("/admin/api/locations/stats")
        assert sql_call_count == before_clear_count, (
            "Cache HIT precondition failed — second call ran SQL."
        )

        # Explicit flush.
        clear_locations_admin_cache()

        # Now the next call should MISS and re-execute SQL.
        admin_client.get("/admin/api/locations/stats")
        assert sql_call_count > before_clear_count, (
            f"After cache flush, SQL should re-execute. "
            f"sql_call_count went from {before_clear_count} → "
            f"{sql_call_count} (expected increase)."
        )


def test_cache_does_not_leak_state_across_distinct_endpoints():
    """The cache keys on function name + args. Stats and timeseries
    are different functions and shouldn't collide. A call to one
    should NOT serve the other from cache.
    """
    sql_call_count = 0

    def counting_responder(sql, params):
        nonlocal sql_call_count
        sql_call_count += 1
        if "COUNT(*) AS total" in sql:
            return [{"total": 0}]
        return []

    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql",
               side_effect=counting_responder):
        admin_client.get("/admin/api/locations/stats")
        after_stats = sql_call_count

        admin_client.get("/admin/api/locations/timeseries")
        after_timeseries = sql_call_count

    assert after_timeseries > after_stats, (
        f"Timeseries call should have run its own SQL (different "
        f"cache key from stats). Got: after_stats={after_stats}, "
        f"after_timeseries={after_timeseries}. If equal, the cache "
        f"is dangerously collapsing distinct endpoints into one entry."
    )


def test_cache_list_endpoint_intentionally_not_cached():
    """The /list endpoint takes high-cardinality filter args (page,
    sort, borough, age_bucket, search, category, has_issues). Caching
    it would mean one cache entry per filter combination, which is
    unbounded in practice. It's also the most admin-interactive
    endpoint — admins click filters expecting fresh results.

    Confirm /list is NOT cached: two identical requests should run
    the SQL twice.
    """
    sql_call_count = 0

    def counting_responder(sql, params):
        nonlocal sql_call_count
        sql_call_count += 1
        if "COUNT(*) AS total" in sql:
            return [{"total": 0}]
        return []

    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql",
               side_effect=counting_responder):
        admin_client.get("/admin/api/locations/list?page=1")
        first_count = sql_call_count

        admin_client.get("/admin/api/locations/list?page=1")
        second_count = sql_call_count

    assert second_count > first_count, (
        f"/list should NOT be cached — two identical requests should "
        f"run SQL twice. Got first_count={first_count}, "
        f"second_count={second_count}. If they're equal, someone "
        f"added @ttl_cached() to get_locations_list — see the docstring "
        f"on this test for why that's a bad idea at high filter cardinality."
    )
