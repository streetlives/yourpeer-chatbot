# Copyright (c) 2024 Streetlives, Inc.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Tests for A.4 fix: merge_slots clears queue state on service change.

When a user changes their primary service type mid-conversation
("food" → "actually, shelter"), any queue state tied to the old
service's context must be cleared — otherwise the confirmation
message builder reads the stale queue and produces surreal output
like "I'll look for shelter and food..." when the user said just
shelter.

The clear happens in merge_slots specifically (not the orchestrator's
queue-pop at line 364) because the bug's primary failure path is
_handle_post_pending_confirmation → merge_slots, which bypasses the
orchestrator's queue-pop entirely.

See docs/audits/MULTI_INTENT_PLAN.md A.4 and the eval scenarios
`confirm_change_service` and `multiturn_change_mind` for the
user-visible failure.
"""

import pytest

from app.services.slot_extractor import merge_slots


class TestServiceChangeClearsQueueState:
    """Merging a new primary service_type clears queue state from the
    old service's context."""

    def test_service_change_clears_queued_services(self):
        """The core case: user had a queued service tied to 'food',
        then switched to 'shelter'. The queue from the food context
        should be dropped."""
        existing = {
            "service_type": "food",
            "location": "manhattan",
            "_queued_services": [("clothing", None, None)],
        }
        new_values = {"service_type": "shelter"}

        merged = merge_slots(existing, new_values)

        assert merged.get("service_type") == "shelter"
        assert "_queued_services" not in merged

    def test_service_change_clears_queued_services_original(self):
        """_queued_services_original (the snapshot captured at confirmation
        time) must also be cleared on service change. Otherwise the
        confirmation message builder can read from it and include the
        old service in the display."""
        existing = {
            "service_type": "food",
            "location": "manhattan",
            "_queued_services_original": [("clothing", None, None)],
        }
        new_values = {"service_type": "shelter"}

        merged = merge_slots(existing, new_values)

        assert merged.get("service_type") == "shelter"
        assert "_queued_services_original" not in merged

    def test_service_change_clears_queue_offer_pending_flag(self):
        """_queue_offer_pending means the bot was about to offer the
        queued services after the current search completes. If the
        user changed the primary service, that offer is stale — the
        user is no longer in the flow that led to the offer."""
        existing = {
            "service_type": "food",
            "location": "manhattan",
            "_queue_offer_pending": True,
        }
        new_values = {"service_type": "shelter"}

        merged = merge_slots(existing, new_values)

        assert merged.get("service_type") == "shelter"
        assert "_queue_offer_pending" not in merged

    def test_service_change_clears_all_queue_state_at_once(self):
        """The realistic mid-confirmation scenario: the session has
        all three queue-related fields from an earlier multi-service
        flow, user changes service, all three must disappear."""
        existing = {
            "service_type": "food",
            "location": "manhattan",
            "_pending_confirmation": True,
            "_queued_services": [("clothing", None, None)],
            "_queued_services_original": [("clothing", None, None)],
            "_queue_offer_pending": True,
        }
        new_values = {"service_type": "shelter"}

        merged = merge_slots(existing, new_values)

        assert merged.get("service_type") == "shelter"
        assert "_queued_services" not in merged
        assert "_queued_services_original" not in merged
        assert "_queue_offer_pending" not in merged
        # Location should survive — the user only changed service
        assert merged.get("location") == "manhattan"


class TestServiceChangeDoesNotClearUnrelatedState:
    """Negative cases: the clear should be narrowly targeted."""

    def test_same_service_merge_preserves_queue(self):
        """No service change = no queue clearing. User adds a location
        to an existing service, the queue should survive."""
        existing = {
            "service_type": "food",
            "_queued_services": [("shelter", None, None)],
        }
        new_values = {"service_type": "food", "location": "brooklyn"}

        merged = merge_slots(existing, new_values)

        assert merged.get("service_type") == "food"
        assert merged.get("location") == "brooklyn"
        assert merged.get("_queued_services") == [("shelter", None, None)]

    def test_additive_service_does_not_trigger_clear(self):
        """Additive intent ("also shelter" on top of food) is NOT a
        service change — the additive branch in merge_slots already
        handles it by queueing the new service and leaving service_type
        untouched. The clear logic should not fire."""
        existing = {
            "service_type": "food",
            "_queued_services": [("clothing", None, None)],
        }
        new_values = {
            "service_type": "shelter",
            "_is_additive": True,
        }

        merged = merge_slots(existing, new_values)

        # Primary service unchanged — "also" queues, it doesn't replace
        assert merged.get("service_type") == "food"
        # Queue now includes BOTH the old clothing AND the new additive shelter
        queue = merged.get("_queued_services", [])
        services_in_queue = [q[0] for q in queue]
        assert "clothing" in services_in_queue
        assert "shelter" in services_in_queue

    def test_new_service_with_no_existing_service_does_not_clear(self):
        """First-time service set (no prior service_type in existing)
        is not a "change" — there's nothing to clear, but also nothing
        should break."""
        existing = {"_queued_services": [("food", None, None)]}
        new_values = {"service_type": "shelter"}

        merged = merge_slots(existing, new_values)

        assert merged.get("service_type") == "shelter"
        # Existing queue survives — this wasn't a change, it was a fill
        assert merged.get("_queued_services") == [("food", None, None)]

    def test_location_only_change_preserves_queue(self):
        """User changes location without changing service → queue
        should not be touched."""
        existing = {
            "service_type": "food",
            "location": "manhattan",
            "_queued_services": [("shelter", None, None)],
        }
        new_values = {"location": "brooklyn"}

        merged = merge_slots(existing, new_values)

        assert merged.get("location") == "brooklyn"
        assert merged.get("service_type") == "food"
        assert merged.get("_queued_services") == [("shelter", None, None)]


class TestContradictionPromotionClearsQueue:
    """When merge_slots does contradiction-promotion (the narrow case
    where the old service survived extraction and we promote the first
    additional_services entry), the queue should also be cleared since
    service_type is effectively being replaced."""

    def test_contradiction_promotion_clears_queue(self):
        """User's real intent was 'shelter' but extractor missed the
        negation and left service_type='food'. additional_services has
        'shelter' as the first entry. Contradiction-promotion swaps
        them in; queue state from the food context should be cleared."""
        existing = {
            "service_type": "food",
            "location": "manhattan",
            "_queued_services": [("clothing", None, None)],
        }
        new_values = {
            "service_type": "food",  # extractor missed the negation
            "_contradiction": True,
            "additional_services": [("shelter", None, None)],
        }

        merged = merge_slots(existing, new_values)

        # Promotion path: shelter becomes the primary
        assert merged.get("service_type") == "shelter"
        # Queue from food context cleared
        assert "_queued_services" not in merged
