"""
Tests for response escalation behaviors.

Covers:
  - Unrecognized service response variation (3 tiers)
  - Negative preference → frustration escalation (3 tiers)

Run with: python -m pytest tests/unit/test_response_escalation.py -v
"""

import pytest
from conftest import send_multi, MOCK_QUERY_RESULTS


# -----------------------------------------------------------------------
# UNRECOGNIZED SERVICE RESPONSE VARIATION
# -----------------------------------------------------------------------

class TestUnrecognizedServiceTiers:
    """Repeated unrecognized service requests should get varied responses."""

    def test_tier1_standard_redirect(self):
        r = send_multi(["I need a helicopter ride in Staten Island"])
        resp = r[0]["response"].lower()
        assert "not sure" in resp or "can search" in resp

    def test_tier2_shorter_with_navigator(self):
        r = send_multi([
            "I need a helicopter ride in Staten Island",
            "I still need a helicopter ride",
        ])
        resp = r[1]["response"].lower()
        assert "limited" in resp
        assert "social services" in resp

    def test_tier3_navigator_only(self):
        r = send_multi([
            "I need a helicopter ride in Staten Island",
            "I still need a helicopter ride",
            "helicopter ride please",
        ])
        resp = r[2]["response"].lower()
        assert "navigator" in resp

    def test_responses_all_different(self):
        r = send_multi([
            "I need a helicopter ride in Staten Island",
            "I still need a helicopter ride",
            "helicopter ride please",
        ])
        assert r[0]["response"][:30] != r[1]["response"][:30]
        assert r[1]["response"][:30] != r[2]["response"][:30]

    def test_tier3_shorter_than_tier1(self):
        r = send_multi([
            "I need a helicopter ride in Staten Island",
            "I still need a helicopter ride",
            "helicopter ride please",
        ])
        assert len(r[2]["response"]) < len(r[0]["response"])

    def test_tier1_has_service_buttons(self):
        """First unrecognized should offer service category buttons."""
        r = send_multi(["I need a helicopter ride in Staten Island"])
        qr_labels = [q["label"] for q in r[0].get("quick_replies", [])]
        assert len(qr_labels) >= 2

    def test_tier3_has_navigator_only(self):
        """Third+ unrecognized should only offer navigator."""
        r = send_multi([
            "I need a helicopter ride in Staten Island",
            "I still need a helicopter ride",
            "helicopter ride please",
        ])
        qr = r[2].get("quick_replies", [])
        labels = [q["label"].lower() for q in qr]
        assert any("navigator" in lable for lable in labels)


# -----------------------------------------------------------------------
# NEGATIVE PREFERENCE → FRUSTRATION ESCALATION
# -----------------------------------------------------------------------

class TestNegativePreferenceFrustrationTiers:
    """Repeated negative preferences should escalate through frustration tiers."""

    def test_first_negative_standard(self):
        """First negative preference gets the standard response."""
        r = send_multi([
            "I need food in Brooklyn", "Yes, search",
            "those are not helpful",
        ])
        resp = r[2]["response"].lower()
        assert "aren't what you need" in resp

    def test_second_negative_tier2(self):
        """Second negative preference escalates to tier 2 (navigator + 311)."""
        r = send_multi([
            "I need food in Brooklyn", "Yes, search",
            "those are not helpful",
            "none of those help me",
        ])
        resp = r[3]["response"].lower()
        assert "navigator" in resp
        assert "311" in resp or "start over" in resp.lower()

    def test_third_negative_tier3(self):
        """Third negative preference escalates to tier 3 (direct connect)."""
        r = send_multi([
            "I need food in Brooklyn", "Yes, search",
            "those are not helpful",
            "none of those help me",
            "still not what I need",
        ])
        resp = r[4]["response"].lower()
        assert "connect" in resp
        assert "navigator" in resp

    def test_tier2_response_different_from_tier1(self):
        r = send_multi([
            "I need food in Brooklyn", "Yes, search",
            "those are not helpful",
            "none of those help me",
        ])
        assert r[2]["response"][:30] != r[3]["response"][:30]

    def test_frustration_then_negative_accumulates(self):
        """Explicit frustration followed by negative preference should accumulate."""
        r = send_multi([
            "I need food in Brooklyn", "Yes, search",
            "this is so frustrating",       # frustration → count=1
            "those are not helpful",        # negative_pref → count=2 → tier 2
        ])
        resp = r[3]["response"].lower()
        # Should be tier 2 since total frustration count is now 2
        assert "navigator" in resp
