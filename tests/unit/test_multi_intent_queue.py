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
from conftest import send_multi


# -----------------------------------------------------------------------
# EXTRACTION VERIFICATION
# -----------------------------------------------------------------------

class TestCrossBoroughExtraction:
    """Verify multi-intent extraction for cross-borough requests."""

    def test_food_brooklyn_shelter_manhattan(self):
        """Cross-borough extraction: Housing First makes shelter primary
        (at Manhattan), food goes to additional (at Brooklyn). The key
        invariant is that the location travels with the correct service."""
        s = extract_slots("I need food in Brooklyn and shelter in Manhattan")
        # Shelter (tier 1) wins primary over food (tier 2) — Feature B
        assert s["service_type"] == "shelter"
        assert s["location"] == "manhattan"
        assert len(s["additional_services"]) == 1
        assert s["additional_services"][0][0] == "food"
        assert s["additional_services"][0][2] == "brooklyn"

    def test_same_borough_multi_intent(self):
        """Both services requested in one borough. Shelter primary by
        priority, food queued, shared location."""
        s = extract_slots("I need food and shelter in Brooklyn")
        assert s["service_type"] == "shelter"
        assert s["location"] == "brooklyn"
        assert len(s["additional_services"]) == 1
        assert s["additional_services"][0][0] == "food"


# -----------------------------------------------------------------------
# CROSS-BOROUGH QUEUE HANDLING
# -----------------------------------------------------------------------

class TestCrossBoroughQueueOffer:
    """Cross-borough services should remain queued, not co-located."""

    def test_queue_offer_fires_for_cross_borough(self):
        """After shelter/Manhattan results, food/Brooklyn should be offered.

        Housing First (Feature B): shelter (tier 1) is primary at its
        mentioned location (Manhattan); food goes to the queue with its
        own location (Brooklyn) and the offer surfaces after results.
        """
        r = send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
        ])
        resp = r[1]["response"].lower()
        assert "also mentioned" in resp
        assert "brooklyn" in resp

    def test_queue_offer_includes_service_name(self):
        r = send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
        ])
        resp = r[1]["response"].lower()
        # food is the queued service under Housing First
        assert "food" in resp

    def test_queue_offer_has_yes_no_buttons(self):
        r = send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
        ])
        qr = r[1].get("quick_replies", [])
        labels = [q["label"].lower() for q in qr]
        has_yes = any("yes" in lable for lable in labels)
        has_no = any("no" in lable for lable in labels)
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
