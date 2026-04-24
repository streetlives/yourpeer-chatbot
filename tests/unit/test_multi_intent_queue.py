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


# -----------------------------------------------------------------------
# R34 SPRINT 1 — THREE-BUG FIX FOR CROSS-LOCATION MULTI-INTENT
# -----------------------------------------------------------------------

class TestCrossLocationConfirmationWording:
    """Bug 1: confirmation message must surface BOTH locations when
    queued services have distinct locations from the primary.

    Pre-fix, "food in Brooklyn and shelter in Manhattan" rendered as
    "I'll look for shelter and food in Manhattan" — dropping Brooklyn
    entirely and silently misrepresenting the user's request.
    """

    def test_cross_borough_confirmation_surfaces_both_locations(self):
        r = send_multi(["I need food in Brooklyn and shelter in Manhattan"])
        resp = r[0]["response"].lower()
        # Primary location must appear
        assert "manhattan" in resp
        # Queued location must ALSO appear — the pre-fix bug dropped it
        assert "brooklyn" in resp

    def test_cross_borough_confirmation_does_not_conflate_services(self):
        """Queued cross-location service must not be folded into the
        primary's location. Pre-fix rendered 'shelter and food in
        Manhattan' — conflation that dropped Brooklyn."""
        r = send_multi(["I need food in Brooklyn and shelter in Manhattan"])
        resp = r[0]["response"].lower()
        # "food in Manhattan" is the exact string the pre-fix produced.
        # The post-fix string says "food in Brooklyn" separately.
        assert "food in manhattan" not in resp

    def test_cross_neighborhood_confirmation_surfaces_both_locations(self):
        r = send_multi([
            "I want to shower in the Lower East Side and grab food in Chinatown"
        ])
        resp = r[0]["response"].lower()
        assert "chinatown" in resp
        assert "lower east side" in resp

    def test_same_location_multi_intent_still_folds(self):
        """Regression guard: same-location queued services should STILL
        fold into the combined label (pre-fix co-location behavior
        preserved for the same-location case)."""
        r = send_multi(["I need food and shelter in Brooklyn"])
        resp = r[0]["response"].lower()
        # Both services mentioned, single location
        assert "food" in resp and "shelter" in resp
        assert "brooklyn" in resp
        # Should NOT double-mention Brooklyn (no cross-location split)
        assert resp.count("brooklyn") == 1


class TestQueuedOfferSlotPersistence:
    """Bug 2: _apply_queue_offer must save the offered item's full tuple
    in _queued_offer so a typed 'yes' (not button-click) can reconstruct
    the promoted search."""

    def test_queued_offer_slot_set_after_primary_search(self):
        """After the primary search returns results and queue offer
        fires, _queued_offer should contain the full offered tuple."""
        import uuid
        from app.services.session_store import get_session_slots
        sid = f"test-{uuid.uuid4().hex[:8]}"
        send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
        ], session_id=sid)
        slots = get_session_slots(sid)
        assert slots.get("_queue_offer_pending") is True
        # _queued_offer is the persisted full tuple of what was offered
        offer = slots.get("_queued_offer")
        assert offer is not None, (
            "_queued_offer must be persisted so typed 'yes' can "
            "reconstruct the promoted search; without it the yes "
            "falls through to a stale primary confirmation"
        )
        assert offer[0] == "food"
        assert offer[2] == "brooklyn"

    def test_queued_offer_cleared_after_decline(self):
        """No to queue offer clears the _queued_offer slot."""
        import uuid
        from app.services.session_store import get_session_slots
        sid = f"test-{uuid.uuid4().hex[:8]}"
        send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
            "No thanks",
        ], session_id=sid)
        slots = get_session_slots(sid)
        assert slots.get("_queue_offer_pending") is None
        assert slots.get("_queued_offer") is None


class TestQueueOfferLocationDisplay:
    """The queue-offer message ('You also mentioned X in Y...') should
    display-case the queued location the same way the primary
    confirmation does. Pre-fix, the primary said 'Lower East Side'
    (display-cased) while the queue-offer said 'lower east side' (raw
    lowercase from the slot extractor) — cosmetic but jarring."""

    def test_queue_offer_location_is_display_cased(self):
        """Queue-offer shows 'Brooklyn', not 'brooklyn'."""
        r = send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
        ])
        resp = r[1]["response"]
        # Find the "also mentioned" line
        assert "also mentioned" in resp.lower()
        # Display-cased Brooklyn should appear
        assert "Brooklyn" in resp, (
            f"Queue-offer should display-case the queued location. "
            f"Got: {resp!r}"
        )

    def test_queue_offer_location_respects_nyc_casing_overrides(self):
        """NYC-specific casing (Lower East Side, SoHo, etc.) should
        flow through to the queue-offer message via _display_location."""
        r = send_multi([
            "I want to shower in the Lower East Side and grab food in Chinatown",
            "Yes, search",
        ])
        resp = r[1]["response"]
        assert "also mentioned" in resp.lower()
        # Title-cased per _display_location's .title() fallback for
        # names not in _LOCATION_DISPLAY_OVERRIDES.
        assert "Lower East Side" in resp, (
            f"Queue-offer should apply the same display-casing as the "
            f"primary confirmation. Got: {resp!r}"
        )


