"""Confirmation-flow handlers.

These handle the back-and-forth around the confirmation step: yes/no
responses, explicit requests to change service or location, corrections
("that's not what I meant"), rejections of offered results, and the edge
case where the user sends something new while a confirmation is still
pending.

``_handle_context_aware_confirm`` and ``_handle_pending_confirmation``
are both called unconditionally from the orchestrator — each returns
None to indicate "fall through to the next stage."
"""

import logging

from app.services.confirmation import (
    _build_confirmation_message,
    _confirmation_quick_replies,
    _follow_up_quick_replies,
)
from app.services.phrase_lists import _SERVICE_LABELS, _WELCOME_QUICK_REPLIES
from app.services.responses import _ESCALATION_RESPONSE
from app.services.session_store import save_session_slots
from app.services.slot_extractor import (
    NEAR_ME_SENTINEL,
    extract_slots,
    is_enough_to_answer,
    merge_slots,
    next_follow_up_question,
)

from ..context import _USE_LLM, _USE_UNIFIED_EXTRACTOR, _empty_reply
from ..execution import _execute_and_respond
from ..logging import _log_turn


logger = logging.getLogger(__name__)


# Wh-words used for topic-shift detection. Listed explicitly rather than
# via regex class so the match is deterministic and linter-friendly.
_WH_WORD_STARTERS = (
    "who ", "what ", "where ", "when ", "why ", "how ",
    "who's ", "whats ", "what's ", "hows ", "how's ",
    "whos ", "whens ", "wheres ", "whys ",
    "are you ", "do you ", "can you ", "will you ",
    "did you ", "have you ", "is this ", "is that ",
)


def _looks_like_topic_shift_question(message: str) -> bool:
    """Return True if `message` looks like an off-topic question during a
    pending confirmation — something that deserves a disambiguation prompt
    rather than a silent re-nudge of the prior search.

    This is intentionally conservative. It fires only on messages where the
    user is clearly asking about something else (a question ending in "?",
    starting with a wh-word or auxiliary-verb opener). Single-word
    responses, fragments, and short confirmation-shaped utterances
    ("yeah ok", "maybe", "I dunno") are excluded.

    Called only from Path 3 of ``_handle_post_pending_confirmation`` —
    where the message already failed to match any routing category AND had
    no slot updates. In practice, most true topic-shift questions
    (e.g. "what's your name?") are caught earlier by bot_identity /
    bot_question / help classifiers; this is the defense-in-depth for
    novel phrasings that slip past those lists.
    """
    if not message:
        return False
    stripped = message.strip().lower()
    # Too short to be a substantive question — probably a confirmation
    # fragment like "ok?", "yes?", single-word "what".
    words = stripped.split()
    if len(words) < 2:
        return False
    # Strong signal: ends with a question mark.
    ends_with_question = stripped.rstrip().endswith("?")
    # Starts with a wh-word or question opener.
    starts_with_question = stripped.startswith(_WH_WORD_STARTERS)
    # Require either (a) both signals AND ≥3 words, (b) a strong lead
    # with at least 3 words, or (c) a question mark with at least 4
    # words. Short two-word questions like "what now?" are ambiguous
    # (closer to a confused/help response than a clean topic shift)
    # and fall through to the existing re-nudge path.
    if ends_with_question and starts_with_question and len(words) >= 3:
        return True
    if starts_with_question and len(words) >= 3:
        return True
    if ends_with_question and len(words) >= 4:
        return True
    return False


