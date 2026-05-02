"""Tests for ``chatbot.session_helpers``.

Each helper is tested in isolation against an in-memory session
store, exercising every branch and every documented invariant.
Mutation-coverage hot spots (set membership in
``_CONSUMES_LAST_ACTION``, the ``_MAX_TRANSCRIPT`` boundary, the
five-clause AND in ``_update_queued_services``, the asymmetric
save condition in ``_persist_emotional_context_late``) get
multiple targeted tests so each individual mutant has at least one
test that flips on it.

Bug 1 (ORCHESTRATOR_AUDIT.md): the late-emotional-context save
condition is preserved as-is. ``test_late_persist_does_not_save_on_value_to_value_change``
*pins the buggy behavior*. When PR-γ fixes the bug, that test
inverts to assert the save DID happen.
"""

import pytest

from app.services.chatbot import session_helpers
from app.services.chatbot.session_helpers import (
    _MAX_TRANSCRIPT,
    _USER_PROVIDED_SLOTS,
    _append_to_transcript,
    _clear_awaiting_service_after_clear,
    _clear_stale_last_action,
    _consume_last_action,
    _persist_emotional_context_early,
    _persist_emotional_context_late,
    _update_queued_services,
    has_user_content,
)


# ---------------------------------------------------------------------------
# Save-tracking fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def save_recorder(monkeypatch):
    """Record all ``save_session_slots`` calls made by the helpers.

    Returns a list of ``(session_id, slots_snapshot)`` tuples. The
    snapshot is a shallow copy so the test can detect what state was
    persisted, even if the original dict mutates afterward.
    """
    calls = []

    def _save(session_id, slots):
        calls.append((session_id, dict(slots)))

    monkeypatch.setattr(
        "app.services.chatbot.session_helpers.save_session_slots", _save
    )
    return calls


# ===========================================================================
# _append_to_transcript
# ===========================================================================


class TestAppendToTranscript:
    def test_initializes_transcript_when_absent(self):
        merged = {}
        _append_to_transcript(merged, "hello")
        assert merged["transcript"] == [{"role": "user", "text": "hello"}]

    def test_appends_to_existing_transcript(self):
        merged = {"transcript": [{"role": "user", "text": "first"}]}
        _append_to_transcript(merged, "second")
        assert merged["transcript"] == [
            {"role": "user", "text": "first"},
            {"role": "user", "text": "second"},
        ]

    def test_appended_entry_has_role_user(self):
        """Mutant kill: 'role' literal swapped to 'assistant' / dropped."""
        merged = {}
        _append_to_transcript(merged, "msg")
        assert merged["transcript"][-1]["role"] == "user"

    def test_appended_entry_text_is_redacted_message(self):
        """Mutant kill: 'text' key swap, or message argument dropped."""
        merged = {}
        _append_to_transcript(merged, "the redacted message")
        assert merged["transcript"][-1]["text"] == "the redacted message"

    def test_truncates_when_over_max(self):
        merged = {
            "transcript": [
                {"role": "user", "text": f"old-{i}"}
                for i in range(_MAX_TRANSCRIPT)
            ]
        }
        _append_to_transcript(merged, "new")
        assert len(merged["transcript"]) == _MAX_TRANSCRIPT
        # The oldest is dropped; the newest is at the end.
        assert merged["transcript"][0]["text"] == "old-1"
        assert merged["transcript"][-1]["text"] == "new"

    def test_does_not_truncate_when_at_max_minus_one(self):
        """Boundary: at MAX-1 entries plus the new append == MAX (no truncation)."""
        merged = {
            "transcript": [
                {"role": "user", "text": f"old-{i}"}
                for i in range(_MAX_TRANSCRIPT - 1)
            ]
        }
        _append_to_transcript(merged, "new")
        assert len(merged["transcript"]) == _MAX_TRANSCRIPT
        # First entry preserved; nothing dropped.
        assert merged["transcript"][0]["text"] == "old-0"

    def test_does_not_truncate_when_under_max(self):
        merged = {"transcript": [{"role": "user", "text": "only"}]}
        _append_to_transcript(merged, "new")
        assert len(merged["transcript"]) == 2

    def test_truncate_keeps_most_recent_max_entries(self):
        """Mutant kill: slice direction (`[-MAX:]` vs `[:MAX]`)."""
        merged = {
            "transcript": [
                {"role": "user", "text": f"e-{i}"}
                # Construct to length MAX+5 so truncation drops 5.
                for i in range(_MAX_TRANSCRIPT + 4)
            ]
        }
        _append_to_transcript(merged, "newest")
        # After append we have MAX+5 → truncated to MAX, keeping the
        # last MAX. The first kept entry should be at original index 5
        # ("e-5"), not "e-0" (which would mean we kept the first MAX).
        assert merged["transcript"][0]["text"] == "e-5"
        assert merged["transcript"][-1]["text"] == "newest"

    def test_does_not_save(self, save_recorder):
        """Pure mutator: must not call save_session_slots."""
        _append_to_transcript({}, "msg")
        assert save_recorder == []