class TestQueueYesPromotesQueuedService:
    """Bug 3: typed 'yes' to queue offer must promote the queued service
    to primary and execute its search. Pre-fix, yes fell through to
    default handlers which saw stale primary slots still in session and
    rebuilt a primary confirmation — total disconnect from user intent."""

    def test_yes_to_queue_offer_searches_queued_service(self):
        """Three-turn flow: multi-intent → primary results → yes →
        queued-service results. Pre-fix turn 3 re-confirmed primary
        instead of searching queued."""
        r = send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
            "yes",  # accept queue offer
        ])
        # Turn 3 (index 2) should be results for the queued service
        turn3 = r[2]["response"].lower()
        # Post-fix: bot searches and returns results
        assert "found" in turn3 or "option" in turn3, (
            f"Turn 3 should show results for the queued search. Got: {turn3!r}. "
            f"Pre-fix this would say 'I'll look for shelter in Manhattan — sound good?' "
            f"(re-confirming the already-searched primary)."
        )
        # Specifically should NOT re-confirm the primary
        assert "look for shelter" not in turn3, (
            "Turn 3 should NOT re-confirm the primary shelter search — "
            "pre-fix bug symptom. User said yes to FOOD, not shelter."
        )

    def test_yes_to_queue_offer_updates_primary_slots(self):
        """After yes-to-queue, the session's service_type and location
        should reflect the promoted (queued) service, not the stale
        primary that was already searched."""
        import uuid
        from app.services.session_store import get_session_slots
        sid = f"test-{uuid.uuid4().hex[:8]}"
        send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
            "yes",
        ], session_id=sid)
        slots = get_session_slots(sid)
        # Primary slots now reflect the queued (promoted) service
        assert slots.get("service_type") == "food"
        assert slots.get("location") == "brooklyn"
        # Queue state cleared
        assert slots.get("_queue_offer_pending") is None
        assert slots.get("_queued_offer") is None

    def test_cross_neighborhood_yes_also_promotes(self):
        """Same behavior across both cross-location shapes."""
        r = send_multi([
            "I want to shower in the Lower East Side and grab food in Chinatown",
            "Yes, search",
            "yes",
        ])
        turn3 = r[2]["response"].lower()
        assert "found" in turn3 or "option" in turn3
        assert "look for food" not in turn3, (
            "Turn 3 should not re-confirm the primary food search "
            "when user yes'd the queued showers offer."
        )

    def test_no_to_queue_offer_still_declines(self):
        """Regression guard: the pre-existing decline path still works
        after adding the yes case."""
        r = send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
            "No thanks",
        ])
        turn3 = r[2]["response"].lower()
        # Decline message
        assert "no problem" in turn3 or "let me know" in turn3