def _handle_change_location_request(session_id, redacted_message, existing,
                                    early_extracted, category, tone, request_id):
    """User asked to change search location ("search somewhere else").

    If the message already contains a new location (e.g. "actually Manhattan"),
    apply it directly — prevents a frustration loop where the bot wipes the
    location and re-asks what the user just said. Otherwise, wipe and ask.
    """
    new_loc = early_extracted.get("location")
    if new_loc:
        existing["location"] = new_loc
        save_session_slots(session_id, existing)
        if is_enough_to_answer(existing):
            existing["_pending_confirmation"] = True
            save_session_slots(session_id, existing)
            confirm_msg = _build_confirmation_message(existing)
            result = {
                "session_id": session_id,
                "response": confirm_msg,
                "follow_up_needed": True,
                "slots": existing,
                "services": [],
                "result_count": 0,
                "relaxed_search": False,
                "quick_replies": _confirmation_quick_replies(existing),
            }
            _log_turn(session_id, redacted_message, result, "confirmation",
                      request_id=request_id, tone=tone)
            return result
        else:
            follow_up = next_follow_up_question(existing)
            result = {
                "session_id": session_id,
                "response": follow_up,
                "follow_up_needed": True,
                "slots": existing,
                "services": [],
                "result_count": 0,
                "relaxed_search": False,
                "quick_replies": _follow_up_quick_replies(existing),
            }
            _log_turn(session_id, redacted_message, result, "service",
                      request_id=request_id, tone=tone)
            return result

    # No location in message — clear and ask for one
    existing["location"] = None
    save_session_slots(session_id, existing)
    result = _empty_reply(
        session_id,
        "Sure! What neighborhood or borough should I search in?",
        existing,
        quick_replies=[
            {"label": "📍 Use my location", "value": "__use_geolocation__"},
            {"label": "Manhattan", "value": "Manhattan"},
            {"label": "Brooklyn", "value": "Brooklyn"},
            {"label": "Queens", "value": "Queens"},
            {"label": "Bronx", "value": "Bronx"},
            {"label": "Staten Island", "value": "Staten Island"},
        ],
    )
    _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
    return result


def _handle_change_service_request(session_id, redacted_message, existing,
                                   category, tone, request_id):
    """User asked to change the service type — wipe service_type + service_detail
    and show the service menu."""
    existing["service_type"] = None
    existing.pop("service_detail", None)
    save_session_slots(session_id, existing)
    result = _empty_reply(
        session_id,
        "No problem! What kind of help do you need?",
        existing,
        quick_replies=list(_WELCOME_QUICK_REPLIES),
    )
    _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
    return result


