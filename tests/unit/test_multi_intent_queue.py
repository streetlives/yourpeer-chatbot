"""
Tests for multi-intent queue handling, particularly cross-borough requests.

Covers:
  - Cross-borough queue: different-location services stay queued
  - Same-borough co-location still works
  - Queue offer text includes correct location

Run with: python -m pytest tests/unit/test_multi_intent_queue.py -v
"""

import pytest
from app.services.slot_extractor import extract_slots
from conftest import send_multi, MOCK_QUERY_RESULTS


# -----------------------------------------------------------------------
# EXTRACTION VERIFICATION
# -----------------------------------------------------------------------

class TestCrossBoroughExtraction:
    """Verify multi-intent extraction for cross-borough requests."""

    def test_food_brooklyn_shelter_manhattan(self):
        s = extract_slots("I need food in Brooklyn and shelter in Manhattan")
        assert s["service_type"] == "food"
        assert s["location"] == "brooklyn"
        assert len(s["additional_services"]) == 1
        assert s["additional_services"][0][0] == "shelter"
        assert s["additional_services"][0][2] == "manhattan"

    def test_same_borough_multi_intent(self):
        s = extract_slots("I need food and shelter in Brooklyn")
        assert s["service_type"] == "food"
        assert s["location"] == "brooklyn"
        assert len(s["additional_services"]) == 1
        assert s["additional_services"][0][0] == "shelter"


# -----------------------------------------------------------------------
# CROSS-BOROUGH QUEUE HANDLING
# -----------------------------------------------------------------------

class TestCrossBoroughQueueOffer:
    """Cross-borough services should remain queued, not co-located."""

    def test_queue_offer_fires_for_cross_borough(self):
        """After food/Brooklyn results, shelter/Manhattan should be offered."""
        r = send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
        ])
        resp = r[1]["response"].lower()
        assert "also mentioned" in resp
        assert "manhattan" in resp

    def test_queue_offer_includes_service_name(self):
        r = send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
        ])
        resp = r[1]["response"].lower()
        assert "shelter" in resp

    def test_queue_offer_has_yes_no_buttons(self):
        r = send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
        ])
        qr = r[1].get("quick_replies", [])
        labels = [q["label"].lower() for q in qr]
        has_yes = any("yes" in l for l in labels)
        has_no = any("no" in l for l in labels)
        assert has_yes and has_no

    def test_results_still_returned_for_primary(self):
        """Primary service should still return results."""
        r = send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
        ])
        assert r[1]["result_count"] >= 1


class TestSameBoroughColocation:
    """Same-borough multi-intent should still attempt co-location."""

    def test_same_borough_gets_results(self):
        r = send_multi([
            "I need food and shelter in Brooklyn",
            "Yes, search",
        ])
        assert r[1]["result_count"] >= 1

    def test_same_borough_confirmation_mentions_both(self):
        """Confirmation should mention both services."""
        r = send_multi(["I need food and shelter in Brooklyn"])
        resp = r[0]["response"].lower()
        assert "food" in resp
        assert "shelter" in resp
