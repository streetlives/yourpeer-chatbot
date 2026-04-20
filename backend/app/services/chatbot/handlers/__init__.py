"""Handler package — every branch of the routing dispatch lives here.

Handlers are grouped by shared concerns rather than by category name so
that related code (and its regexes / phrase lists) stays in the same
module. The split is:

    meta           greeting, thanks, help, bot-identity, bot-capability,
                   reset, confused
    emotional      emotional, frustration, escalation, crisis
    accessibility  spanish detection, demographic skip, location unknown
    confirmation   context-aware confirm, pending confirmation,
                   change service/location, correction, negative preference,
                   post-pending confirmation
    post_results   post-results interaction (dispatch), show-more,
                   sort, question, hours-for-day
    general        general conversation / unrecognized-service tiered redirect

Everything handler-related is re-exported here so the orchestrator's
import block stays short.
"""

from .accessibility import (
    _handle_demographic_skip,
    _handle_location_unknown,
    _handle_spanish_detection,
)
from .confirmation import (
    _handle_change_location_request,
    _handle_change_service_request,
    _handle_context_aware_confirm,
    _handle_correction,
    _handle_negative_preference,
    _handle_pending_confirmation,
    _handle_post_pending_confirmation,
)
from .emotional import (
    _handle_crisis,
    _handle_emotional,
    _handle_escalation,
    _handle_frustration,
    _validate_emotional_enhancement,
)
from .general import _handle_general_conversation
from .meta import (
    _handle_bot_capability_question,
    _handle_bot_identity,
    _handle_confused,
    _handle_greeting,
    _handle_help,
    _handle_reset,
    _handle_thanks,
)
from .post_results import (
    _handle_hours_for_day,
    _handle_post_results_interaction,
    _handle_post_results_question,
    _handle_show_more,
    _handle_sort_results,
)


__all__ = [
    # meta
    "_handle_bot_capability_question",
    "_handle_bot_identity",
    "_handle_confused",
    "_handle_greeting",
    "_handle_help",
    "_handle_reset",
    "_handle_thanks",
    # emotional
    "_handle_crisis",
    "_handle_emotional",
    "_handle_escalation",
    "_handle_frustration",
    "_validate_emotional_enhancement",
    # accessibility
    "_handle_demographic_skip",
    "_handle_location_unknown",
    "_handle_spanish_detection",
    # confirmation
    "_handle_change_location_request",
    "_handle_change_service_request",
    "_handle_context_aware_confirm",
    "_handle_correction",
    "_handle_negative_preference",
    "_handle_pending_confirmation",
    "_handle_post_pending_confirmation",
    # post_results
    "_handle_hours_for_day",
    "_handle_post_results_interaction",
    "_handle_post_results_question",
    "_handle_show_more",
    "_handle_sort_results",
    # general
    "_handle_general_conversation",
]
