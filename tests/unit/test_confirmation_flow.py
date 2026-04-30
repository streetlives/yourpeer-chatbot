"""
Tests for confirmation flow changes.

Covers:
  - confirm_change_service expanded phrase patterns
  - confirm_deny + new service intent interception
  - Contradiction auto-execute during pending confirmation

Run with: python -m pytest tests/unit/test_confirmation_flow.py -v
"""

import pytest
from app.services.classifier import _classify_action
from conftest import send_multi, MOCK_QUERY_RESULTS


# -----------------------------------------------------------------------
# CONFIRM CHANGE SERVICE PATTERNS
# -----------------------------------------------------------------------

class TestConfirmChangeServicePatterns:
    """Expanded patterns should classify as confirm_change_service."""

    @pytest.mark.parametrize("phrase", [
        "change to shelter",
        "switch to clothing",
        "can i change to food",
        "can we do shelter",
        "i want to switch",
        "let me change",
        "i'd rather have",
    ])
    def test_new_pattern(self, phrase):
        assert _classify_action(phrase) == "confirm_change_service"

    @pytest.mark.parametrize("phrase", [
        "change service", "different service", "wrong service",
        "change what i need", "change service type",
    ])
    def test_existing_patterns_unchanged(self, phrase):
        assert _classify_action(phrase) == "confirm_change_service"

    def test_during_pending_confirmation(self):
        """'Change to shelter' during pending should clear and re-prompt."""
        r = send_multi(["I need food in Brooklyn", "change to shelter"])
        # Should either show new confirmation for shelter, or prompt for service
        resp = r[1]["response"].lower()
        has_shelter = "shelter" in resp
        has_prompt = "what kind" in resp or "what do you need" in resp
        assert has_shelter or has_prompt

    def test_pending_change_service_sets_awaiting_clear_flag(self):
        """Regression guard for `confirm_multi_change` (R36 unified 3.55).

        The `_awaiting_service_after_clear` flag tells the orchestrator's
        service-extraction stage to trust the regex on the next turn and
        skip the LLM, so that a bare "Shelter" reply doesn't get
        mis-extracted by the LLM as `{service_type: food (stale from
        history), additional: [shelter]}` — that mis-extraction was the
        root cause of the confirmation displaying "food and shelter"
        instead of just "shelter" after a "Change service" turn.

        Both change-service code paths must set this flag:
          1. `_handle_change_service_request` — standalone, fires when
             there is no pending confirmation (rare).
          2. The `confirm_change_service` branch inside
             `_handle_pending_confirmation` — fires when there IS a
             pending confirmation (the common path, and the one
             `confirm_multi_change` exercises).

        Path 1 always set the flag; path 2 did not, and the two drifted
        out of sync. This test pins path 2's behavior.
        """
        import uuid
        from app.services.session_store import get_session_slots
        sid = f"test-{uuid.uuid4().hex[:8]}"
        # Turn 1 establishes pending_confirmation=True.
        # Turn 2 routes through _handle_pending_confirmation's
        # confirm_change_service branch (not the standalone handler).
        send_multi([
            "I need food in Brooklyn",
            "Change service",
        ], session_id=sid)
        slots = get_session_slots(sid)
        assert slots.get("service_type") is None, (
            "Change service should clear service_type"
        )
        assert slots.get("_pending_confirmation") is None, (
            "Change service should clear pending confirmation"
        )
        assert slots.get("_awaiting_service_after_clear") is True, (
            "Change service (in pending path) must set "
            "_awaiting_service_after_clear=True so the next-turn LLM "
            "guard engages. Without it, a bare 'Shelter' reply next "
            "turn hits the LLM with stale food-in-history and gets "
            "mis-extracted as {service_type: food, additional: "
            "[shelter]} — the confirm_multi_change failure shape."
        )

    def test_pending_change_service_flag_recovers_scenario(self):
        """End-to-end regression: the confirm_multi_change scenario flow.
        Turn 3's 'Shelter' must cleanly replace service_type, not
        append to it. The awaiting-clear flag set on turn 2 is what
        makes this work under the unified extractor path (where the
        LLM's stale-history mis-extraction would otherwise stick)."""
        import uuid
        from app.services.session_store import get_session_slots
        sid = f"test-{uuid.uuid4().hex[:8]}"
        send_multi([
            "I need food in Brooklyn",
            "Change service",
            "Shelter",
        ], session_id=sid)
        slots = get_session_slots(sid)
        assert slots.get("service_type") == "shelter", (
            f"After Change service → Shelter, service_type should be "
            f"'shelter' (replaced), not food-with-shelter-added. Got "
            f"{slots.get('service_type')!r}."
        )
        # Note: the `_awaiting_service_after_clear` flag is consumed by
        # the orchestrator's service-extraction guard at
        # orchestrator.py:445, but only when `_USE_LLM` is True (i.e.
        # when an `ANTHROPIC_API_KEY` is set). In the test environment
        # `_USE_LLM` is False so the guard never runs — the flag stays
        # harmlessly set. This doesn't affect correctness since the
        # regex path gives the same result as the guard.