class TestQueueAcceptServiceMatch:
    """Service-category message that matches the queued offer should be
    treated as queue-accept, not as a new service request.

    The scenario this fixes (multi_accept_queued_shelter in R36): after
    the primary search returns results and the queue offer fires for the
    secondary service, the user either taps the quick-reply button
    ("I need food in Brooklyn") or re-types the service name
    ("I need food"). Both should promote the queued service and run
    the search immediately — same UX as `yes`.

    Pre-fix: these messages fell through to the normal service flow,
    where merge_slots' service-change clearing wiped the queue offer and
    the orchestrator built a fresh confirmation for the "new" search —
    an extra redundant turn and a judge-scored critical failure.

    Note on scenario shapes: these tests use cross-borough inputs
    ("food in Brooklyn and shelter in Manhattan") to force the queue
    path. Same-borough multi-intent falls into co-located search, which
    returns both services at one location and never queues — a
    different code path not exercised here.
    """

    def test_typed_queued_service_promotes_and_searches(self):
        """'I need food' after a food queue offer: promote the
        offer's location (Brooklyn) and run the search. Don't
        re-confirm — the explicit service name IS the acceptance.

        Turn 1 extracts shelter as tier-1 primary with food queued;
        after turn 2's shelter/Manhattan search runs, the queue offer
        fires for food/Brooklyn. Turn 3's 'I need food' matches the
        queued food.
        """
        r = send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
            "I need food",
        ])
        turn3 = r[2]["response"].lower()
        # Bot searches and returns results (not a re-confirmation)
        assert "found" in turn3 or "option" in turn3, (
            f"Turn 3 should run the food search immediately. Got: {turn3!r}. "
            f"Pre-fix, merge_slots' service-change clear wiped the queue "
            f"offer and the orchestrator built a 'look for food in Brooklyn, "
            f"sound right?' confirmation — an extra redundant turn."
        )
        # Specifically should NOT re-confirm the queued service
        assert "sound right" not in turn3 and "sound good" not in turn3, (
            "Turn 3 should NOT re-confirm — user's 'I need food' IS "
            "the acceptance of the queue offer."
        )

    def test_button_click_value_promotes_and_searches(self):
        """Simulates the user tapping the '✅ Yes, search for food'
        quick reply, which sends 'I need food in brooklyn' (the
        value set by _apply_queue_offer). Should be recognized as
        queue-accept, not a fresh search."""
        r = send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
            "I need food in brooklyn",  # The qr_value shape
        ])
        turn3 = r[2]["response"].lower()
        assert "found" in turn3 or "option" in turn3
        assert "sound right" not in turn3 and "sound good" not in turn3

    def test_typed_service_honors_user_location_override(self):
        """If the user re-specifies a different location than the
        queued offer ('I need food in Queens' when the offer
        was for Brooklyn), honor the user's location — they've just
        signaled a location change. The queued service still
        promotes; only the location comes from the user."""
        import uuid
        from app.services.session_store import get_session_slots
        sid = f"test-{uuid.uuid4().hex[:8]}"
        send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
            "I need food in Queens",  # Different location than queued
        ], session_id=sid)
        slots = get_session_slots(sid)
        # Primary slots: queued service promoted, user's location wins
        assert slots.get("service_type") == "food"
        assert slots.get("location") == "queens", (
            f"User's typed location should override the queued "
            f"location; got {slots.get('location')!r}."
        )
        # Queue state cleared
        assert slots.get("_queue_offer_pending") is None
        assert slots.get("_queued_offer") is None

    def test_different_service_falls_through_to_service_flow(self):
        """User types a DIFFERENT service than queued (queue offer is
        for food, user types 'I need clothing'). Queue-accept must
        NOT fire — the normal service flow handles it, which includes
        clearing the stale queue state via merge_slots' service-change
        guard. That's the right behavior here."""
        import uuid
        from app.services.session_store import get_session_slots
        sid = f"test-{uuid.uuid4().hex[:8]}"
        r = send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
            "I need clothing",  # Different service — not the queued food
        ], session_id=sid)
        slots = get_session_slots(sid)
        # Normal service flow: new primary is clothing, queue cleared
        assert slots.get("service_type") == "clothing"
        assert slots.get("_queued_offer") is None
        # Turn 3 should be a confirmation (user's clothing request is new,
        # hasn't been confirmed yet) — not an immediate search.
        turn3 = r[2]["response"].lower()
        # Either confirmation prompt or missing-slot question; NOT a
        # results page yet.
        assert "found" not in turn3, (
            f"Different service should go through normal service flow "
            f"(which asks or confirms), not queue-accept's immediate "
            f"search. Got: {turn3!r}"
        )

    def test_typed_queued_service_updates_session_to_queued(self):
        """After queue-accept via service-typed message, session
        reflects the queued service + its location."""
        import uuid
        from app.services.session_store import get_session_slots
        sid = f"test-{uuid.uuid4().hex[:8]}"
        send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
            "I need food",
        ], session_id=sid)
        slots = get_session_slots(sid)
        # Queued service (food at brooklyn) is now the primary
        assert slots.get("service_type") == "food"
        assert slots.get("location") == "brooklyn"
        # Queue state cleared (same as the confirm_yes path)
        assert slots.get("_queue_offer_pending") is None
        assert slots.get("_queued_offer") is None

    def test_no_queue_offer_service_message_is_normal_flow(self):
        """Regression guard: with no pending queue offer, a service
        message should take the normal service flow (confirmation or
        missing-slot question). Queue-accept must not accidentally
        fire when there's no queue."""
        import uuid
        sid = f"test-{uuid.uuid4().hex[:8]}"
        r = send_multi(["I need shelter in Brooklyn"], session_id=sid)
        turn1 = r[0]["response"].lower()
        # Should reach a confirmation, not an immediate search
        assert "sound right" in turn1 or "sound good" in turn1 or "look for" in turn1, (
            f"Bare service request should confirm, not queue-accept. "
            f"Got: {turn1!r}"
        )
