"""
Tests for response escalation behaviors.

Covers:
  - Unrecognized service response variation (3 tiers)
  - Negative preference → frustration escalation (3 tiers)

Run with: python -m pytest tests/unit/test_response_escalation.py -v
"""

import pytest
from unittest.mock import patch
from conftest import send_multi, MOCK_QUERY_RESULTS
from app.services.slot_extractor import extract_slots as _regex_extract

# Patch LLM slot extraction to use regex-only, so unrecognized-service
# routing is deterministic (not subject to LLM nondeterminism).
#
# Phase 4 Stage 3 (April 2026): the legacy
# `app.services.llm_slot_extractor.extract_slots_smart` was deleted.
# The unified replacement is `app.services.slot_extraction.extract`,
# which takes `(message, regex_result, **kwargs)` — so the side_effect
# must accept that signature. We ignore the regex_result and
# api_key_available kwargs and return a fresh regex extraction so the
# test stays deterministic regardless of caller-side wiring.
_PATCH_SLOTS = patch(
    "app.services.slot_extraction.extract",
    side_effect=lambda msg, regex_result=None, **kw: _regex_extract(msg),
)


# -----------------------------------------------------------------------
# UNRECOGNIZED SERVICE RESPONSE VARIATION
# -----------------------------------------------------------------------

class TestUnrecognizedServiceTiers:
    """Repeated unrecognized service requests should get varied responses."""

    # All messages use "I need X in Y" to reliably match _SERVICE_NEED_RE
    # and include a location so the unrecognized-service handler fires
    # via the regex path (not LLM).
    _MSGS = [
        "I need a helicopter ride in Staten Island",
        "I need a submarine tour in Staten Island",
        "I need a spaceship launch in Staten Island",
    ]

    @_PATCH_SLOTS
    def test_tier1_standard_redirect(self, _mock):
        r = send_multi([self._MSGS[0]])
        resp = r[0]["response"].lower()
        assert "not sure" in resp or "can search" in resp

    @_PATCH_SLOTS
    def test_tier2_shorter_with_navigator(self, _mock):
        r = send_multi(self._MSGS[:2])
        resp = r[1]["response"].lower()
        assert "limited" in resp
        assert "social services" in resp

    @_PATCH_SLOTS
    def test_tier3_navigator_only(self, _mock):
        r = send_multi(self._MSGS[:3])
        resp = r[2]["response"].lower()
        assert "navigator" in resp

    @_PATCH_SLOTS
    def test_responses_all_different(self, _mock):
        r = send_multi(self._MSGS[:3])
        assert r[0]["response"][:30] != r[1]["response"][:30]
        assert r[1]["response"][:30] != r[2]["response"][:30]

    @_PATCH_SLOTS
    def test_tier3_shorter_than_tier1(self, _mock):
        r = send_multi(self._MSGS[:3])
        assert len(r[2]["response"]) < len(r[0]["response"])

    @_PATCH_SLOTS
    def test_tier1_has_service_buttons(self, _mock):
        """First unrecognized should offer service category buttons."""
        r = send_multi([self._MSGS[0]])
        qr_labels = [q["label"] for q in r[0].get("quick_replies", [])]
        assert len(qr_labels) >= 2

    @_PATCH_SLOTS
    def test_tier3_has_navigator_only(self, _mock):
        """Third+ unrecognized should only offer navigator."""
        r = send_multi(self._MSGS[:3])
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