# ===========================================================================
# _update_queued_services
# ===========================================================================


class TestUpdateQueuedServices:
    def test_seeds_queue_from_additional_when_queue_empty(self):
        merged = {}
        extracted = {"additional_services": [{"type": "food"}]}
        existing = {}
        _update_queued_services(merged, extracted, existing)
        assert merged["_queued_services"] == [{"type": "food"}]

    def test_does_not_seed_when_queue_already_present(self):
        """Mutant kill: 'not in' → 'in' swap on the seed condition."""
        merged = {"_queued_services": [{"type": "shelter"}]}
        extracted = {"additional_services": [{"type": "food"}]}
        existing = {}
        _update_queued_services(merged, extracted, existing)
        # Original queue preserved; new additional NOT appended.
        assert merged["_queued_services"] == [{"type": "shelter"}]

    def test_does_not_seed_when_no_additional(self):
        merged = {}
        extracted = {"additional_services": []}
        existing = {}
        _update_queued_services(merged, extracted, existing)
        assert "_queued_services" not in merged

    # --- Five-clause AND for the clear path ---

    def _setup_clear_scenario(self):
        """Default setup where ALL five clauses are satisfied → clear fires."""
        merged = {"_queued_services": [{"type": "food"}]}
        extracted = {
            "service_type": "shelter",
            "additional_services": [],
            "_is_additive": False,
        }
        existing = {"service_type": "food"}
        return merged, extracted, existing

    def test_clears_queue_when_all_five_clauses_hold(self):
        merged, extracted, existing = self._setup_clear_scenario()
        _update_queued_services(merged, extracted, existing)
        assert "_queued_services" not in merged

    def test_does_not_clear_when_extracted_service_type_is_none(self):
        """Clause 1 false: ``extracted.service_type`` is None."""
        merged, extracted, existing = self._setup_clear_scenario()
        extracted["service_type"] = None
        _update_queued_services(merged, extracted, existing)
        assert merged["_queued_services"] == [{"type": "food"}]

    def test_does_not_clear_when_existing_service_type_is_none(self):
        """Clause 2 false: prior ``existing.service_type`` is None.

        Without this clause, a first-turn ``service_type`` extraction
        with no add-ons would clear a queue that doesn't yet exist —
        irrelevant in practice, but the guard prevents accidental
        behavior change in edge cases.
        """
        merged, extracted, existing = self._setup_clear_scenario()
        existing["service_type"] = None
        _update_queued_services(merged, extracted, existing)
        assert merged["_queued_services"] == [{"type": "food"}]

    def test_does_not_clear_when_service_type_unchanged(self):
        """Clause 3 false: ``extracted.service_type == existing.service_type``."""
        merged, extracted, existing = self._setup_clear_scenario()
        existing["service_type"] = "shelter"  # same as extracted
        _update_queued_services(merged, extracted, existing)
        assert merged["_queued_services"] == [{"type": "food"}]

    def test_does_not_clear_when_extracted_has_additional(self):
        """Clause 4 false: this turn brought new additional services."""
        merged, extracted, existing = self._setup_clear_scenario()
        # Note: this also triggers the seed branch — but the queue is
        # already non-empty so seed is a no-op. The clear MUST not fire.
        extracted["additional_services"] = [{"type": "clothing"}]
        _update_queued_services(merged, extracted, existing)
        assert merged["_queued_services"] == [{"type": "food"}]

    def test_does_not_clear_when_turn_is_additive(self):
        """Clause 5 false: ``_is_additive`` flag set → user is adding, not pivoting."""
        merged, extracted, existing = self._setup_clear_scenario()
        extracted["_is_additive"] = True
        _update_queued_services(merged, extracted, existing)
        assert merged["_queued_services"] == [{"type": "food"}]

    def test_does_not_save(self, save_recorder):
        """Pure mutator: must not call save_session_slots."""
        merged, extracted, existing = self._setup_clear_scenario()
        _update_queued_services(merged, extracted, existing)
        assert save_recorder == []

    def test_seed_when_additional_present_and_queue_empty_does_not_collide_with_clear(self):
        """Both branches in one call: seeds, but clear does NOT fire because
        ``additional`` is non-empty (clause 4)."""
        merged = {}
        extracted = {
            "service_type": "shelter",
            "additional_services": [{"type": "food"}],
            "_is_additive": False,
        }
        existing = {"service_type": "shelter"}  # same → clause 3 also false
        _update_queued_services(merged, extracted, existing)
        # Seed fired:
        assert merged["_queued_services"] == [{"type": "food"}]