def _handle_correction(session_id, redacted_message, existing, tone, request_id):
    """Acknowledge a user correction ("that's not what I meant") by clearing
    pending state and echoing what we WERE searching for so they can redirect."""
    existing.pop("_pending_confirmation", None)
    existing.pop("_last_action", None)
    existing.pop("_last_results", None)
    save_session_slots(session_id, existing)
    service_type = existing.get("service_type")
    location = existing.get("location")
    if location == NEAR_ME_SENTINEL:
        location = None
    context = ""
    if service_type and location:
        context = f" I was searching for {service_type} in {location}."
    elif service_type:
        context = f" I was searching for {service_type}."
    result = _empty_reply(
        session_id,
        f"Sorry about that!{context} Let me know what you need — you can "
        f"pick a service below, tell me in your own words, or connect "
        f"with a peer navigator.",
        existing,
        quick_replies=list(_WELCOME_QUICK_REPLIES) + [
            {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
        ],
    )
    _log_turn(session_id, redacted_message, result, "correction",
              request_id=request_id, tone=tone, confidence="low")
    return result


def _handle_negative_preference(session_id, redacted_message, existing, tone, request_id):
    """Handle "I don't like those" / "none of these" with tiered escalation.

    After 3+ consecutive frustration-counted turns, routes to peer navigator.
    After 2, adds 311 as a live-help option. Otherwise offers to search
    something else.
    """
    # Also count as frustration for escalation tiers (Run 24 eval fix)
    frust_count = existing.get("_frustration_count", 0) + 1
    existing["_frustration_count"] = frust_count

    # When frustration has accumulated, use tiered escalation
    if frust_count >= 3:
        existing["_last_action"] = "frustration"
        save_session_slots(session_id, existing)
        result = _empty_reply(
            session_id,
            "I'm sorry I haven't been able to help. Let me connect you "
            "with a peer navigator — they can work with you directly.",
            existing,
            quick_replies=[
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
            ],
        )
        _log_turn(session_id, redacted_message, result, "frustration_tier3",
                  request_id=request_id, tone=tone)
        return result
    elif frust_count >= 2:
        existing["_last_action"] = "frustration"
        save_session_slots(session_id, existing)
        result = _empty_reply(
            session_id,
            "I hear you — I'm clearly not finding what you need right now. "
            "A peer navigator would be more helpful — they're real people "
            "who know the system. You can also call 311 for live help.",
            existing,
            quick_replies=[
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
                {"label": "🔄 Start over", "value": "Start over"},
            ],
        )
        _log_turn(session_id, redacted_message, result, "frustration_tier2",
                  request_id=request_id, tone=tone)
        return result

    existing["_last_action"] = "negative_preference"
    save_session_slots(session_id, existing)
    result = _empty_reply(
        session_id,
        "I understand — those options aren't what you need. "
        "I can search for a different type of service, or connect "
        "you with a peer navigator who might know of other resources. "
        "What would be most helpful?",
        existing,
        quick_replies=list(_WELCOME_QUICK_REPLIES) + [
            {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
        ],
    )
    _log_turn(session_id, redacted_message, result, "negative_preference",
              request_id=request_id, tone=tone)
    return result


def _handle_context_aware_confirm(
    session_id, message, redacted_message, existing,
    category, last_action, tone, request_id,
):
    """Handle yes/no after escalation, emotional, crisis, confused, frustration.

    Returns a result dict, or None if no context-aware handling applies.
    """
    if last_action == "escalation" and category == "confirm_yes":
        existing.pop("_last_action", None)
        save_session_slots(session_id, existing)
        result = _empty_reply(
            session_id,
            "I've shared the contact info above — reach out when you're "
            "ready. Is there anything else I can help with in the meantime?",
            existing,
            quick_replies=[
                {"label": "🔍 Search for services", "value": "Start over"},
                {"label": "👤 Show contact info again", "value": "Connect with peer navigator"},
            ],
        )
        _log_turn(session_id, redacted_message, result, "escalation", request_id=request_id, tone=tone)
        return result

    if last_action == "emotional" and category == "confirm_yes":
        existing.pop("_last_action", None)
        save_session_slots(session_id, existing)
        result = _empty_reply(
            session_id,
            _ESCALATION_RESPONSE,
            existing,
            quick_replies=[
                {"label": "🔍 Search for services", "value": "Start over"},
            ],
        )
        _log_turn(session_id, redacted_message, result, "escalation", request_id=request_id, tone=tone)
        return result

    if last_action == "crisis" and category == "confirm_yes":
        existing.pop("_last_action", None)
        save_session_slots(session_id, existing)
        _crisis_geo_ready = (
            existing.get("location") == NEAR_ME_SENTINEL
            and existing.get("_latitude") is not None
            and existing.get("_longitude") is not None
        )
        if is_enough_to_answer(existing) or _crisis_geo_ready:
            result = _execute_and_respond(session_id, message, existing, request_id=request_id)
        else:
            follow_up = next_follow_up_question(existing)
            result = _empty_reply(
                session_id, follow_up, existing,
                quick_replies=_follow_up_quick_replies(existing),
            )
            result["follow_up_needed"] = True
        _log_turn(session_id, redacted_message, result, "service", request_id=request_id, tone=tone)
        return result

    if last_action == "confused" and category == "confirm_yes":
        existing.pop("_last_action", None)
        save_session_slots(session_id, existing)
        result = _empty_reply(
            session_id, _ESCALATION_RESPONSE, existing,
            quick_replies=[
                {"label": "🔍 New search", "value": "Start over"},
                {"label": "👤 Talk to a person", "value": "Connect with person"},
            ],
        )
        _log_turn(session_id, redacted_message, result, "escalation", request_id=request_id, tone=tone)
        return result

    if last_action == "frustration" and category == "confirm_yes":
        existing.pop("_last_action", None)
        save_session_slots(session_id, existing)
        result = _empty_reply(
            session_id, _ESCALATION_RESPONSE, existing,
            quick_replies=[
                {"label": "🔍 New search", "value": "Start over"},
                {"label": "👤 Talk to a person", "value": "Connect with person"},
            ],
        )
        _log_turn(session_id, redacted_message, result, "escalation", request_id=request_id, tone=tone)
        return result

    # Deny handlers for each context
    _deny_contexts = {
        "escalation": (
            "No problem — I'm here if you change your mind. "
            "Is there anything else I can help you with?"
        ),
        "emotional": (
            "That's okay. I'm here whenever you're ready. "
            "If there's anything practical I can help you find, just let me know."
        ),
        "frustrated": (
            "No worries. If you'd like to try something else or talk to a "
            "real person, just let me know."
        ),
        "frustration": (
            "No worries. If you'd like to try something else or talk to a "
            "real person, just let me know."
        ),
        "confused": (
            "That's okay — no rush. I'm here when you're ready. "
            "You can also talk to a real person if that would help."
        ),
        "crisis": (
            "That's okay. The resources above are available anytime. "
            "If you'd like to search for services later, I'm here."
        ),
    }
    if category == "confirm_deny" and last_action in _deny_contexts:
        existing.pop("_last_action", None)
        existing.pop("_pending_confirmation", None)
        save_session_slots(session_id, existing)
        qr = [{"label": "🤝 Peer navigator", "value": "Connect with peer navigator"}]
        if last_action == "crisis":
            qr.append({"label": "🔍 Search for services", "value": "I need help"})
        result = _empty_reply(
            session_id, _deny_contexts[last_action], existing,
            quick_replies=qr,
        )
        _log_turn(session_id, redacted_message, result, "general", request_id=request_id, tone=tone)
        return result

    return None


def _handle_pending_confirmation(
    session_id, message, redacted_message, existing, pending,
    category, tone, request_id, early_extracted=None,
):
    """Handle confirm_yes, confirm_change_*, confirm_deny during pending confirmation.

    Returns a result dict, or None if no pending handling applies.
    """
    if not pending:
        # Queue offer active (post-results "You also mentioned X — search too?"):
        # handle yes and no distinctly. Without this, yes-to-queue falls
        # through to default handlers which see stale primary slots still
        # in session and rebuild a primary confirmation. See R34
        # Diagnosis 2, Bug 3.
        queue_offer_active = existing.get("_queued_services") or existing.get("_queue_offer_pending")
        if category == "confirm_deny" and queue_offer_active:
            existing.pop("_queued_services", None)
            existing.pop("_queue_offer_pending", None)
            existing.pop("_queued_offer", None)
            existing.pop("_queued_location", None)
            existing.pop("_queued_services_original", None)
            save_session_slots(session_id, existing)
            result = _empty_reply(
                session_id,
                "No problem! Let me know if you need anything else.",
                existing,
                quick_replies=list(_WELCOME_QUICK_REPLIES),
            )
            _log_turn(session_id, redacted_message, result, "queue_decline", request_id=request_id, tone=tone)
            return result
        if category == "confirm_yes" and queue_offer_active:
            offer = existing.get("_queued_offer")
            if offer:
                next_service, next_detail, next_location = offer
                # Clear the prior results' post-search state — we're
                # starting a fresh search, not paginating/filtering the
                # previous one.
                existing.pop("_last_results", None)
                existing.pop("_displayed_count", None)
                existing.pop("_filtered_results", None)
                existing.pop("_filter_phrase", None)
                # Promote the queued service to primary.
                existing["service_type"] = next_service
                if next_detail:
                    existing["service_detail"] = next_detail
                else:
                    existing.pop("service_detail", None)
                if next_location:
                    existing["location"] = next_location
                # Clear queue state.
                existing.pop("_queue_offer_pending", None)
                existing.pop("_queued_offer", None)
                existing.pop("_queued_location", None)
                # Note: _queued_services may still have remaining items
                # for multi-queued scenarios (user queued 3+ services).
                # Leave it intact — _apply_queue_offer will re-fire after
                # this search completes.
                # Skip re-confirmation: user's "yes" IS the confirmation
                # for the search we just promoted. Go straight to query.
                save_session_slots(session_id, existing)
                result = _execute_and_respond(session_id, message, existing, request_id=request_id)
                _log_turn(session_id, redacted_message, result, "queue_accept", request_id=request_id, tone=tone)
                return result
        return None

    if category == "confirm_yes":
        confirm_extracted = extract_slots(message)
        if (confirm_extracted.get("service_type") is not None
                and confirm_extracted["service_type"] != existing.get("service_type")):
            logger.info(
                f"Service type changed in confirmation: "
                f"'{existing.get('service_type')}' → '{confirm_extracted['service_type']}'"
            )
            existing = merge_slots(existing, confirm_extracted)
        existing.pop("_pending_confirmation", None)
        save_session_slots(session_id, existing)
        result = _execute_and_respond(session_id, message, existing, request_id=request_id)
        _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
        return result

    if category == "confirm_change_service":
        existing.pop("_pending_confirmation", None)
        existing["service_type"] = None
        existing.pop("service_detail", None)
        save_session_slots(session_id, existing)
        result = _empty_reply(
            session_id,
            "No problem! What kind of help do you need?",
            existing,
            quick_replies=list(_WELCOME_QUICK_REPLIES),
        )
        _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
        return result

    if category == "confirm_change_location":
        existing.pop("_pending_confirmation", None)
        # If the user's message contains a new location (e.g., "change
        # to Brooklyn" or "I already said Manhattan"), use it directly
        # instead of wiping and re-asking.
        new_loc = (early_extracted or {}).get("location")
        if new_loc:
            existing["location"] = new_loc
            save_session_slots(session_id, existing)
            if is_enough_to_answer(existing):
                existing["_pending_confirmation"] = True
                save_session_slots(session_id, existing)
                confirm_msg = _build_confirmation_message(existing)
                result = {
                    "session_id": session_id,
                    "response": confirm_msg,
                    "follow_up_needed": True,
                    "slots": existing,
                    "services": [],
                    "result_count": 0,
                    "relaxed_search": False,
                    "quick_replies": _confirmation_quick_replies(existing),
                }
                _log_turn(session_id, redacted_message, result, "confirmation",
                          request_id=request_id, tone=tone)
                return result
        # No location in message — ask for one
        existing["location"] = None
        save_session_slots(session_id, existing)
        result = _empty_reply(
            session_id,
            "Sure! What neighborhood or borough should I search in?",
            existing,
            quick_replies=[
                {"label": "📍 Use my location", "value": "__use_geolocation__"},
                {"label": "Manhattan", "value": "Manhattan"},
                {"label": "Brooklyn", "value": "Brooklyn"},
                {"label": "Queens", "value": "Queens"},
                {"label": "Bronx", "value": "Bronx"},
                {"label": "Staten Island", "value": "Staten Island"},
            ],
        )
        _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
        return result

    if category == "confirm_deny":
        # Fix 1: Check if the denial also contains a new service intent.
        # "I changed my mind, shelter" should switch to shelter, not deny.
        deny_extracted = extract_slots(message)
        new_service = deny_extracted.get("service_type")
        if new_service and new_service != existing.get("service_type"):
            logger.info(
                f"[{session_id}] confirm_deny + new service: "
                f"'{existing.get('service_type')}' → '{new_service}'"
            )
            existing = merge_slots(existing, deny_extracted)
            existing.pop("_pending_confirmation", None)
            save_session_slots(session_id, existing)
            new_label = _SERVICE_LABELS.get(new_service, new_service)
            confirm_msg = f"Got it — switching to {new_label}. " + _build_confirmation_message(existing)
            result = {
                "session_id": session_id,
                "response": confirm_msg,
                "follow_up_needed": True,
                "slots": existing,
                "services": [],
                "result_count": 0,
                "relaxed_search": False,
                "quick_replies": _confirmation_quick_replies(existing),
            }
            _log_turn(session_id, redacted_message, result, "service_switch", request_id=request_id, tone=tone)
            return result

        existing.pop("_pending_confirmation", None)
        save_session_slots(session_id, existing)
        result = _empty_reply(
            session_id,
            "No problem! I'll hold onto your info in case you want to "
            "come back to it. What would you like to do?",
            existing,
            quick_replies=[
                {"label": "🔄 Change service", "value": "Change service"},
                {"label": "📍 Change location", "value": "Change location"},
                {"label": "🔍 New search", "value": "Start over"},
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
            ],
        )
        _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
        return result

    return None


def _handle_post_pending_confirmation(session_id, message, redacted_message, existing,
                                      response_tone, tone, request_id):
    """Handle messages that arrive while a confirmation was pending, after
    ``_handle_pending_confirmation`` has already had a chance to match a
    direct yes/no/change.

    Three paths:

    1. **Contradiction** — a slot CHANGED (old → new value). The user is
       correcting their search; auto-execute with an acknowledgment prefix.
    2. **Slot fill** — a slot FILLED (None → new value). The user already
       confirmed and is just providing the missing piece; auto-execute
       without re-confirmation.
    3. **No new info** — re-nudge with a tone-matched confirmation prompt.

    Returns a result dict in all three cases.
    """
    existing.pop("_pending_confirmation", None)
    if _USE_LLM:
        # Feature flag for Phase 2 of the llm_slot_extractor migration.
        # When USE_UNIFIED_EXTRACTOR=1, route through
        # `slot_extraction.extract()`. The new extractor requires a
        # regex_result parameter, so we run regex here first
        # (cheap — the caller's `_run_early_extraction` isn't in
        # scope at this post-pending path).
        if _USE_UNIFIED_EXTRACTOR:
            from app.services.slot_extraction import extract as extract_unified
            regex_result = extract_slots(message)
            pending_extracted = extract_unified(
                message,
                regex_result,
                conversation_history=existing.get("transcript", []),
                api_key_available=True,  # gated by _USE_LLM above
            )
        else:
            from app.services.llm_slot_extractor import extract_slots_smart
            pending_extracted = extract_slots_smart(
                message,
                conversation_history=existing.get("transcript", []),
            )
    else:
        pending_extracted = extract_slots(message)
    # no_requirements is always present as False when not set — exclude it
    # so the default value doesn't falsely trigger the pending_has_new branch.
    pending_has_new = any(
        v is not None and v != [] and v is not False
        for k, v in pending_extracted.items()
        if k not in ("additional_services", "_populations", "_contradiction", "_is_additive", "no_requirements")
    )

    # Path 1+2: something changed or filled
    if pending_has_new:
        _SLOT_KEYS = ("service_type", "location", "age", "_gender", "family_status")
        changed = {
            k: pending_extracted[k]
            for k in _SLOT_KEYS
            if pending_extracted.get(k) is not None
            and existing.get(k) is not None
            and pending_extracted[k] != existing[k]
        }
        # Additive intent: user is adding a service, not changing it.
        # Queue the new service and re-confirm the current search.
        _is_additive = pending_extracted.get("_is_additive", False)
        if (changed
                and _is_additive
                and "service_type" in changed):
            merged_pending = merge_slots(existing, pending_extracted)
            merged_pending["_pending_confirmation"] = True
            save_session_slots(session_id, merged_pending)
            svc_label = _SERVICE_LABELS.get(
                existing.get("service_type", ""), existing.get("service_type", "services"))
            new_label = _SERVICE_LABELS.get(changed["service_type"], changed["service_type"])
            result = _empty_reply(
                session_id,
                f"Got it — I've noted {new_label} for after. "
                f"Let me finish searching for {svc_label} first. Sound good?",
                merged_pending,
                quick_replies=_confirmation_quick_replies(merged_pending),
            )
            _log_turn(session_id, redacted_message, result, "additive_service",
                      request_id=request_id, tone=tone)
            return result

        if changed:
            # Path 1: contradiction detected
            merged_pending = merge_slots(existing, pending_extracted)
            if is_enough_to_answer(merged_pending):
                logger.info(
                    f"[{session_id}] Contradiction during confirmation: "
                    f"{changed} — auto-executing"
                )
                save_session_slots(session_id, merged_pending)
                result = _execute_and_respond(
                    session_id, message, merged_pending, request_id=request_id
                )
                # Prepend acknowledgment of the change
                changes = []
                if "service_type" in changed:
                    changes.append(
                        _SERVICE_LABELS.get(changed["service_type"], changed["service_type"])
                    )
                if "location" in changed:
                    changes.append(changed["location"])
                prefix = f"Got it — switching to {', '.join(changes)}. " if changes else ""
                result["response"] = prefix + result["response"]
                _log_turn(
                    session_id, redacted_message, result,
                    "contradiction_auto_execute", request_id=request_id, tone=tone,
                )
                return result

        # Path 2: slot fill after user already confirmed. The contradiction
        # check above fires only on VALUE CHANGES (old → new). This handles
        # FILLS (None → new) — e.g. crisis step-down "Yes, search" → location
        # follow-up → user provides location. They already said yes; they're
        # just providing the missing piece, not requesting a new search.
        #
        # RESTRICTED to REQUIRED slot fills (service_type or location). Optional
        # demographic slots (age, _gender, family_status) falling alone during
        # pending_confirmation should re-nudge, NOT auto-execute, because the
        # user never said yes — they just added a qualifier to a "sound good?"
        # prompt that is still outstanding. The contradiction-auto-execute
        # regression (test_new_slot_no_contradiction_reconfirms) covers this.
        required_fill = (
            pending_extracted.get("service_type") is not None
            or pending_extracted.get("location") is not None
        )
        if not changed and required_fill:
            merged_pending = merge_slots(existing, pending_extracted)
            geolocation_fill = (
                merged_pending.get("location") == NEAR_ME_SENTINEL
                and merged_pending.get("_latitude") is not None
                and merged_pending.get("_longitude") is not None
            )
            if is_enough_to_answer(merged_pending) or geolocation_fill:
                logger.info(
                    f"[{session_id}] Slot fill after confirmation — auto-executing"
                )
                save_session_slots(session_id, merged_pending)
                result = _execute_and_respond(
                    session_id, message, merged_pending, request_id=request_id
                )
                _log_turn(
                    session_id, redacted_message, result,
                    "fill_auto_execute", request_id=request_id, tone=tone,
                )
                return result

    # Path 3: nothing new — re-nudge. Restore the pending flag so the
    # next message is interpreted as a confirmation response. Merge any
    # pending_extracted slots (typically optional demographics like age,
    # family_status, _gender) into existing first so the info isn't lost
    # — it'll be used when the user eventually confirms.
    if pending_has_new:
        existing = merge_slots(existing, pending_extracted)
    existing["_pending_confirmation"] = True
    save_session_slots(session_id, existing)

    # C.2 (April 2026) — topic-shift disambiguation. If the user sent
    # something that looks like an off-topic question (e.g. "what's your
    # name?", "can you speak Spanish?") while a confirmation was pending,
    # replying with a blind re-nudge of the prior search reads as a
    # surreal non-sequitur. Most such questions are caught earlier by
    # bot_identity / bot_question / help classifiers; this branch
    # catches novel phrasings that slipped past those lists. We preserve
    # _pending_confirmation so "yes" still works, but offer the user a
    # clear choice rather than silently assuming they want the old
    # search. See docs/CHATBOT_BEHAVIOR.md § Confirmation Actions.
    if _looks_like_topic_shift_question(message):
        confirm_msg = (
            "I wasn't sure if that was a question about something else, "
            "or if you were still thinking about the search. "
            + _build_confirmation_message(existing)
            + " — should I go ahead with that, or were you asking "
            "something different?"
        )
        qr = _confirmation_quick_replies(existing)
        # Augment with an explicit "something else" escape so the user
        # doesn't have to type their off-topic question twice.
        qr = list(qr) + [
            {"label": "💬 I was asking something else",
             "value": "I was asking something else"},
        ]
        result = {
            "session_id": session_id,
            "response": confirm_msg,
            "follow_up_needed": True,
            "slots": existing,
            "services": [],
            "result_count": 0,
            "relaxed_search": False,
            "quick_replies": qr,
        }
        _log_turn(session_id, redacted_message, result,
                  "topic_shift_disambiguation",
                  request_id=request_id, tone=tone)
        return result

    nudge_prefix = "Just to make sure — "
    if response_tone == "emotional":
        nudge_prefix = "I hear you. Just to make sure — "
    elif response_tone == "frustrated":
        nudge_prefix = "I understand. Let me just confirm — "
    elif response_tone == "confused":
        nudge_prefix = "No worries — let me just confirm: "
    elif response_tone == "urgent":
        nudge_prefix = "Got it — just to confirm: "

    confirm_msg = (
        nudge_prefix + _build_confirmation_message(existing)
        + ' Tap "Yes, search" to go, or you can change the details.'
    )
    result = {
        "session_id": session_id,
        "response": confirm_msg,
        "follow_up_needed": True,
        "slots": existing,
        "services": [],
        "result_count": 0,
        "relaxed_search": False,
        "quick_replies": _confirmation_quick_replies(existing),
    }
    _log_turn(session_id, redacted_message, result, "confirmation_nudge",
              request_id=request_id, tone=tone)
    return result
