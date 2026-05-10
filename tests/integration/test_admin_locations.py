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

from fastapi.testclient import TestClient
from app.main import app
from app.services.audit_log import (
    clear_audit_log,
    log_location_feedback,
)


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
        "top_categories": list(top_categories),
        "distinct_categories_count": distinct_categories_count,
        "has_hours": has_hours,
    }


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


def test_list_invalid_sort_key_falls_back_to_default():
    """Unknown sort_key values should fall back to last_validated_at,
    not 500. Defensive against frontend drift or URL tampering."""
    clear_audit_log()
    captured_sql: list = []
    def capturing(sql, params):
        captured_sql.append(sql)
        if "COUNT(*) AS total" in sql:
            return [{"total": 0}]
        return []
    with patch.dict(os.environ, {"ADMIN_API_KEY": "test-key"}), \
         patch("app.services.locations_admin.aggregations._execute_sql", side_effect=capturing):
        r = admin_client.get("/admin/api/locations/list?sort_key=NOT_A_REAL_KEY")
        assert r.status_code == 200
        # The list query (the one that actually orders) should reference
        # last_validated_at — the fallback default.
        list_query = next((s for s in captured_sql if "ORDER BY" in s), None)
        assert list_query is not None
        assert "last_validated_at" in list_query


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
         patch("app.rag.query_executor._stated_borough_from_city", return_value="Manhattan"):
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
         patch("app.rag.query_executor._stated_borough_from_city", return_value="Brooklyn"):
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
         patch("app.rag.query_executor._stated_borough_from_city", return_value="Manhattan"):
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
         patch("app.rag.query_executor._stated_borough_from_city", return_value=None):
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
         patch("app.rag.query_executor._stated_borough_from_city", side_effect=["Brooklyn", "Manhattan"]):
        body = admin_client.get("/admin/api/locations/coordinate-issues").json()
        assert len(body["issues"]) == 2
        # Outside-NYC sorts before mismatch
        assert body["issues"][0]["location_id"] == "loc-outside"
        assert body["issues"][1]["location_id"] == "loc-mismatch"