# ===========================================================================
# _clear_awaiting_service_after_clear
# ===========================================================================


class TestClearAwaitingServiceAfterClear:
    def test_pops_flag_when_present(self, save_recorder):
        existing = {"_awaiting_service_after_clear": True, "service_type": "food"}
        _clear_awaiting_service_after_clear("sid", existing)
        assert "_awaiting_service_after_clear" not in existing

    def test_no_error_when_flag_absent(self, save_recorder):
        existing = {"service_type": "food"}
        _clear_awaiting_service_after_clear("sid", existing)
        assert "_awaiting_service_after_clear" not in existing

    def test_saves_after_pop(self, save_recorder):
        existing = {"_awaiting_service_after_clear": True}
        _clear_awaiting_service_after_clear("sid", existing)
        assert len(save_recorder) == 1
        sid, snapshot = save_recorder[0]
        assert sid == "sid"
        assert "_awaiting_service_after_clear" not in snapshot

    def test_save_happens_even_when_flag_was_absent(self, save_recorder):
        """The original code saves unconditionally after the pop. Preserved."""
        existing = {}
        _clear_awaiting_service_after_clear("sid", existing)
        assert len(save_recorder) == 1


# ===========================================================================
# _clear_stale_last_action
# ===========================================================================


class TestClearStaleLastAction:
    def test_clears_when_last_action_set_and_category_not_consumer(
        self, save_recorder
    ):
        existing = {"_last_action": "emotional_ack"}
        _clear_stale_last_action("sid", existing, "greeting")
        assert "_last_action" not in existing
        assert len(save_recorder) == 1

    def test_does_not_clear_when_category_is_confirm_yes(self, save_recorder):
        """Mutant kill: dropping 'confirm_yes' from the consumer set."""
        existing = {"_last_action": "emotional_ack"}
        _clear_stale_last_action("sid", existing, "confirm_yes")
        assert existing["_last_action"] == "emotional_ack"
        assert save_recorder == []

    def test_does_not_clear_when_category_is_confirm_deny(self, save_recorder):
        """Mutant kill: dropping 'confirm_deny' from the consumer set."""
        existing = {"_last_action": "emotional_ack"}
        _clear_stale_last_action("sid", existing, "confirm_deny")
        assert existing["_last_action"] == "emotional_ack"
        assert save_recorder == []

    def test_does_not_clear_when_last_action_absent(self, save_recorder):
        """Mutant kill: 'and' → 'or' on the guard."""
        existing = {}
        _clear_stale_last_action("sid", existing, "greeting")
        assert "_last_action" not in existing
        assert save_recorder == []

    def test_does_not_clear_when_last_action_falsy_value(self, save_recorder):
        """``existing.get("_last_action")`` truthy guard: falsy → no clear, no save."""
        existing = {"_last_action": None}  # explicit None
        _clear_stale_last_action("sid", existing, "greeting")
        # No error; key still present but unchanged. Save not called.
        assert save_recorder == []

    def test_unknown_category_clears(self, save_recorder):
        """Any non-consumer category triggers clear — kills mutants that
        check for specific consumers' negation rather than set membership."""
        existing = {"_last_action": "x"}
        _clear_stale_last_action("sid", existing, "service")
        assert "_last_action" not in existing


