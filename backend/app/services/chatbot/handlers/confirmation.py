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
from app.services.slot_extraction_regex import (
    NEAR_ME_SENTINEL,
    extract_slots,
    is_enough_to_answer,
    merge_slots,
    next_follow_up_question,
)
from app.services import slot_extraction
from app.utils.text_normalize import normalize_apostrophes

from ..context import _USE_LLM, _empty_reply
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
    # Normalize curly apostrophes from mobile autocorrect before matching
    # against _WH_WORD_STARTERS, which has entries like "what's", "who's",
    # "how's". Without normalization, mobile users typing "what's your
    # name?" with autocorrect would fail topic-shift detection.
    stripped = normalize_apostrophes(message.strip().lower())
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


def _handle_change_location_request(ctx):
    """User asked to change search location ("search somewhere else").

    If the message already contains a new location (e.g. "actually Manhattan"),
    apply it directly — prevents a frustration loop where the bot wipes the
    location and re-asks what the user just said. Otherwise, wipe and ask.
    """
    new_loc = ctx.early_extracted.get("location")
    if new_loc:
        ctx.existing["location"] = new_loc
        save_session_slots(ctx.session_id, ctx.existing)
        if is_enough_to_answer(ctx.existing):
            ctx.existing["_pending_confirmation"] = True
            save_session_slots(ctx.session_id, ctx.existing)
            confirm_msg = _build_confirmation_message(ctx.existing)
            result = {
                "session_id": ctx.session_id,
                "response": confirm_msg,
                "follow_up_needed": True,
                "slots": ctx.existing,
                "services": [],
                "result_count": 0,
                "relaxed_search": False,
                "quick_replies": _confirmation_quick_replies(ctx.existing),
            }
            _log_turn(ctx.session_id, ctx.redacted_message, result, "confirmation",
                      request_id=ctx.request_id, tone=ctx.tone)
            return result
        else:
            follow_up = next_follow_up_question(ctx.existing)
            result = {
                "session_id": ctx.session_id,
                "response": follow_up,
                "follow_up_needed": True,
                "slots": ctx.existing,
                "services": [],
                "result_count": 0,
                "relaxed_search": False,
                "quick_replies": _follow_up_quick_replies(ctx.existing),
            }
            _log_turn(ctx.session_id, ctx.redacted_message, result, "service",
                      request_id=ctx.request_id, tone=ctx.tone)
            return result

    # No location in message — clear and ask for one
    ctx.existing["location"] = None
    save_session_slots(ctx.session_id, ctx.existing)
    result = _empty_reply(
        ctx.session_id,
        "Sure! What neighborhood or borough should I search in?",
        ctx.existing,
        quick_replies=[
            {"label": "📍 Use my location", "value": "__use_geolocation__"},
            {"label": "Manhattan", "value": "Manhattan"},
            {"label": "Brooklyn", "value": "Brooklyn"},
            {"label": "Queens", "value": "Queens"},
            {"label": "Bronx", "value": "Bronx"},
            {"label": "Staten Island", "value": "Staten Island"},
        ],
    )
    _log_turn(ctx.session_id, ctx.redacted_message, result, ctx.category,
              request_id=ctx.request_id, tone=ctx.tone)
    return result


def _handle_change_service_request(ctx):
    """User asked to change the service type — wipe service_type + service_detail
    and show the service menu.

    Also sets `_awaiting_service_after_clear` so the orchestrator can
    trust regex extraction on the user's next message without calling the
    LLM. Context: the transcript only stores user messages (the bot's
    "What kind of help do you need?" prompt is absent), so a subsequent
    bare "Shelter" reply, fed through the LLM with history, can be
    mis-extracted as `{service_type: food (stale from history),
    additional: [shelter]}`. Skipping the LLM in this narrow case —
    user just cleared service, next message regex-extracts a clean
    single service — avoids the mis-extraction entirely. Covers
    `confirm_multi_change`.
    """
    ctx.existing["service_type"] = None
    ctx.existing.pop("service_detail", None)
    ctx.existing["_awaiting_service_after_clear"] = True
    save_session_slots(ctx.session_id, ctx.existing)
    result = _empty_reply(
        ctx.session_id,
        "No problem! What kind of help do you need?",
        ctx.existing,
        quick_replies=list(_WELCOME_QUICK_REPLIES),
    )
    _log_turn(ctx.session_id, ctx.redacted_message, result, ctx.category,
              request_id=ctx.request_id, tone=ctx.tone)
    return result


