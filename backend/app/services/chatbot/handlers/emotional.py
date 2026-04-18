"""Emotion-aware handlers: emotional expression, frustration, escalation, crisis.

Each of these sets ``existing["_last_action"]`` so a follow-up confirm_yes
/ confirm_deny is interpreted through the emotional lens (see
``handlers/confirmation.py``:_handle_context_aware_confirm).

``_handle_crisis`` is the largest because it routes through seven crisis
categories with population-specific resources, applies DV-specific
population tagging, and implements the "step-down" flow where a crisis
and a service request co-occur.
"""

import logging

from app.services.audit_log import log_crisis_detected
from app.services.confirmation import _confirmation_quick_replies
from app.services.phrase_lists import _SERVICE_LABELS
from app.services.responses import (
    _ESCALATION_RESPONSE,
    _FRUSTRATION_RESPONSE,
    _pick_emotional_response,
)
from app.services.session_store import save_session_slots
from app.services.slot_extractor import (
    NEAR_ME_SENTINEL,
    is_enough_to_answer,
    merge_slots,
)

from ..context import _empty_reply
from ..logging import _log_turn


logger = logging.getLogger(__name__)


def _handle_emotional(session_id, message, redacted_message, existing,
                      category, tone, request_id):
    """Empathic response picked to match the detected emotional signal.
    Marks _last_action so a follow-up 'yes' is interpreted as asking for
    the peer-navigator handoff."""
    response = _pick_emotional_response(message)
    existing["_last_action"] = "emotional"
    save_session_slots(session_id, existing)
    result = _empty_reply(
        session_id, response, existing,
        quick_replies=[
            {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
        ],
    )
    _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
    return result


def _handle_escalation(session_id, redacted_message, existing, category, tone, request_id):
    """User explicitly asked for human help. Clear any pending confirmation
    (so a trailing 'yes' doesn't fire a search the user abandoned) and offer
    peer navigator + start-over."""
    if existing.get("_pending_confirmation"):
        existing.pop("_pending_confirmation", None)
    existing["_last_action"] = "escalation"
    save_session_slots(session_id, existing)
    result = _empty_reply(
        session_id, _ESCALATION_RESPONSE, existing,
        quick_replies=[
            {"label": "🔍 New search", "value": "Start over"},
            {"label": "👤 Talk to a person", "value": "Connect with person"},
        ],
    )
    _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
    return result


def _handle_frustration(session_id, redacted_message, existing, tone, request_id):
    """Handle frustration with escalating responses.

    When the user is frustrated because the bot re-asked for info they
    already provided, and the session already has enough to search,
    offer to proceed instead of just apologizing.
    """
    frust_count = existing.get("_frustration_count", 0) + 1
    existing["_frustration_count"] = frust_count
    existing["_last_action"] = "frustration"
    save_session_slots(session_id, existing)

    # Context recovery: if we already have enough info to search,
    # acknowledge the frustration AND offer to proceed immediately.
    _has_enough = is_enough_to_answer(existing)
    _svc = existing.get("service_type")
    _loc = existing.get("location")

    if frust_count >= 3:
        result = _empty_reply(
            session_id,
            "I'm sorry I haven't been able to help. Let me connect you "
            "with a peer navigator — they can work with you directly.",
            existing,
            quick_replies=[
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
            ],
        )
    elif frust_count >= 2:
        result = _empty_reply(
            session_id,
            "I hear you — I'm clearly not finding what you need right now. "
            "I think a peer navigator would be more helpful. They're real "
            "people who know the system and can work with you directly. "
            "You can also call 311 for live help anytime.",
            existing,
            quick_replies=[
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
                {"label": "🔄 Start over", "value": "Start over"},
            ],
        )
    elif _has_enough and _svc and _loc and not existing.get("_last_results"):
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
            session_id,
            f"You're right, I apologize for the confusion. "
            f"I already have what I need — I'll look for "
            f"{svc_label} in {loc_label}. Sound good?",
            existing,
            quick_replies=_confirmation_quick_replies(existing),
        )
        existing["_pending_confirmation"] = True
        # Clear _last_action: the frustration context has been resolved —
        # the bot is now asking a confirmation question ("Sound good?").
        # Without this, _handle_context_aware_confirm sees last_action=
        # "frustration" + confirm_yes and routes to escalation instead of
        # _handle_pending_confirmation which executes the search.
        existing.pop("_last_action", None)
        save_session_slots(session_id, existing)
    else:
        result = _empty_reply(
            session_id, _FRUSTRATION_RESPONSE, existing,
            quick_replies=[
                {"label": "🔍 New search", "value": "Start over"},
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
            ],
        )
    _log_turn(session_id, redacted_message, result, "frustration", request_id=request_id, tone=tone)
    return result


def _handle_crisis(
    session_id, message, redacted_message, existing,
    early_extracted, has_service_intent, _crisis_result,
    tone, request_id,
):
    """Handle crisis detection. Returns a result dict, or None to fall through."""
    if _crisis_result is None:
        return None

    crisis_category, crisis_response = _crisis_result
    logger.warning(
        f"Session {session_id}: crisis detected, "
        f"category='{crisis_category}'"
    )
    log_crisis_detected(session_id, crisis_category, redacted_message, request_id=request_id)

    if existing.get("_pending_confirmation"):
        existing.pop("_pending_confirmation", None)

    _step_down_categories = (
        "safety_concern", "domestic_violence", "youth_runaway", "assault_victim",
    )
    if has_service_intent and crisis_category in _step_down_categories:
        merged_crisis = merge_slots(existing, early_extracted)

        # Phase 5: When crisis category is domestic_violence, ensure
        # dv_survivor is in _populations so the description boost fires
        # on the subsequent search. The crisis detector catches 54 DV
        # phrases (e.g. "he hits me", "afraid to go home") that the
        # population extractor doesn't cover. This bridges the gap.
        if crisis_category == "domestic_violence":
            pops = set(merged_crisis.get("_populations", []))
            pops.add("dv_survivor")
            merged_crisis["_populations"] = sorted(pops)

        additional = early_extracted.get("additional_services", [])
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
        save_session_slots(session_id, merged_crisis)

        svc_label = _SERVICE_LABELS.get(
            early_extracted.get("service_type", ""),
            early_extracted.get("service_type", "services"),
        )
        loc_label = early_extracted.get("location") or "your area"
        step_down_msg = (
            f"\n\nI can also help you find {svc_label} in "
            f"{loc_label} — would you like me to search?"
        )
        result = _empty_reply(
            session_id,
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
            pops = set(existing.get("_populations", []))
            pops.add("dv_survivor")
            existing["_populations"] = sorted(pops)

        existing["_last_action"] = "crisis"
        save_session_slots(session_id, existing)
        result = _empty_reply(session_id, crisis_response, existing)

    _log_turn(session_id, redacted_message, result, "crisis", request_id=request_id, tone=tone)
    return result