# ===========================================================================
# _consume_last_action
# ===========================================================================


class TestConsumeLastAction:
    def test_pops_when_last_action_truthy(self, save_recorder):
        existing = {"_last_action": "emotional_ack"}
        _consume_last_action("sid", existing, "emotional_ack")
        assert "_last_action" not in existing
        assert len(save_recorder) == 1

    def test_no_op_when_last_action_falsy(self, save_recorder):
        """Mutant kill: removing the guard."""
        existing = {"_last_action": "emotional_ack"}
        _consume_last_action("sid", existing, None)
        assert existing["_last_action"] == "emotional_ack"
        assert save_recorder == []

    def test_uses_captured_value_not_current_dict(self, save_recorder):
        """Key behavior: caller captured ``last_action`` BEFORE the
        ``_handle_context_aware_confirm`` call, in case the handler
        mutated the dict. Helper must read the captured arg, not
        ``existing.get('_last_action')``.
        """
        # last_action captured as truthy, but the handler popped the key
        # before this helper runs:
        existing = {}
        _consume_last_action("sid", existing, "captured_truthy_value")
        # Pop on absent key is a no-op; save still called because the
        # captured value was truthy.
        assert len(save_recorder) == 1


# ===========================================================================
# _persist_emotional_context_early
# ===========================================================================


class TestPersistEmotionalContextEarly:
    def test_writes_and_saves_when_update_non_none(self, save_recorder):
        existing = {}
        _persist_emotional_context_early("sid", existing, "shame")
        assert existing["_emotional_context"] == "shame"
        assert len(save_recorder) == 1
        assert save_recorder[0][1]["_emotional_context"] == "shame"

    def test_no_op_when_update_is_none(self, save_recorder):
        existing = {"existing_key": 1}
        _persist_emotional_context_early("sid", existing, None)
        assert "_emotional_context" not in existing
        assert save_recorder == []

    def test_saves_even_when_update_is_falsy_but_not_none(self, save_recorder):
        """Empty string is non-None → must save.

        Mutant kill: ``is not None`` → bare truthy check (``if update:``)
        which would skip the save on empty string.
        """
        existing = {}
        _persist_emotional_context_early("sid", existing, "")
        assert existing["_emotional_context"] == ""
        assert len(save_recorder) == 1

    def test_overwrites_existing_value(self, save_recorder):
        existing = {"_emotional_context": "shame"}
        _persist_emotional_context_early("sid", existing, "frustrated")
        assert existing["_emotional_context"] == "frustrated"


# ===========================================================================
# _persist_emotional_context_late
# ===========================================================================


