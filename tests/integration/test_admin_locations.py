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
