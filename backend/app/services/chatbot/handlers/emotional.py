"""Emotion-aware handlers: emotional expression, frustration, escalation, crisis.

Each of these sets ``existing["_last_action"]`` so a follow-up confirm_yes
/ confirm_deny is interpreted through the emotional lens (see
``handlers/confirmation.py``:_handle_context_aware_confirm).

``_handle_crisis`` is the largest because it routes through seven crisis
categories with population-specific resources, applies DV-specific
population tagging, and implements the "step-down" flow where a crisis
and a service request co-occur.

All handlers in this module take a single ``MessageContext`` parameter
(see ``chatbot/context.py``) — see ORCHESTRATOR_AUDIT.md Phase B.
"""

import logging
import re

from app.services.audit_log import log_crisis_detected
from app.services.confirmation import _confirmation_quick_replies
from app.services.phrase_lists import _SERVICE_LABELS
from app.services.responses import (
    _ESCALATION_RESPONSE,
    _FRUSTRATION_RESPONSE,
    _pick_emotional_response,
)
from app.services.session_store import save_session_slots
from app.services.slot_extraction_regex import (
    NEAR_ME_SENTINEL,
    is_enough_to_answer,
    merge_slots,
)

from ..context import MessageContext, _empty_reply
from ..logging import _log_turn


logger = logging.getLogger(__name__)

# --- Maximum word count for emotional enhancements ---
_ENHANCEMENT_MAX_WORDS = 25

# --- Patterns that indicate service-push language (case-insensitive) ---
_SERVICE_PUSH_RE = re.compile(
    r"(?i)"
    r"(?:help you find|search for|look(?:ing)? for|find (?:a |you )?)"
    r"|(?:shelter|food|clothing|shower|housing|medical|job|employment|navigator)"
    r"|(?:(?:services?|resources?) (?:near|for|available))"
    r"|(?:assist you|connect you|help with finding)"
    r"|(?:anything practical)"
    r"|(?:finding something specific)"
)

# --- Vague service-adjacent phrases that slip past the regex above ---
_BLOCKLIST_PHRASES = [
    "options available",
    "places that might help",
    "i can look into that",
    "information that could help",
    "support out there",
    "often benefit from",
    "that's what i'm here for",
    "point you in the right direction",
    "figure out what you need",
]


def _validate_emotional_enhancement(text: str) -> bool:
    """Return True if *text* is a valid emotional-enhancement line.

    Rejects empty / literal-NONE values, service-push language, vague
    service-adjacent phrases, and anything over ``_ENHANCEMENT_MAX_WORDS``
    words.
    """
    if not text or text.strip().lower() in ("none", ""):
        return False

    if len(text.split()) > _ENHANCEMENT_MAX_WORDS:
        return False

    if _SERVICE_PUSH_RE.search(text):
        return False

    lowered = text.lower()
    for phrase in _BLOCKLIST_PHRASES:
        if phrase in lowered:
            return False

    return True