def _handle_correction(ctx):
    """Acknowledge a user correction ("that's not what I meant") by clearing
    pending state and echoing what we WERE searching for so they can redirect."""
    ctx.existing.pop("_pending_confirmation", None)
    ctx.existing.pop("_last_action", None)
    ctx.existing.pop("_last_results", None)
    save_session_slots(ctx.session_id, ctx.existing)
    service_type = ctx.existing.get("service_type")
    location = ctx.existing.get("location")
    if location == NEAR_ME_SENTINEL:
        location = None
    context = ""
    if service_type and location:
        context = f" I was searching for {service_type} in {location}."
    elif service_type:
        context = f" I was searching for {service_type}."
    result = _empty_reply(
        ctx.session_id,
        f"Sorry about that!{context} Let me know what you need — you can "
        f"pick a service below, tell me in your own words, or connect "
        f"with a peer navigator.",
        ctx.existing,
        quick_replies=list(_WELCOME_QUICK_REPLIES) + [
            {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
        ],
    )
    _log_turn(ctx.session_id, ctx.redacted_message, result, "correction",
              request_id=ctx.request_id, tone=ctx.tone, confidence="low")
    return result


def _handle_negative_preference(ctx):
    """Handle "I don't like those" / "none of these" with tiered escalation.

    After 3+ consecutive frustration-counted turns, routes to peer navigator.
    After 2, adds 311 as a live-help option. Otherwise offers to search
    something else.
    """
    # Also count as frustration for escalation tiers (Run 24 eval fix)
    frust_count = ctx.existing.get("_frustration_count", 0) + 1
    ctx.existing["_frustration_count"] = frust_count

    # When frustration has accumulated, use tiered escalation
    if frust_count >= 3:
        ctx.existing["_last_action"] = "frustration"
        save_session_slots(ctx.session_id, ctx.existing)
        result = _empty_reply(
            ctx.session_id,
            "I'm sorry I haven't been able to help. Let me connect you "
            "with a peer navigator — they can work with you directly.",
            ctx.existing,
            quick_replies=[
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
            ],
        )
        _log_turn(ctx.session_id, ctx.redacted_message, result, "frustration_tier3",
                  request_id=ctx.request_id, tone=ctx.tone)
        return result
    elif frust_count >= 2:
        ctx.existing["_last_action"] = "frustration"
        save_session_slots(ctx.session_id, ctx.existing)
        result = _empty_reply(
            ctx.session_id,
            "I hear you — I'm clearly not finding what you need right now. "
            "A peer navigator would be more helpful — they're real people "
            "who know the system. You can also call 311 for live help.",
            ctx.existing,
            quick_replies=[
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
                {"label": "🔄 Start over", "value": "Start over"},
            ],
        )
        _log_turn(ctx.session_id, ctx.redacted_message, result, "frustration_tier2",
                  request_id=ctx.request_id, tone=ctx.tone)
        return result

    ctx.existing["_last_action"] = "negative_preference"
    save_session_slots(ctx.session_id, ctx.existing)
    result = _empty_reply(
        ctx.session_id,
        "I understand — those options aren't what you need. "
        "I can search for a different type of service, or connect "
        "you with a peer navigator who might know of other resources. "
        "What would be most helpful?",
        ctx.existing,
        quick_replies=list(_WELCOME_QUICK_REPLIES) + [
            {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
        ],
    )
    _log_turn(ctx.session_id, ctx.redacted_message, result, "negative_preference",
              request_id=ctx.request_id, tone=ctx.tone)
    return result


def _handle_context_aware_confirm(ctx):
    """Handle yes/no after escalation, emotional, crisis, confused, frustration.

    Reads ``ctx.snapshot_last_action`` — a snapshot of
    ``ctx.existing.get("_last_action")`` captured by the orchestrator
    BEFORE this handler ran, because this handler pops ``_last_action``
    from ``existing`` on confirm_yes paths. The orchestrator's subsequent
    ``_consume_last_action`` call also reads the snapshot, so both
    callers see the same value. See ``MessageContext.snapshot_last_action``
    for the full contract.

    Returns a result dict, or None if no context-aware handling applies.
    """
    last_action = ctx.snapshot_last_action
    if last_action == "escalation" and ctx.category == "confirm_yes":
        ctx.existing.pop("_last_action", None)
        save_session_slots(ctx.session_id, ctx.existing)
        result = _empty_reply(
            ctx.session_id,
            "I've shared the contact info above — reach out when you're "
            "ready. Is there anything else I can help with in the meantime?",
            ctx.existing,
            quick_replies=[
                {"label": "🔍 Search for services", "value": "Start over"},
                {"label": "👤 Show contact info again", "value": "Connect with peer navigator"},
            ],
        )
        _log_turn(ctx.session_id, ctx.redacted_message, result, "escalation",
                  request_id=ctx.request_id, tone=ctx.tone)
        return result

    if last_action == "emotional" and ctx.category == "confirm_yes":
        ctx.existing.pop("_last_action", None)
        save_session_slots(ctx.session_id, ctx.existing)
        result = _empty_reply(
            ctx.session_id,
            _ESCALATION_RESPONSE,
            ctx.existing,
            quick_replies=[
                {"label": "🔍 Search for services", "value": "Start over"},
            ],
        )
        _log_turn(ctx.session_id, ctx.redacted_message, result, "escalation",
                  request_id=ctx.request_id, tone=ctx.tone)
        return result

    if last_action == "crisis" and ctx.category == "confirm_yes":
        ctx.existing.pop("_last_action", None)
        save_session_slots(ctx.session_id, ctx.existing)
        _crisis_geo_ready = (
            ctx.existing.get("location") == NEAR_ME_SENTINEL
            and ctx.existing.get("_latitude") is not None
            and ctx.existing.get("_longitude") is not None
        )
        if is_enough_to_answer(ctx.existing) or _crisis_geo_ready:
            result = _execute_and_respond(ctx.session_id, ctx.message, ctx.existing,
                                          request_id=ctx.request_id)
        else:
            follow_up = next_follow_up_question(ctx.existing)
            result = _empty_reply(
                ctx.session_id, follow_up, ctx.existing,
                quick_replies=_follow_up_quick_replies(ctx.existing),
            )
            result["follow_up_needed"] = True
        _log_turn(ctx.session_id, ctx.redacted_message, result, "service",
                  request_id=ctx.request_id, tone=ctx.tone)
        return result

    if last_action == "confused" and ctx.category == "confirm_yes":
        ctx.existing.pop("_last_action", None)
        save_session_slots(ctx.session_id, ctx.existing)
        result = _empty_reply(
            ctx.session_id, _ESCALATION_RESPONSE, ctx.existing,
            quick_replies=[
                {"label": "🔍 New search", "value": "Start over"},
                {"label": "👤 Talk to a person", "value": "Connect with person"},
            ],
        )
        _log_turn(ctx.session_id, ctx.redacted_message, result, "escalation",
                  request_id=ctx.request_id, tone=ctx.tone)
        return result

    if last_action == "frustration" and ctx.category == "confirm_yes":
        ctx.existing.pop("_last_action", None)
        save_session_slots(ctx.session_id, ctx.existing)
        result = _empty_reply(
            ctx.session_id, _ESCALATION_RESPONSE, ctx.existing,
            quick_replies=[
                {"label": "🔍 New search", "value": "Start over"},
                {"label": "👤 Talk to a person", "value": "Connect with person"},
            ],
        )
        _log_turn(ctx.session_id, ctx.redacted_message, result, "escalation",
                  request_id=ctx.request_id, tone=ctx.tone)
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
    if ctx.category == "confirm_deny" and last_action in _deny_contexts:
        ctx.existing.pop("_last_action", None)
        ctx.existing.pop("_pending_confirmation", None)
        save_session_slots(ctx.session_id, ctx.existing)
        qr = [{"label": "🤝 Peer navigator", "value": "Connect with peer navigator"}]
        if last_action == "crisis":
            qr.append({"label": "🔍 Search for services", "value": "I need help"})
        result = _empty_reply(
            ctx.session_id, _deny_contexts[last_action], ctx.existing,
            quick_replies=qr,
        )
        _log_turn(ctx.session_id, ctx.redacted_message, result, "general",
                  request_id=ctx.request_id, tone=ctx.tone)
        return result

    return None


def _promote_queued_offer(ctx, offer, location_override=None):
    """Promote a queued service offer to the primary slot and execute the search.

    Shared between the two "user accepts the queued offer" paths:

      1. `confirm_yes` on a queue offer ("yes" / "yes please" / button).
      2. A service-category message matching the queued service
         ("I need shelter", or the quick-reply value "I need shelter in
         Brooklyn" when the user taps the queue-offer button).

    Both cases go straight to the search — the user's explicit
    acceptance is the confirmation.

    Args:
        offer: The (service, detail, location) tuple from
            ``ctx.existing["_queued_offer"]``.
        location_override: If set, use this instead of ``offer[2]``.
            Lets the service-match path honor a location the user just
            typed ("I need shelter in Manhattan" when the offer was
            for Brooklyn). ``confirm_yes`` callers pass None (the yes
            message carries no new location signal).
    """
    next_service, next_detail, next_location = offer
    location = location_override or next_location

    # Clear prior results' post-search state — fresh search, not
    # paginating/filtering the previous one.
    ctx.existing.pop("_last_results", None)
    ctx.existing.pop("_displayed_count", None)
    ctx.existing.pop("_filtered_results", None)
    ctx.existing.pop("_filter_phrase", None)

    # Promote the queued service to primary.
    ctx.existing["service_type"] = next_service
    if next_detail:
        ctx.existing["service_detail"] = next_detail
    else:
        ctx.existing.pop("service_detail", None)
    if location:
        ctx.existing["location"] = location

    # Clear queue state for this offer. _queued_services may still
    # hold remaining items for 3+ queue scenarios — leave it for
    # _apply_queue_offer to re-fire after the search completes.
    ctx.existing.pop("_queue_offer_pending", None)
    ctx.existing.pop("_queued_offer", None)
    ctx.existing.pop("_queued_location", None)

    save_session_slots(ctx.session_id, ctx.existing)
    result = _execute_and_respond(ctx.session_id, ctx.message, ctx.existing,
                                  request_id=ctx.request_id)
    _log_turn(ctx.session_id, ctx.redacted_message, result, "queue_accept",
              request_id=ctx.request_id, tone=ctx.tone)
    return result


def _handle_pending_confirmation(ctx):
    """Handle confirm_yes, confirm_change_*, confirm_deny during pending confirmation.

    Reads ``ctx.snapshot_pending`` — a snapshot of
    ``ctx.existing.get("_pending_confirmation")`` captured by the
    orchestrator BEFORE this handler ran, because this handler pops
    ``_pending_confirmation`` from ``existing`` on confirm paths. The
    orchestrator's subsequent ``if pending:`` guard before
    ``_handle_post_pending_confirmation`` must see the same value this
    handler dispatched on. See ``MessageContext.snapshot_pending`` for
    the full contract.

    Returns a result dict, or None if no pending handling applies.
    """
    pending = ctx.snapshot_pending
    # Local alias — handler may rebind via ``merge_slots`` below, which
    # returns a new dict. The orchestrator's ``ctx.existing`` reference
    # stays pointing at the original; subsequent reads of ``ctx.existing``
    # in the orchestrator are safe because the orchestrator returns
    # immediately when this handler returns non-None (the only branches
    # that rebind also return).
    existing = ctx.existing

    if not pending:
        # Queue offer active (post-results "You also mentioned X — search too?"):
        # handle yes and no distinctly. Without this, yes-to-queue falls
        # through to default handlers which see stale primary slots still
        # in session and rebuild a primary confirmation. See R34
        # Diagnosis 2, Bug 3.
        #
        # Service-match queue-accept ("I need food" when food is queued)
        # is NOT handled here because `_handle_post_results_interaction`
        # fires earlier in the orchestrator and wipes the queue state
        # when it sees `has_service_intent`. That higher-priority path
        # is handled directly in `orchestrator.generate_reply` via an
        # early queue-accept check, using `_promote_queued_offer` below.
        queue_offer_active = existing.get("_queued_services") or existing.get("_queue_offer_pending")
        if ctx.category == "confirm_deny" and queue_offer_active:
            existing.pop("_queued_services", None)
            existing.pop("_queue_offer_pending", None)
            existing.pop("_queued_offer", None)
            existing.pop("_queued_location", None)
            existing.pop("_queued_services_original", None)
            save_session_slots(ctx.session_id, existing)
            result = _empty_reply(
                ctx.session_id,
                "No problem! Let me know if you need anything else.",
                existing,
                quick_replies=list(_WELCOME_QUICK_REPLIES),
            )
            _log_turn(ctx.session_id, ctx.redacted_message, result, "queue_decline",
                      request_id=ctx.request_id, tone=ctx.tone)
            return result

        if ctx.category == "confirm_yes" and queue_offer_active:
            offer = existing.get("_queued_offer")
            if offer:
                return _promote_queued_offer(ctx, offer)
        return None

    if ctx.category == "confirm_yes":
        confirm_extracted = extract_slots(ctx.message)
        if (confirm_extracted.get("service_type") is not None
                and confirm_extracted["service_type"] != existing.get("service_type")):
            logger.info(
                f"Service type changed in confirmation: "
                f"'{existing.get('service_type')}' → '{confirm_extracted['service_type']}'"
            )
            existing = merge_slots(existing, confirm_extracted)
        existing.pop("_pending_confirmation", None)
        save_session_slots(ctx.session_id, existing)
        result = _execute_and_respond(ctx.session_id, ctx.message, existing,
                                      request_id=ctx.request_id)
        _log_turn(ctx.session_id, ctx.redacted_message, result, ctx.category,
                  request_id=ctx.request_id, tone=ctx.tone)
        return result

    if ctx.category == "confirm_change_service":
        existing.pop("_pending_confirmation", None)
        existing["service_type"] = None
        existing.pop("service_detail", None)
        # Set the "next user message is picking a new service" flag so
        # the orchestrator's service-extraction stage will trust the
        # regex for a single-service reply and skip the LLM.
        #
        # Rationale (same as _handle_change_service_request): the
        # transcript only stores user messages, so the bot's "What
        # kind of help do you need?" prompt is absent from the
        # conversation history the LLM sees. A bare "Shelter" reply
        # next turn, run through the LLM with history, can be
        # mis-extracted as `{service_type: food (stale), additional:
        # [shelter]}` — food gets bundled back in and the slot change
        # doesn't stick. The awaiting-clear guard in
        # `orchestrator.py:438–446` uses this flag to short-circuit
        # to regex-only when regex returns a clean single service.
        #
        # Without this line the guard never fires for the pending-
        # confirmation change-service path (which is the common path
        # — standalone `_handle_change_service_request` only runs when
        # there's no pending confirmation). This was the bug behind
        # `confirm_multi_change`'s 3.55 score: turn 2 ("Change service")
        # hit this branch, turn 3 ("Shelter") ran the LLM with stale
        # food-in-history, and the mis-extraction persisted through
        # to the final confirmation — "food and shelter" instead of
        # just "shelter".
        existing["_awaiting_service_after_clear"] = True
        save_session_slots(ctx.session_id, existing)
        result = _empty_reply(
            ctx.session_id,
            "No problem! What kind of help do you need?",
            existing,
            quick_replies=list(_WELCOME_QUICK_REPLIES),
        )
        _log_turn(ctx.session_id, ctx.redacted_message, result, ctx.category,
                  request_id=ctx.request_id, tone=ctx.tone)
        return result

    if ctx.category == "confirm_change_location":
        existing.pop("_pending_confirmation", None)
        # If the user's message contains a new location (e.g., "change
        # to Brooklyn" or "I already said Manhattan"), use it directly
        # instead of wiping and re-asking.
        new_loc = (ctx.early_extracted or {}).get("location")
        if new_loc:
            existing["location"] = new_loc
            save_session_slots(ctx.session_id, existing)
            if is_enough_to_answer(existing):
                existing["_pending_confirmation"] = True
                save_session_slots(ctx.session_id, existing)
                confirm_msg = _build_confirmation_message(existing)
                result = {
                    "session_id": ctx.session_id,
                    "response": confirm_msg,
                    "follow_up_needed": True,
                    "slots": existing,
                    "services": [],
                    "result_count": 0,
                    "relaxed_search": False,
                    "quick_replies": _confirmation_quick_replies(existing),
                }
                _log_turn(ctx.session_id, ctx.redacted_message, result, "confirmation",
                          request_id=ctx.request_id, tone=ctx.tone)
                return result
        # No location in message — ask for one
        existing["location"] = None
        save_session_slots(ctx.session_id, existing)
        result = _empty_reply(
            ctx.session_id,
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
        _log_turn(ctx.session_id, ctx.redacted_message, result, ctx.category,
                  request_id=ctx.request_id, tone=ctx.tone)
        return result

    if ctx.category == "confirm_deny":
        # Fix 1: Check if the denial also contains a new service intent.
        # "I changed my mind, shelter" should switch to shelter, not deny.
        deny_extracted = extract_slots(ctx.message)
        new_service = deny_extracted.get("service_type")
        if new_service and new_service != existing.get("service_type"):
            logger.info(
                f"[{ctx.session_id}] confirm_deny + new service: "
                f"'{existing.get('service_type')}' → '{new_service}'"
            )
            existing = merge_slots(existing, deny_extracted)
            existing.pop("_pending_confirmation", None)
            save_session_slots(ctx.session_id, existing)
            new_label = _SERVICE_LABELS.get(new_service, new_service)
            confirm_msg = f"Got it — switching to {new_label}. " + _build_confirmation_message(existing)
            result = {
                "session_id": ctx.session_id,
                "response": confirm_msg,
                "follow_up_needed": True,
                "slots": existing,
                "services": [],
                "result_count": 0,
                "relaxed_search": False,
                "quick_replies": _confirmation_quick_replies(existing),
            }
            _log_turn(ctx.session_id, ctx.redacted_message, result, "service_switch",
                      request_id=ctx.request_id, tone=ctx.tone)
            return result

        existing.pop("_pending_confirmation", None)
        save_session_slots(ctx.session_id, existing)
        result = _empty_reply(
            ctx.session_id,
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
        _log_turn(ctx.session_id, ctx.redacted_message, result, ctx.category,
                  request_id=ctx.request_id, tone=ctx.tone)
        return result

    return None


def _handle_post_pending_confirmation(ctx):
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

    Reads ``ctx.snapshot_response_tone`` — a snapshot of the response tone
    captured by the orchestrator BEFORE any negative_preference → service
    promotion. Used at the bottom of the function to pick a tone-matched
    nudge prefix. Distinct from ``ctx.tone`` because the orchestrator may
    have promoted a None tone to "frustrated" along the negative_preference
    path; the snapshot preserves the pre-promotion value so the nudge
    prefix reflects what the user originally said. See
    ``MessageContext.snapshot_response_tone`` for the full contract.

    Returns a result dict in all three cases.
    """
    response_tone = ctx.snapshot_response_tone
    # Local alias — handler may rebind via ``merge_slots`` below. See
    # the analogous note in ``_handle_pending_confirmation``.
    existing = ctx.existing

    existing.pop("_pending_confirmation", None)
    if ctx.unified_extraction is not None:
        # LLM-2: the gate (in pipeline._run_llm_gate) already ran the
        # unified extractor on this exact message and cached the result
        # on ctx. Reuse it instead of firing the same LLM call again.
        # See PHASE_AC_AFTERMATH.md `LLM-2`.
        pending_extracted = ctx.unified_extraction
    elif _USE_LLM:
        # Gate didn't fire (regex caught service_type, message too
        # short, action in _SKIP_UNIFIED_ACTIONS, etc.) — but the
        # unified extractor still adds value via post-regex slot
        # enrichment, so call it once here.
        #
        # Pass ``ctx.early_extracted`` as the regex_result instead of
        # rerunning ``extract_slots`` on the same message — the
        # orchestrator's ``_run_early_extraction`` already computed it
        # (regex + semantic router) and stashed it on ctx. Forward
        # ``ctx.extraction_source`` so Trust Model 3 can give the
        # semantic router priority on disagreement (the same kwarg the
        # main service-flow call passes).
        pending_extracted = slot_extraction.extract(
            ctx.message,
            ctx.early_extracted,
            conversation_history=existing.get("transcript", []),
            api_key_available=True,  # gated by _USE_LLM above
            extraction_source=ctx.extraction_source or "regex",
        )
    else:
        # No LLM available — ``ctx.early_extracted`` is the same regex
        # result the prior code path computed via ``extract_slots``,
        # plus semantic-router enrichment when applicable.
        pending_extracted = ctx.early_extracted
    # no_requirements is always present as False when not set — exclude it
    # so the default value doesn't falsely trigger the pending_has_new branch.
    pending_has_new = any(
        v is not None and v != [] and v is not False
        for k, v in pending_extracted.items()
        if k not in ("additional_services", "_populations", "_contradiction", "_is_additive", "no_requirements", "tone", "action")
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
            save_session_slots(ctx.session_id, merged_pending)
            svc_label = _SERVICE_LABELS.get(
                existing.get("service_type", ""), existing.get("service_type", "services"))
            new_label = _SERVICE_LABELS.get(changed["service_type"], changed["service_type"])
            result = _empty_reply(
                ctx.session_id,
                f"Got it — I've noted {new_label} for after. "
                f"Let me finish searching for {svc_label} first. Sound good?",
                merged_pending,
                quick_replies=_confirmation_quick_replies(merged_pending),
            )
            _log_turn(ctx.session_id, ctx.redacted_message, result, "additive_service",
                      request_id=ctx.request_id, tone=ctx.tone)
            return result

        if changed:
            # Path 1: contradiction detected
            merged_pending = merge_slots(existing, pending_extracted)
            if is_enough_to_answer(merged_pending):
                logger.info(
                    f"[{ctx.session_id}] Contradiction during confirmation: "
                    f"{changed} — auto-executing"
                )
                save_session_slots(ctx.session_id, merged_pending)
                result = _execute_and_respond(
                    ctx.session_id, ctx.message, merged_pending, request_id=ctx.request_id
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
                    ctx.session_id, ctx.redacted_message, result,
                    "contradiction_auto_execute", request_id=ctx.request_id, tone=ctx.tone,
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
                    f"[{ctx.session_id}] Slot fill after confirmation — auto-executing"
                )
                save_session_slots(ctx.session_id, merged_pending)
                result = _execute_and_respond(
                    ctx.session_id, ctx.message, merged_pending, request_id=ctx.request_id
                )
                _log_turn(
                    ctx.session_id, ctx.redacted_message, result,
                    "fill_auto_execute", request_id=ctx.request_id, tone=ctx.tone,
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
    save_session_slots(ctx.session_id, existing)

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
    if _looks_like_topic_shift_question(ctx.message):
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
            "session_id": ctx.session_id,
            "response": confirm_msg,
            "follow_up_needed": True,
            "slots": existing,
            "services": [],
            "result_count": 0,
            "relaxed_search": False,
            "quick_replies": qr,
        }
        _log_turn(ctx.session_id, ctx.redacted_message, result,
                  "topic_shift_disambiguation",
                  request_id=ctx.request_id, tone=ctx.tone)
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
        "session_id": ctx.session_id,
        "response": confirm_msg,
        "follow_up_needed": True,
        "slots": existing,
        "services": [],
        "result_count": 0,
        "relaxed_search": False,
        "quick_replies": _confirmation_quick_replies(existing),
    }
    _log_turn(ctx.session_id, ctx.redacted_message, result, "confirmation_nudge",
              request_id=ctx.request_id, tone=ctx.tone)
    return result