class TestPersistEmotionalContextLate:
    def test_sets_merged_when_update_non_none(self, save_recorder):
        merged = {}
        existing = {}
        _persist_emotional_context_late("sid", merged, existing, "shame")
        assert merged["_emotional_context"] == "shame"

    def test_does_not_set_merged_when_update_is_none(self, save_recorder):
        merged = {}
        existing = {}
        _persist_emotional_context_late("sid", merged, existing, None)
        assert "_emotional_context" not in merged

    def test_saves_on_none_to_value_transition(self, save_recorder):
        """The intended save case: early site set nothing, late site
        produced a context value."""
        merged = {}
        existing = {}  # no prior _emotional_context
        _persist_emotional_context_late("sid", merged, existing, "shame")
        assert len(save_recorder) == 1
        assert save_recorder[0][1]["_emotional_context"] == "shame"

    def test_does_not_save_when_existing_already_had_context(
        self, save_recorder
    ):
        """If existing already has context, the early site already saved.
        Late site does not need to save again (and the original code's
        condition prevents it)."""
        merged = {}
        existing = {"_emotional_context": "shame"}
        _persist_emotional_context_late("sid", merged, existing, None)
        # update=None means merged isn't modified, and the save guard
        # checks merged.get(...) → None → no save.
        assert save_recorder == []

    def test_does_not_save_when_no_update_and_no_prior_context(
        self, save_recorder
    ):
        merged = {}
        existing = {}
        _persist_emotional_context_late("sid", merged, existing, None)
        assert save_recorder == []

    def test_late_persist_does_not_save_on_value_to_value_change(
        self, save_recorder
    ):
        """PIN: ORCHESTRATOR_AUDIT.md Bug 1.

        When the early site set ``_emotional_context = "shame"`` and the
        late computation produces ``"frustrated"``, the in-memory dict
        updates but the save condition evaluates ``"frustrated" and not
        "shame"`` → False, so the change is NOT persisted.

        This test pins the BUGGY behavior. When PR-γ fixes the
        condition (``!=`` instead), invert the save_recorder
        assertion to ``len(...) == 1`` and add a content check that
        the persisted value is "frustrated".
        """
        merged = {"_emotional_context": "shame"}  # carried from existing into merged
        existing = {"_emotional_context": "shame"}
        _persist_emotional_context_late("sid", merged, existing, "frustrated")
        # In-memory: updated (this part is correct).
        assert merged["_emotional_context"] == "frustrated"
        # Persistence: BUG — not saved.
        assert save_recorder == []

    def test_save_uses_merged_dict_not_existing(self, save_recorder):
        """When the save fires, it persists ``merged`` (which may have
        more session state from the latest extraction). Mutant kill:
        ``save_session_slots(session_id, existing)`` → wrong dict.
        """
        merged = {"_emotional_context": "shame", "service_type": "shelter"}
        existing = {}  # None → triggers save
        _persist_emotional_context_late("sid", merged, existing, "shame")
        assert len(save_recorder) == 1
        snapshot = save_recorder[0][1]
        # The persisted dict is merged, not existing — so service_type
        # should appear.
        assert snapshot.get("service_type") == "shelter"


# ===========================================================================
# Module-level constants
# ===========================================================================


class TestModuleConstants:
    def test_consumes_last_action_is_frozenset(self):
        """Frozen so a downstream caller can't accidentally mutate it."""
        assert isinstance(session_helpers._CONSUMES_LAST_ACTION, frozenset)

    def test_consumes_last_action_contains_both_confirm_actions(self):
        assert "confirm_yes" in session_helpers._CONSUMES_LAST_ACTION
        assert "confirm_deny" in session_helpers._CONSUMES_LAST_ACTION

    def test_consumes_last_action_does_not_contain_unrelated_actions(self):
        """Mutant kill: extraneous additions to the set."""
        assert "greeting" not in session_helpers._CONSUMES_LAST_ACTION
        assert "service" not in session_helpers._CONSUMES_LAST_ACTION
        assert "" not in session_helpers._CONSUMES_LAST_ACTION

    def test_max_transcript_is_positive(self):
        assert session_helpers._MAX_TRANSCRIPT > 0

    def test_max_transcript_value(self):
        """Pin the documented value. Change deliberately if requirements shift."""
        assert session_helpers._MAX_TRANSCRIPT == 20


# ---------------------------------------------------------------------------
# has_user_content / _USER_PROVIDED_SLOTS
# ---------------------------------------------------------------------------