# -----------------------------------------------------------------------
# CONFIRM DENY + NEW SERVICE INTENT
# -----------------------------------------------------------------------

class TestConfirmDenyServiceSwitch:
    """Denial messages containing a new service intent should switch, not deny."""

    def test_changed_mind_shelter(self):
        r = send_multi(["I need food in Brooklyn", "I changed my mind, shelter"])
        assert "shelter" in r[1]["response"].lower()
        assert "hold onto" not in r[1]["response"].lower()

    def test_nah_clothing(self):
        r = send_multi(["I need food in Brooklyn", "nah, I need clothing"])
        resp = r[1]["response"].lower()
        assert "clothing" in resp or r[1]["slots"].get("service_type") == "clothing"

    def test_pure_denial_no_service(self):
        """'no thanks' without a service keyword remains a denial."""
        r = send_multi(["I need food in Brooklyn", "no thanks"])
        resp = r[1]["response"].lower()
        assert "hold onto" in resp or "what would you like" in resp

    def test_switch_preserves_location(self):
        r = send_multi(["I need food in Brooklyn", "I changed my mind, shelter"])
        assert r[1]["slots"].get("location") is not None

    def test_switch_with_new_location(self):
        """Denial + new service + new location."""
        r = send_multi(["I need food in Brooklyn", "no, shelter in Manhattan"])
        resp = r[1]["response"].lower()
        assert "shelter" in resp
        assert "manhattan" in resp or r[1]["slots"].get("location") == "manhattan"

    def test_same_service_stays_denial(self):
        """'nah, food' (same service) should be a denial, not a switch."""
        r = send_multi(["I need food in Brooklyn", "nah, food"])
        # Same service → pure denial
        resp = r[1]["response"].lower()
        assert "hold onto" in resp or "what would you like" in resp


# -----------------------------------------------------------------------
# CONTRADICTION AUTO-EXECUTE
# -----------------------------------------------------------------------

class TestContradictionAutoExecute:
    """Contradicting a slot during pending confirmation should auto-execute."""

    def test_service_change_auto_executes(self):
        r = send_multi(["I need food in Brooklyn", "actually shelter in Queens"])
        assert r[1]["result_count"] >= 1
        resp = r[1]["response"].lower()
        assert "switching" in resp or "got it" in resp

    def test_location_change_auto_executes(self):
        r = send_multi(["I need food in Brooklyn", "actually Manhattan"])
        assert r[1]["result_count"] >= 1

    def test_both_changed_auto_executes(self):
        r = send_multi(["I need food in Brooklyn", "actually shelter in Manhattan"])
        assert r[1]["result_count"] >= 1
        assert r[1]["slots"]["service_type"] == "shelter"

    def test_new_slot_no_contradiction_reconfirms(self):
        """Adding age (not contradicting) should re-confirm, not auto-execute."""
        r = send_multi(["I need food in Brooklyn", "I'm 25"])
        assert r[1]["follow_up_needed"] is True or r[1]["result_count"] == 0

    def test_same_value_not_contradiction(self):
        """Repeating the same service type is not a contradiction."""
        r = send_multi(["I need food in Brooklyn", "yes, food"])
        assert r[1]["result_count"] >= 1

    def test_auto_execute_has_prefix(self):
        r = send_multi(["I need food in Brooklyn", "actually shelter in Queens"])
        resp = r[1]["response"].lower()
        assert "got it" in resp or "switching" in resp

    def test_full_change_mind_flow(self):
        """User asks food, changes to shelter, gets results."""
        r = send_multi([
            "I need food in Brooklyn",
            "actually, I need shelter",
        ])
        assert r[1]["result_count"] >= 1
        assert r[1]["slots"]["service_type"] == "shelter"
