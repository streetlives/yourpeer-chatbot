"""
Tests for post-results state handling.

These tests prevent regressions for two bugs found in Run 25:
  1. "Yes, search" after results re-executed the same query (duplicate results)
  2. "nah I'm good" after results re-triggered food confirmation instead of declining
  3. Auto-execute urgent skipped confirmation (reverted in this PR)

Run with: python -m pytest tests/unit/test_post_results_state.py -v
"""

import pytest
from conftest import send_multi, MOCK_QUERY_RESULTS


# -----------------------------------------------------------------------
# CONFIRM_YES AFTER RESULTS (Pattern B from R25 regression)
# -----------------------------------------------------------------------

class TestConfirmYesAfterResults:
    """'Yes, search' after results should NOT re-execute the same query."""

    def test_yes_search_after_results_no_reexecute(self):
        r = send_multi(["I need food in Brooklyn", "Yes, search", "Yes, search"])
        assert r[2]["result_count"] == 0, "Re-executed same query!"
        assert "already shown" in r[2]["response"].lower()

    def test_yes_after_results_clears_last_results(self):
        """After acknowledging, _last_results should be cleared."""
        r = send_multi(["I need food in Brooklyn", "Yes, search", "Yes, search"])
        assert r[2]["slots"].get("_last_results") is None

    def test_yes_after_results_offers_new_search(self):
        r = send_multi(["I need food in Brooklyn", "Yes, search", "Yes, search"])
        qr = [q["label"].lower() for q in r[2].get("quick_replies", [])]
        assert any("new search" in lable or "start over" in lable for lable in qr)

    def test_yes_after_results_no_infinite_loop(self):
        """Multiple 'Yes, search' should never re-execute."""
        r = send_multi([
            "I need food in Brooklyn", "Yes, search",
            "Yes, search", "Yes, search",
        ])
        for i in [2, 3]:
            assert r[i]["result_count"] == 0

    def test_yes_after_results_then_new_service(self):
        """After acknowledging, user should be able to start a new search."""
        r = send_multi([
            "I need food in Brooklyn", "Yes, search",
            "Yes, search",        # acknowledge
            "I need shelter",     # new service
        ])
        # T4 should start new search flow, not repeat old results
        assert "shelter" in r[3]["response"].lower()

    def test_yes_after_colocated_results(self):
        """Co-located results + 'Yes, search' should not re-execute."""
        r = send_multi([
            "I need food and shelter in Brooklyn",
            "Yes, search",
            "Yes, search",
        ])
        assert r[2]["result_count"] == 0
        assert "already shown" in r[2]["response"].lower()


# -----------------------------------------------------------------------
# CONFIRM_DENY AFTER RESULTS (Pattern C from R25 regression)
# -----------------------------------------------------------------------

class TestConfirmDenyAfterResults:
    """Decline phrases after results should gracefully end, not re-search."""

    @pytest.mark.parametrize("decline", [
        "no thanks", "nah", "nope",
        "nah I'm good", "I'm good", "im good",
        "all good", "no need",
        "I'm fine", "im fine", "no I'm fine", "no im fine",
    ])
    def test_decline_after_results_is_graceful(self, decline):
        r = send_multi(["I need food in Brooklyn", "Yes, search", decline])
        resp = r[2]["response"].lower()
        assert "no problem" in resp or "let me know" in resp, \
            f'Expected graceful decline, got: {r[2]["response"][:80]}'

    @pytest.mark.parametrize("decline", [
        "no thanks", "nah I'm good", "I'm fine",
    ])
    def test_decline_after_results_no_reexecute(self, decline):
        r = send_multi(["I need food in Brooklyn", "Yes, search", decline])
        assert r[2]["result_count"] == 0, f"Re-executed after '{decline}'!"

    def test_decline_after_results_clears_state(self):
        r = send_multi(["I need food in Brooklyn", "Yes, search", "nah I'm good"])
        assert r[2]["slots"].get("_last_results") is None

    def test_decline_after_results_shows_service_menu(self):
        r = send_multi(["I need food in Brooklyn", "Yes, search", "no thanks"])
        qr = r[2].get("quick_replies", [])
        assert len(qr) >= 2, "Should show service menu options"

    def test_decline_after_colocated_results(self):
        r = send_multi([
            "I need food and shelter in Brooklyn",
            "Yes, search",
            "no thanks",
        ])
        resp = r[2]["response"].lower()
        assert "no problem" in resp or "let me know" in resp

    def test_decline_does_not_fire_during_pending(self):
        """'no thanks' during pending confirmation should deny, not post-results."""
        r = send_multi(["I need food in Brooklyn", "no thanks"])
        # Should be standard deny (hold onto info), not post-results decline
        resp = r[1]["response"].lower()
        assert "hold onto" in resp or "what would you like" in resp