class TestHasUserContent:
    """The predicate that distinguishes "user shared something searchable"
    from "the unified extractor has run and populated control flags."

    This was originally a private constant in ``handlers/meta.py`` for the
    greeting handler. Promoted here because Phase C handlers
    (``_handle_pending_confirmation``, ``_handle_post_results_interaction``,
    etc.) will need the same predicate. The bug it guards against is the
    naive ``any(v is not None for v in session.values())`` check that
    misreads Trust Model 5 control flags as evidence of a prior search.
    """

    def test_empty_session_returns_false(self):
        """No session at all → no content."""
        assert has_user_content({}) is False

    def test_none_session_returns_false(self):
        """None passes through cleanly (callers may pass None)."""
        assert has_user_content(None) is False

    def test_only_control_flags_returns_false(self):
        """Trust Model 5 control flags MUST NOT count as user content.

        This is the regression-preventing test. Without the whitelist
        approach, ``no_requirements: False`` (a populated bool, default
        from the extractor) would wrongly count as "user content."
        """
        session = {
            "no_requirements": False,
            "_contradiction": False,
            "_is_additive": False,
            "_populations": [],
            "transcript": [],
        }
        assert has_user_content(session) is False, (
            "Trust Model 5 control flags must not register as user content. "
            "If this assertion fails, _handle_greeting and any other "
            "handler using has_user_content() will misread casual-chat "
            "sessions as having an active search."
        )

    def test_service_type_counts_as_content(self):
        assert has_user_content({"service_type": "shelter"}) is True

    def test_location_counts_as_content(self):
        assert has_user_content({"location": "Brooklyn"}) is True

    def test_org_name_counts_as_content(self):
        """org_name is a search target distinct from service_type — must count."""
        assert has_user_content({"org_name": "Covenant House"}) is True

    def test_populations_with_value_counts_as_content(self):
        """Empty list (default) is falsy, but a populated list IS content."""
        assert has_user_content({"_populations": ["lgbtq"]}) is True

    def test_empty_populations_list_does_not_count(self):
        """Default-empty _populations is not content (it's the extractor's
        always-populated default, like the Trust Model 5 flags)."""
        assert has_user_content({"_populations": []}) is False

    def test_empty_string_does_not_count(self):
        """Falsy values for content slots also don't count — the user
        didn't actually share something."""
        assert has_user_content({"location": ""}) is False
        assert has_user_content({"service_type": None}) is False

    def test_mixed_content_and_flags(self):
        """A real session: flags AND user content. Should return True."""
        session = {
            "service_type": "food",
            "location": "Manhattan",
            "no_requirements": False,
            "_contradiction": False,
            "_populations": [],
        }
        assert has_user_content(session) is True

    def test_realistic_casual_chat_session(self):
        """The actual session shape after one turn of casual chat. The
        greeting bug regression scenario.

        After a user types "how's it going?" turn 1 and "hi" turn 2,
        the extractor populates these fields. Without has_user_content,
        the greeting handler responds "Hey again! I still have your
        earlier search info..." — implying a search that never happened.
        """
        casual_session = {
            "no_requirements": False,
            "_contradiction": False,
            "_is_additive": False,
            "_populations": [],
            "transcript": [{"role": "user", "content": "how's it going?"}],
            "service_type": None,
            "service_detail": None,
            "additional_services": None,
            "location": None,
            "urgency": None,
            "age": None,
            "family_status": None,
            "_gender": None,
            "org_name": None,
        }
        assert has_user_content(casual_session) is False

    def test_user_provided_slots_constant_is_canonical(self):
        """The constant must contain exactly the 10 user-content slots
        documented in slot_extractor's output. Pinning this prevents
        accidental drift if a new control flag gets added to the
        extractor and someone naively appends it here.
        """
        # If this fails, the slot_extractor probably added a new slot.
        # Decide if it's user-content (add to _USER_PROVIDED_SLOTS) or
        # control flag (don't). Don't reflexively expand the tuple.
        assert _USER_PROVIDED_SLOTS == (
            "service_type", "service_detail", "additional_services",
            "location", "urgency", "age", "family_status",
            "_gender", "_populations", "org_name",
        )