def _handle_emotional(ctx: MessageContext):
    """Empathic response picked to match the detected emotional signal.
    Marks _last_action so a follow-up 'yes' is interpreted as asking for
    the peer-navigator handoff."""
    response = _pick_emotional_response(ctx.message)
    ctx.existing["_last_action"] = "emotional"
    save_session_slots(ctx.session_id, ctx.existing)
    result = _empty_reply(
        ctx.session_id, response, ctx.existing,
        quick_replies=[
            {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
        ],
    )
    _log_turn(ctx.session_id, ctx.redacted_message, result, ctx.category,
              request_id=ctx.request_id, tone=ctx.tone)
    return result


def _handle_escalation(ctx: MessageContext):
    """User explicitly asked for human help. Clear any pending confirmation
    (so a trailing 'yes' doesn't fire a search the user abandoned) and offer
    peer navigator + start-over."""
    if ctx.existing.get("_pending_confirmation"):
        ctx.existing.pop("_pending_confirmation", None)
    ctx.existing["_last_action"] = "escalation"
    save_session_slots(ctx.session_id, ctx.existing)
    result = _empty_reply(
        ctx.session_id, _ESCALATION_RESPONSE, ctx.existing,
        quick_replies=[
            {"label": "🔍 New search", "value": "Start over"},
            {"label": "👤 Talk to a person", "value": "Connect with person"},
        ],
    )
    _log_turn(ctx.session_id, ctx.redacted_message, result, ctx.category,
              request_id=ctx.request_id, tone=ctx.tone)
    return result


def _handle_frustration(ctx: MessageContext):
    """Handle frustration with escalating responses.

    When the user is frustrated because the bot re-asked for info they
    already provided, and the session already has enough to search,
    offer to proceed instead of just apologizing.
    """
    frust_count = ctx.existing.get("_frustration_count", 0) + 1
    ctx.existing["_frustration_count"] = frust_count
    ctx.existing["_last_action"] = "frustration"
    save_session_slots(ctx.session_id, ctx.existing)

    # Context recovery: if we already have enough info to search,
    # acknowledge the frustration AND offer to proceed immediately.
    _has_enough = is_enough_to_answer(ctx.existing)
    _svc = ctx.existing.get("service_type")
    _loc = ctx.existing.get("location")

    if frust_count >= 3:
        result = _empty_reply(
            ctx.session_id,
            "I'm sorry I haven't been able to help. Let me connect you "
            "with a peer navigator — they can work with you directly.",
            ctx.existing,
            quick_replies=[
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
            ],
        )
    elif frust_count >= 2:
        result = _empty_reply(
            ctx.session_id,
            "I hear you — I'm clearly not finding what you need right now. "
            "I think a peer navigator would be more helpful. They're real "
            "people who know the system and can work with you directly. "
            "You can also call 311 for live help anytime.",
            ctx.existing,
            quick_replies=[
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
                {"label": "🔄 Start over", "value": "Start over"},
            ],
        )
    elif _has_enough and _svc and _loc and not ctx.existing.get("_last_results"):
        # First frustration AND we have enough info AND no results yet —
        # the frustration is likely caused by the bot re-asking for info
        # we already have. Acknowledge the mistake and offer to proceed
        # with what we know.
        #
        # IMPORTANT: this branch must NOT fire when _last_results exists.
        # If the user has already seen results, the frustration is about
        # the RESULTS being unhelpful — the "I already have what I need"
        # reframe becomes wrong (we already searched; re-confirming just
        # re-runs the same search that produced the unhelpful results).
        # Post-results frustration falls through to the else branch
        # instead, which offers the navigator/311 escalation. See the
        # test_second_frustration_is_shorter / test_eval_frustration_loop
        # regression that this guard resolves.
        svc_label = _SERVICE_LABELS.get(_svc, _svc)
        loc_label = _loc if _loc != NEAR_ME_SENTINEL else "your area"
        result = _empty_reply(
            ctx.session_id,
            f"You're right, I apologize for the confusion. "
            f"I already have what I need — I'll look for "
            f"{svc_label} in {loc_label}. Sound good?",
            ctx.existing,
            quick_replies=_confirmation_quick_replies(ctx.existing),
        )
        ctx.existing["_pending_confirmation"] = True
        # Clear _last_action: the frustration context has been resolved —
        # the bot is now asking a confirmation question ("Sound good?").
        # Without this, _handle_context_aware_confirm sees last_action=
        # "frustration" + confirm_yes and routes to escalation instead of
        # _handle_pending_confirmation which executes the search.
        ctx.existing.pop("_last_action", None)
        save_session_slots(ctx.session_id, ctx.existing)
    else:
        result = _empty_reply(
            ctx.session_id, _FRUSTRATION_RESPONSE, ctx.existing,
            quick_replies=[
                {"label": "🔍 New search", "value": "Start over"},
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
            ],
        )

    # --- Filter-aware post-routing cleanup ---
    # The routing branches above have already made their decisions using
    # whatever _last_results was set. Now apply the filter-pipeline design
    # contract: if a filter was active, clear only the filter state but
    # PRESERVE _last_results so the user can recover via "show all";
    # otherwise clear _last_results and pagination entirely.
    #
    # This reconciles Bug 4a (which needs _last_results visible to the
    # handler's routing logic above) with test_filter_pipeline's state
    # expectations (which require _last_results to be gone afterward
    # when there was no filter).
    if ctx.existing.get("_filtered_results"):
        ctx.existing.pop("_filtered_results", None)
        ctx.existing.pop("_filter_phrase", None)
        # _last_results preserved — "show all" will re-display the unfiltered set
        # Reset displayed_count so a subsequent "show more" starts fresh
        ctx.existing["_displayed_count"] = 0
    else:
        ctx.existing.pop("_last_results", None)
        ctx.existing.pop("_displayed_count", None)
    save_session_slots(ctx.session_id, ctx.existing)

    _log_turn(ctx.session_id, ctx.redacted_message, result, "frustration",
              request_id=ctx.request_id, tone=ctx.tone)
    return result


def _handle_crisis(ctx: MessageContext):
    """Handle crisis detection. Returns a result dict, or None to fall through."""
    if ctx.crisis_result is None:
        return None

    crisis_category, crisis_response = ctx.crisis_result
    logger.warning(
        f"Session {ctx.session_id}: crisis detected, "
        f"category='{crisis_category}'"
    )
    log_crisis_detected(ctx.session_id, crisis_category, ctx.redacted_message,
                        request_id=ctx.request_id)

    if ctx.existing.get("_pending_confirmation"):
        ctx.existing.pop("_pending_confirmation", None)

    _step_down_categories = (
        "safety_concern", "domestic_violence", "youth_runaway", "assault_victim",
        # Sprint 1 fix (peer_diabetic_insulin): when crisis fires alongside
        # chronic-medical service intent (e.g. ran out of insulin, regex
        # already extracted service_type=medical, urgency=high), preserve
        # the slots and offer the clinic search the medical_emergency
        # response already promises ("Once you're safe, I can help you find
        # nearby clinics or health services"). For genuine life-threats —
        # heart attack, can't breathe, seizure — the user is on the phone
        # with 911 and the "Yes, search for medical clinic" quick reply is
        # ignored, same trade-off the other 4 categories already make.
        "medical_emergency",
    )
    if ctx.has_service_intent and crisis_category in _step_down_categories:
        merged_crisis = merge_slots(ctx.existing, ctx.early_extracted)

        # Phase 5: When crisis category is domestic_violence, ensure
        # dv_survivor is in _populations so the description boost fires
        # on the subsequent search. The crisis detector catches 54 DV
        # phrases (e.g. "he hits me", "afraid to go home") that the
        # population extractor doesn't cover. This bridges the gap.
        if crisis_category == "domestic_violence":
            pops = set(merged_crisis.get("_populations", []))
            pops.add("dv_survivor")
            merged_crisis["_populations"] = sorted(pops)

        additional = ctx.early_extracted.get("additional_services", [])
        if additional and "_queued_services" not in merged_crisis:
            merged_crisis["_queued_services"] = additional
        merged_crisis["_last_action"] = "crisis"
        # Set pending confirmation so tapping "Yes, search" routes through
        # _handle_pending_confirmation → _execute_and_respond.  Without this,
        # the "already shown results" guard (which checks _last_results +
        # confirm_yes + NOT _pending_confirmation) swallows the button press
        # and tells the user "I've already shown the results above" — even
        # though no service cards were shown, only crisis hotline numbers.
        merged_crisis["_pending_confirmation"] = True
        save_session_slots(ctx.session_id, merged_crisis)

        svc_label = _SERVICE_LABELS.get(
            ctx.early_extracted.get("service_type", ""),
            ctx.early_extracted.get("service_type", "services"),
        )
        loc_label = ctx.early_extracted.get("location") or "your area"
        step_down_msg = (
            f"\n\nI can also help you find {svc_label} in "
            f"{loc_label} — would you like me to search?"
        )
        result = _empty_reply(
            ctx.session_id,
            crisis_response + step_down_msg,
            merged_crisis,
            quick_replies=[
                {"label": f"✅ Yes, search for {svc_label}",
                 "value": "__crisis_geo_search__"},
                {"label": "🤝 Peer navigator",
                 "value": "Connect with peer navigator"},
            ],
        )
    else:
        # Phase 5: Inject dv_survivor even without service intent, so
        # if the user later asks for a service, the boost is in session.
        if crisis_category == "domestic_violence":
            pops = set(ctx.existing.get("_populations", []))
            pops.add("dv_survivor")
            ctx.existing["_populations"] = sorted(pops)

        ctx.existing["_last_action"] = "crisis"
        save_session_slots(ctx.session_id, ctx.existing)
        result = _empty_reply(ctx.session_id, crisis_response, ctx.existing)

    _log_turn(ctx.session_id, ctx.redacted_message, result, "crisis",
              request_id=ctx.request_id, tone=ctx.tone)
    return result