# -----------------------------------------------------------------------
# QUEUE OFFER DECLINE (separate from post-results decline)
# -----------------------------------------------------------------------

class TestQueueOfferDecline:
    """Decline after queue offer should clear queue, not re-search."""

    @pytest.mark.parametrize("decline", [
        "No thanks", "nah", "nah I'm good", "no I'm fine",
    ])
    def test_decline_queue_offer_cross_borough(self, decline):
        r = send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
            decline,
        ])
        resp = r[2]["response"].lower()
        assert "no problem" in resp or "let me know" in resp
        assert "search for food" not in resp, "Re-searched instead of declining!"

    def test_accept_queue_offer(self):
        """'Yes' after queue offer should search the queued service."""
        r = send_multi([
            "I need food in Brooklyn and shelter in Manhattan",
            "Yes, search",
            "I need shelter in Manhattan",  # accept queue
        ])
        assert "shelter" in r[2]["response"].lower()


# -----------------------------------------------------------------------
# AUTO-EXECUTE REVERT REGRESSION GUARD
# -----------------------------------------------------------------------

class TestAutoExecuteReverted:
    """Urgent requests must always go through confirmation.

    This guards against re-introducing the Gap 16 auto-execute feature
    without proper safeguards. In Run 25, auto-execute caused 15 scenario
    regressions by skipping confirmation and causing 'Yes, search' to
    re-execute the same query."""

    @pytest.mark.parametrize("msg", [
        "I need shelter tonight in Brooklyn",
        "I need a bed right now in Manhattan",
        "I need food immediately in Queens",
        "I need somewhere to sleep tonight in the Bronx",
        "urgent, need shelter in Brooklyn",
    ])
    def test_urgent_request_shows_confirmation(self, msg):
        r = send_multi([msg])
        assert r[0]["result_count"] == 0, f"Auto-executed without confirmation for: {msg}"
        assert r[0]["follow_up_needed"] is True

    def test_urgent_then_confirm_delivers_results(self):
        r = send_multi(["I need shelter tonight in Brooklyn", "Yes, search"])
        assert r[0]["result_count"] == 0, "T1 should be confirmation"
        assert r[1]["result_count"] >= 1, "T2 should deliver results"

    def test_urgent_has_empathetic_tone(self):
        """Urgent requests should still get empathetic prefix."""
        r = send_multi(["I need shelter tonight in Brooklyn"])
        resp = r[0]["response"].lower()
        assert "urgent" in resp or "right away" in resp

    def test_non_urgent_also_confirms(self):
        """Non-urgent requests also go through confirmation."""
        r = send_multi(["I need food in Manhattan"])
        assert r[0]["result_count"] == 0
        assert r[0]["follow_up_needed"] is True

    def test_no_duplicate_results_after_confirm(self):
        """After confirming, 'Yes, search' again should NOT duplicate."""
        r = send_multi([
            "I need shelter tonight in Brooklyn",
            "Yes, search",
            "Yes, search",
        ])
        assert r[1]["result_count"] >= 1, "T2 should deliver results"
        assert r[2]["result_count"] == 0, "T3 should NOT re-execute"
        assert "already shown" in r[2]["response"].lower()
